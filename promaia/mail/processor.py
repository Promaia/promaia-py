"""
Email Processor - Main orchestrator for email processing pipeline.

Processes new emails through the complete workflow:
1. Sync recent emails from Gmail
2. Classify each thread
3. Build response context
4. Generate draft responses
5. Save to database for review
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from promaia.mail.draft_manager import DraftManager
from promaia.mail.classifier import EmailClassifier
from promaia.mail.context_builder import ResponseContextBuilder
from promaia.mail.response_generator import ResponseGenerator

logger = logging.getLogger(__name__)


class EmailProcessor:
    """Main orchestrator for email processing pipeline."""
    
    def __init__(self, dry_run: bool = False):
        """Initialize email processor.

        Args:
            dry_run: If True, process emails without saving drafts or updating sync state.
                     Read-only operations (fetch, classify, query) still execute normally.
        """
        self.draft_manager = DraftManager()
        self.classifier = EmailClassifier()
        # Lazy-init: only needed by the refresh path, not the agentic processing path
        self._context_builder = None
        self._response_generator = None
        self.dry_run = dry_run

    @property
    def context_builder(self):
        if self._context_builder is None:
            self._context_builder = ResponseContextBuilder()
        return self._context_builder

    @property
    def response_generator(self):
        if self._response_generator is None:
            self._response_generator = ResponseGenerator()
        return self._response_generator
    
    async def process_new_emails(self, workspaces: List[str], hours_back: int = 72) -> int:
        """
        Process new emails for specified workspaces since last sync.

        Args:
            workspaces: List of workspace names
            hours_back: Fallback hours to check if never synced before (default: 72)

        Returns:
            Number of drafts generated
        """
        total_drafts = 0
        sync_start_time = datetime.now(timezone.utc)

        for workspace in workspaces:
            logger.info(f"📧 Processing emails for workspace: {workspace}")

            try:
                # Get last sync time for this workspace
                last_sync = self.draft_manager.get_last_sync_time(workspace)

                if last_sync:
                    logger.info(f"📅 Last sync: {last_sync.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                else:
                    logger.info(f"📅 First sync - checking last {hours_back} hours")
                    last_sync = datetime.now(timezone.utc) - timedelta(hours=hours_back)

                drafts_count = await self._process_workspace(workspace, last_sync)
                total_drafts += drafts_count

                # Update last sync time after successful processing (skip in dry-run)
                if not self.dry_run:
                    self.draft_manager.update_last_sync_time(workspace, sync_start_time)

                logger.info(f"✅ Generated {drafts_count} draft(s) for {workspace}")

            except Exception as e:
                logger.error(f"❌ Failed to process workspace {workspace}: {e}")
                continue

        logger.info(f"🎉 Total drafts generated: {total_drafts}")
        return total_drafts
    
    async def _process_workspace(self, workspace: str, start_date: datetime) -> int:
        """
        Process emails for a single workspace since start_date.

        Args:
            workspace: Workspace name
            start_date: Process emails received after this time

        Returns:
            Number of drafts created
        """
        from promaia.config.databases import get_database_manager

        # Get Gmail databases for this workspace
        db_manager = get_database_manager()
        gmail_databases = [
            db for db in db_manager.get_workspace_databases(workspace)
            if db.source_type == "gmail"
        ]

        if not gmail_databases:
            logger.warning(f"⚠️  No Gmail databases found for workspace {workspace}")
            return 0

        total_drafts = 0

        for gmail_db in gmail_databases:
            try:
                drafts = await self._process_gmail_database(gmail_db, workspace, start_date)
                total_drafts += drafts
            except Exception as e:
                logger.error(f"❌ Failed to process Gmail database {gmail_db.get_qualified_name()}: {e}")
                continue

        return total_drafts
    
    async def _process_gmail_database(self, db_config, workspace: str, start_date: datetime) -> int:
        """
        Process a single Gmail database since start_date.

        Args:
            db_config: Database configuration
            workspace: Workspace name
            start_date: Process emails received after this time

        Returns:
            Number of drafts created
        """
        from promaia.connectors.gmail_connector import GmailConnector
        from promaia.connectors.base import DateRangeFilter

        logger.info(f"📬 Checking {db_config.get_qualified_name()} for new emails since {start_date.strftime('%Y-%m-%d %H:%M:%S UTC')}...")

        # Create connector with full_thread mode for maia mail
        # (Users need full context to review and refine draft responses)
        connector = GmailConnector({
            "database_id": db_config.database_id,
            "workspace": workspace,
            "gmail_content_mode": "full_thread"  # Get complete conversation history
        })

        await connector.connect()

        # Query emails since start_date
        date_filter = DateRangeFilter(
            property_name="date",
            start_date=start_date
        )
        
        # Get recent threads
        threads = await connector.query_pages(
            date_filter=date_filter,
            limit=50  # Process max 50 threads per run
        )
        
        if not threads:
            logger.info("No new threads found")
            return 0
        
        # Filter out threads where the user sent the last message
        # We only want to process inbound messages that need responses
        inbound_threads = [t for t in threads if not t.get('last_message_from_user', False)]
        
        if len(inbound_threads) < len(threads):
            filtered_count = len(threads) - len(inbound_threads)
            logger.info(f"Filtered out {filtered_count} thread(s) where you sent the last message")
        
        if not inbound_threads:
            logger.info("No inbound threads found (all last messages were from you)")
            return 0
        
        logger.info(f"Found {len(inbound_threads)} inbound thread(s) to process")
        
        # Process each thread
        drafts_created = 0

        for thread in inbound_threads:
            try:
                # Check if we already have a draft for this EXACT message
                # This provides true idempotence - won't re-process same message
                # But WILL process new replies to existing threads
                thread_id = thread.get('thread_id')
                message_ids = thread.get('message_ids', [])
                last_message_id = message_ids[-1] if message_ids else thread_id

                if not self.dry_run and self.draft_manager.message_has_draft(thread_id, last_message_id, workspace):
                    logger.debug(f"Skipping message {last_message_id} - already processed")
                    continue

                # Process thread
                draft_created = await self._process_thread(thread, workspace, db_config.database_id)
                if draft_created:
                    drafts_created += 1

                # Small delay to avoid overwhelming API
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ Failed to process thread {thread.get('thread_id')}: {e}")
                continue

        return drafts_created
    
    async def _process_thread(self, thread: Dict[str, Any], workspace: str, email: str) -> bool:
        """
        Process a single email thread through the pipeline.

        Returns:
            True if draft was created, False otherwise
        """
        thread_id = thread.get('thread_id')
        subject = thread.get('subject', 'No Subject')

        logger.info(f"Processing: {subject}")

        # Check if user has already replied to this thread
        if thread.get('last_message_from_user', False):
            logger.info(f"  ⏭️  Skipping - last message was sent by user (already replied)")
            return False

        # Step 1: Classify
        logger.debug("  → Classifying...")
        classification = await self.classifier.classify(thread, user_email=email, workspace=workspace)
        
        logger.info(
            f"  → Classification: "
            f"pertains={classification['pertains_to_me']}, "
            f"spam={classification['is_spam']}, "
            f"addressed_to_user={classification.get('addressed_to_user', 'unknown')}, "
            f"requires_response={classification['requires_response']}"
        )
        
        # Determine draft status
        draft_status = self.classifier.get_draft_status(classification)
        
        # Check if we should generate a draft
        if not self.classifier.should_generate_draft(classification):
            if self.dry_run:
                logger.info(f"  ⏭️  [DRY RUN] Would skip: {subject} (reason: {classification['reasoning'][:80]})")
                return True

            logger.info(f"  → No response needed - creating skipped draft for review")
            # Create a "skipped" draft (no AI generation, no context loading)
            # User can override by entering draft chat and using /mc to load context
            draft_data = {
                'workspace': workspace,
                'thread_id': thread_id,
                'message_id': thread.get('message_ids', [])[-1] if thread.get('message_ids') else thread_id,
                'inbound_subject': subject,
                'inbound_from': thread.get('from'),
                'inbound_snippet': thread.get('snippet', ''),
                'inbound_date': thread.get('date'),
                'inbound_body': thread.get('conversation_body', ''),
                'pertains_to_me': classification['pertains_to_me'],
                'is_spam': classification['is_spam'],
                'requires_response': classification['requires_response'],
                'classification_reasoning': classification['reasoning'],
                'draft_subject': f"Re: {subject}",
                'draft_body': 'n/a',  # No draft generated
                'response_context': None,  # No context loaded (user can load with /mc in chat)
                'system_prompt': None,
                'ai_model': None,
                'thread_context': thread.get('conversation_body', '')[:500],  # Store snippet
                'message_count': thread.get('message_count', 1),
                'status': draft_status,
                'addressed_to_user': classification.get('addressed_to_user', 'unknown')
            }

            draft_id = self.draft_manager.save_draft(draft_data)
            logger.info(f"  ⏭️  Skipped draft saved: {draft_id}")
            return True  # Count as created so it shows in review queue
        
        # Step 2: Run agentic responder (replaces context building + generation + saving)
        from promaia.mail.agentic_responder import respond as agentic_respond
        drafts_created = await agentic_respond(
            thread=thread,
            classification=classification,
            workspace=workspace,
            email=email,
            draft_manager=self.draft_manager,
            dry_run=self.dry_run,
        )
        return drafts_created > 0
    
    async def refresh_drafts(self, workspaces: List[str], days_back: int = 7) -> int:
        """
        Refresh existing drafts by rebuilding context, thread, and replies.
        
        Only refreshes drafts with status 'pending', 'unsure', or 'skipped'.
        Respects 'sent' and 'archived' statuses (does not touch them).
        
        Args:
            workspaces: List of workspace names
            days_back: Number of days to look back for drafts to refresh
            
        Returns:
            Number of drafts refreshed
        """
        total_refreshed = 0
        
        for workspace in workspaces:
            logger.info(f"🔄 Refreshing drafts for workspace: {workspace}")
            
            try:
                refreshed_count = await self._refresh_workspace_drafts(workspace, days_back)
                total_refreshed += refreshed_count
                logger.info(f"✅ Refreshed {refreshed_count} draft(s) for {workspace}")
                
            except Exception as e:
                logger.error(f"❌ Failed to refresh drafts for workspace {workspace}: {e}")
                continue
        
        logger.info(f"🎉 Total drafts refreshed: {total_refreshed}")
        return total_refreshed
    
    async def _refresh_workspace_drafts(self, workspace: str, days_back: int) -> int:
        """Refresh drafts for a single workspace."""
        from datetime import datetime, timedelta, timezone
        
        # Get drafts that need refreshing
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        # Query drafts with status in ('pending', 'unsure', 'skipped') within date range
        drafts = self.draft_manager.get_refreshable_drafts(
            workspace, 
            cutoff_date.isoformat(),
            statuses=['pending', 'unsure', 'skipped']
        )
        
        if not drafts:
            logger.info(f"No drafts to refresh for {workspace}")
            return 0
        
        logger.info(f"Found {len(drafts)} draft(s) to refresh")
        
        # Group drafts by Gmail database for efficient processing
        drafts_by_db = {}
        for draft in drafts:
            # Extract database info from draft
            # We'll use the workspace to find the right Gmail connector
            db_key = workspace  # Simplified - could be more sophisticated
            if db_key not in drafts_by_db:
                drafts_by_db[db_key] = []
            drafts_by_db[db_key].append(draft)
        
        refreshed_count = 0
        
        # Process each draft
        for draft in drafts:
            try:
                refreshed = await self._refresh_single_draft(draft, workspace)
                if refreshed:
                    refreshed_count += 1
                
                # Small delay to avoid overwhelming API
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"❌ Failed to refresh draft {draft.get('draft_id')}: {e}")
                continue
        
        return refreshed_count
    
    async def _refresh_single_draft(self, draft: Dict[str, Any], workspace: str) -> bool:
        """
        Refresh a single draft by re-fetching thread and regenerating response.
        
        Returns:
            True if draft was refreshed, False otherwise
        """
        from promaia.connectors.gmail_connector import GmailConnector
        from promaia.config.databases import get_database_manager
        
        draft_id = draft.get('draft_id')
        thread_id = draft.get('thread_id')
        subject = draft.get('inbound_subject', 'No Subject')
        
        logger.info(f"Refreshing: {subject}")
        
        # Get Gmail databases for this workspace
        db_manager = get_database_manager()
        gmail_databases = [
            db for db in db_manager.get_workspace_databases(workspace)
            if db.source_type == "gmail"
        ]
        
        if not gmail_databases:
            logger.warning(f"⚠️  No Gmail databases found for workspace {workspace}")
            return False
        
        # Use first Gmail database (in most cases there's only one per workspace)
        gmail_db = gmail_databases[0]
        
        # Create connector with full_thread mode
        connector = GmailConnector({
            "database_id": gmail_db.database_id,
            "workspace": workspace,
            "gmail_content_mode": "full_thread"
        })
        
        await connector.connect()
        
        # Fetch fresh thread data from Gmail
        try:
            # Get the thread by ID
            service = connector.service
            thread_data = service.users().threads().get(
                userId='me', 
                id=thread_id,
                format='full'
            ).execute()
            
            # Process thread data using connector's method
            thread = connector._process_thread_data(thread_data)
            
            if not thread:
                logger.warning(f"⚠️  Could not fetch thread {thread_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to fetch thread from Gmail: {e}")
            return False

        # Check if user has already replied to this thread
        if thread.get('last_message_from_user', False):
            logger.info(f"  ⏭️  User has replied - archiving draft")
            # Update draft to archived status
            self.draft_manager.update_draft_refresh(
                draft_id=draft_id,
                status='archived',
                classification={'pertains_to_me': True, 'is_spam': False, 'requires_response': False, 'reasoning': 'User has already replied to this thread'},
                thread=thread,
                draft_body='n/a',
                response_context=None,
                system_prompt=None,
                ai_model=None
            )
            logger.info(f"  📦 Draft archived: {draft_id}")
            return True

        # Re-classify
        logger.debug("  → Re-classifying...")
        classification = await self.classifier.classify(thread, user_email=gmail_db.database_id, workspace=workspace)
        
        logger.info(
            f"  → Classification: "
            f"pertains={classification['pertains_to_me']}, "
            f"spam={classification['is_spam']}, "
            f"addressed_to_user={classification.get('addressed_to_user', 'unknown')}, "
            f"requires_response={classification['requires_response']}"
        )
        
        # Determine new status
        new_status = self.classifier.get_draft_status(classification)
        
        # Check if we should generate a draft
        if not self.classifier.should_generate_draft(classification):
            logger.info(f"  → Updating to skipped (no response needed)")
            # Update to skipped status
            self.draft_manager.update_draft_refresh(
                draft_id=draft_id,
                status=new_status,
                classification=classification,
                thread=thread,
                draft_body='n/a',
                response_context=None,
                system_prompt=None,
                ai_model=None
            )
            logger.info(f"  ⏭️  Draft updated to skipped: {draft_id}")
            return True
        
        # Rebuild context
        logger.debug("  → Rebuilding context...")
        context = await self.context_builder.build_context(thread, workspace)
        logger.info(f"  → Found {context.total_sources} relevant sources")

        # Build structured context for EmailPromptBuilder (same format as draft_chat)
        # Separate email vs non-email documents
        email_docs = []
        non_email_docs = []

        for doc in context.relevant_docs:
            db_name = doc.get('database', 'unknown')

            # Create page dict
            page = {
                'title': doc.get('title', 'Untitled'),
                'content': doc.get('content_snippet', ''),
                'metadata': doc.get('metadata', {}),
                'database': db_name,
                'similarity': doc.get('similarity', 0),
            }

            # Separate by type
            if db_name == 'gmail' or db_name.endswith('.gmail'):
                email_docs.append(page)
            else:
                non_email_docs.append(page)

        # Build structured context dict
        structured_context = {
            'thread_email': {
                'from': thread.get('from', ''),
                'to': thread.get('to', ''),
                'cc': thread.get('cc', ''),
                'date': thread.get('date', ''),
                'subject': subject,
                'body': thread.get('conversation_body', ''),
            },
            'thread_conversation': thread.get('conversation_body', ''),
            'message_count': thread.get('message_count', 1),
            'non_email_docs': non_email_docs,
            'email_docs': email_docs,
        }

        # Regenerate response
        logger.debug("  → Regenerating response...")
        response = await self.response_generator.generate_response(thread, workspace, structured_context)
        logger.info(f"  → Generated {len(response['body'].split())} word response")
        
        # Update draft
        logger.debug("  → Updating draft...")
        status_emoji = "🤷‍♀️" if new_status == "unsure" else "✅"
        logger.info(f"  {status_emoji} Draft status: {new_status}")
        
        self.draft_manager.update_draft_refresh(
            draft_id=draft_id,
            status=new_status,
            classification=classification,
            thread=thread,
            draft_body=response['body'],
            draft_subject=response['subject'],
            response_context=self.context_builder.serialize_context_for_storage(context),
            system_prompt=response.get('prompt'),
            ai_model=response['model']
        )
        
        logger.info(f"  ✅ Draft refreshed: {draft_id}")
        return True

