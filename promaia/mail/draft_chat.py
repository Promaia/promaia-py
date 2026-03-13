"""
Draft Chat Interface - Thin wrapper around maia chat for email drafts.

Displays the email thread, then launches unified chat with DraftMode
for artifact-based draft composition.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from promaia.mail.draft_manager import DraftManager
from promaia.utils.display import print_text, print_separator
from promaia.utils.timezone_utils import to_local, get_local_timezone_name, now_utc
from promaia.connectors.gmail_connector import GmailConnector
from promaia.config.databases import get_database_manager
from promaia.mail.thread_formatter import format_thread_for_display
from promaia.mail.context_builder import ResponseContext, ResponseContextBuilder

logger = logging.getLogger(__name__)


class DraftChatInterface:
    """
    Thin wrapper around maia chat for email drafts.
    
    Displays email thread, loads context, then launches unified chat
    with DraftMode for specialized email drafting behavior.
    """
    
    def __init__(self, draft_id: str, workspace: str, force_load_context: bool = False):
        """
        Initialize draft chat interface.

        Args:
            draft_id: Draft ID to work with
            workspace: Workspace name
            force_load_context: If True, always load full context (used when -dc flag is passed)
        """
        self.draft_id = draft_id
        self.workspace = workspace
        self.force_load_context = force_load_context
        self.draft_manager = DraftManager()
        self.context_builder = ResponseContextBuilder()

        # Initialize response generator for -dc support
        from promaia.mail.response_generator import ResponseGenerator
        self.response_generator = ResponseGenerator()
    
    def _get_user_email(self) -> Optional[str]:
        """Get user's email from workspace gmail database."""
        try:
            db_manager = get_database_manager()
            gmail_databases = [
                db for db in db_manager.get_workspace_databases(self.workspace)
                if db.source_type == "gmail"
            ]
            if gmail_databases:
                return gmail_databases[0].database_id
        except Exception as e:
            logger.debug(f"Could not get user email from workspace: {e}")
        return None
    
    async def _refetch_full_thread_if_needed(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        """If inbound_body is just a summary, refetch the full thread."""
        inbound_body = draft.get('inbound_body', '')
        if "Showing latest message only" not in inbound_body:
            return draft

        print_text("\n🔄 Detected summarized thread, fetching full conversation...", style="cyan")
        
        try:
            workspace = draft.get('workspace')
            thread_id = draft.get('thread_id')
            
            if not workspace or not thread_id:
                print_text("⚠️  Missing workspace or thread_id, cannot refetch.", style="yellow")
                return draft

            db_manager = get_database_manager()
            gmail_dbs = [
                db for db in db_manager.get_workspace_databases(workspace)
                if db.source_type == "gmail"
            ]
            
            if not gmail_dbs:
                print_text(f"⚠️  No Gmail database found for workspace {workspace}.", style="yellow")
                return draft

            connector = GmailConnector({
                "database_id": gmail_dbs[0].database_id,
                "workspace": workspace,
                "gmail_content_mode": "full_thread"
            })
            await connector.connect()
            
            full_thread_data = await connector.get_page_content(page_id=f"thread_{thread_id}")
            
            if full_thread_data and 'conversation_body' in full_thread_data:
                new_body = full_thread_data['conversation_body']
                draft['inbound_body'] = new_body
                
                # Update in database so we don't refetch next time
                self.draft_manager.update_inbound_body(self.draft_id, new_body)
                print_text("✅ Full thread loaded.", style="green")
            else:
                print_text("⚠️  Failed to fetch full thread.", style="yellow")

        except Exception as e:
            logger.error(f"Failed to refetch full thread: {e}")
            print_text(f"❌ Error fetching full thread: {e}", style="red")
            
        return draft
    
    def _clean_email_body(self, body: str) -> str:
        """Remove redundant email headers from body content."""
        if not body:
            return body
        
        lines = body.split('\n')
        cleaned_lines = []
        skip_headers = True
        
        for line in lines:
            if skip_headers:
                if line.strip().startswith(('From:', 'Sent:', 'To:', 'Subject:', 'Date:', 'Cc:', 'Bcc:')):
                    continue
                elif not line.strip():
                    continue
                else:
                    skip_headers = False
                    cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines).strip()
    
    async def _load_message_context(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        """
        Load context for the email thread.

        Returns structured context dict with separate sections:
        - thread_email: The email being replied to
        - thread_conversation: Full thread if multi-message
        - non_email_docs: Vector search results from non-email databases
        - email_docs: Vector search results from email databases
        - draft_data: Raw draft data for custom prompt building
        """
        print_text("\n🔍 Loading message context...", style="cyan")

        # Build email thread dict for context builder
        thread = {
            'thread_id': draft.get('thread_id'),
            'subject': draft.get('inbound_subject'),
            'body': draft.get('inbound_body'),
            'conversation_body': draft.get('thread_context', ''),
            'from': draft.get('inbound_from'),
            'date': draft.get('inbound_date'),
            'message_count': draft.get('message_count', 1)
        }

        # Build context using dual vector search
        context = await self.context_builder.build_context(thread, self.workspace)

        print_text(f"✅ Loaded {context.total_sources} sources from your knowledge base\n", style="green")

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

        # Return structured context for custom prompt building
        return {
            'thread_email': {
                'from': draft.get('inbound_from', ''),
                'to': draft.get('inbound_to', ''),
                'cc': draft.get('inbound_cc', ''),
                'date': draft.get('inbound_date', ''),
                'subject': draft.get('inbound_subject', ''),
                'body': draft.get('inbound_body', ''),
            },
            'thread_conversation': draft.get('thread_context', ''),
            'message_count': draft.get('message_count', 1),
            'non_email_docs': non_email_docs,
            'email_docs': email_docs,
            'draft_data': draft,
        }
    
    async def run_chat_loop(self):
        """
        Main entry point - display email thread then launch unified chat with DraftMode.
        
        Note: chat() is synchronous but blocking, so we run it in a thread to await it properly.
        """
        import asyncio
        
        try:
            # Load current draft
            draft = self.draft_manager.get_draft(self.draft_id)
            
            if not draft:
                print_text(f"❌ Draft {self.draft_id} not found", style="red")
                return

            # If the draft only has a summary, fetch the full thread content
            draft = await self._refetch_full_thread_if_needed(draft)
            
            # Get user email
            user_email = self._get_user_email()
            if not user_email:
                print_text("⚠️  Could not determine user email", style="yellow")
                user_email = "unknown@example.com"
            
            # Format date
            try:
                received_dt = datetime.fromisoformat(draft.get('inbound_date', '').replace('Z', '+00:00'))
                local_received = to_local(received_dt)
                tz_name = get_local_timezone_name()
                received_str = local_received.strftime(f'%A, %B %d, %Y at %I:%M %p {tz_name}')
            except:
                received_str = draft.get('inbound_date', 'Unknown')
            
            # Clean email body
            cleaned_body = self._clean_email_body(draft.get('inbound_body', ''))

            # Display full email thread
            # Add top margin for terminals with UI elements at top
            print("\n\n\n")
            print_separator()
            
            message_count = draft.get('message_count', 1)
            thread_display = format_thread_for_display(
                conversation_body=cleaned_body,
                message_count=message_count,
                from_addr=draft['inbound_from'],
                subject=draft['inbound_subject'],
                received_str=received_str,
                use_colors=True
            )
            
            print(thread_display)
            print_separator()
            
            if message_count > 1:
                print()
                print_text("📜 Tip: Scroll up ↑ to see earlier messages in the thread", style="dim")
            print()

            # Load chat history first (before any other logic)
            chat_history = self.draft_manager.load_chat_messages(self.draft_id)
            logger.info(f"Loaded {len(chat_history)} messages from chat history")

            # Check draft status
            draft_status = draft.get('status', 'pending')
            draft_body = draft.get('draft_body', '')

            # Handle skipped drafts with 3-option prompt
            # Only show menu if no chat history exists (preserves existing conversations)
            if chat_history:
                # Chat history exists - skip the menu and resume the conversation
                logger.info("Chat history exists, skipping skipped menu and resuming conversation")
                message_context = await self._load_message_context(draft)
                initial_messages = chat_history
                auto_respond = False
            elif draft_status == 'skipped' and not self.force_load_context:
                # Show AI reasoning
                print_text("⏭️  SKIPPED - No response needed", style="bold yellow")
                print()
                print_text("AI Assessment:", style="dim")
                if draft.get('classification_reasoning'):
                    print_text(f"  {draft.get('classification_reasoning')}", style="dim")
                print()

                # Show 4 options
                print_text("Options:", style="cyan")
                print_text("  ENTER - Load context and generate draft", style="cyan")
                print_text("  a - Archive and return to queue", style="cyan")
                print_text("  c - Continue with thread only (use /e -dc later to add context)", style="cyan")
                print_text("  q - Return to queue without archiving", style="cyan")
                print()

                # Capture single keypress
                try:
                    import sys
                    import tty
                    import termios

                    # Get single keypress without requiring Enter
                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)
                    try:
                        tty.setraw(fd)
                        key = sys.stdin.read(1)
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

                    # Handle ENTER key (newline/return)
                    if key in ['\r', '\n']:
                        choice = ''
                        print()  # Move to next line after keypress
                    else:
                        choice = key.lower()
                        print(choice)  # Echo the key
                        print()

                except (EOFError, KeyboardInterrupt):
                    print()
                    print_text("\n↩️  Returning to draft list...\n", style="cyan")
                    return
                except Exception as e:
                    # Fallback to regular input if single keypress fails
                    logger.debug(f"Single keypress failed, falling back to input: {e}")
                    try:
                        choice = input("Your choice: ").strip().lower()
                        print()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        print_text("\n↩️  Returning to draft list...\n", style="cyan")
                        return

                # Handle user choice
                auto_respond = False  # Initialize flag for auto-response
                if choice == 'a':
                    # Archive and return
                    self.draft_manager.update_draft_status(self.draft_id, 'archived')
                    print_text("🗄️  Archived - cleared from your queue\n", style="green")
                    return

                elif choice == '' or choice == 'enter':
                    # Load context and prompt AI to write draft
                    print_text("🔍 Loading context...\n", style="cyan")

                    # Load full context with vector search
                    message_context = await self._load_message_context(draft)
                    logger.info(f"Loaded message context: {len(message_context.get('non_email_docs', []))} non-email docs, {len(message_context.get('email_docs', []))} email docs")

                    # Set initial user message to prompt the AI
                    # The AI will decide whether to use <artifact> or ask questions
                    # based on maia_mail_prompt.md guidelines
                    initial_messages = [{
                        "role": "user",
                        "content": "Write a reply to this email."
                    }]
                    auto_respond = True  # Flag to trigger auto-response
                    logger.info("Set initial user message to prompt draft creation")

                elif choice == 'c':
                    # Continue with minimal context (thread only, no vector search)
                    print_text("📧 Proceeding with thread context only\n", style="dim")
                    message_context = {
                        'thread_email': {
                            'from': draft.get('inbound_from', ''),
                            'to': draft.get('inbound_to', ''),
                            'cc': draft.get('inbound_cc', ''),
                            'date': draft.get('inbound_date', ''),
                            'subject': draft.get('inbound_subject', ''),
                            'body': draft.get('inbound_body', ''),
                        },
                        'thread_conversation': draft.get('thread_context', ''),
                        'message_count': draft.get('message_count', 1),
                        'non_email_docs': [],
                        'email_docs': [],
                        'draft_data': draft,
                    }
                    logger.info("Using minimal context (thread only, no vector search)")

                elif choice == 'q':
                    # Return to queue without archiving
                    print_text("↩️  Returning to draft list...\n", style="cyan")
                    return

                else:
                    # Invalid choice - default to minimal context
                    print_text("⚠️  Invalid choice, proceeding with thread context only\n", style="yellow")
                    message_context = {
                        'thread_email': {
                            'from': draft.get('inbound_from', ''),
                            'to': draft.get('inbound_to', ''),
                            'cc': draft.get('inbound_cc', ''),
                            'date': draft.get('inbound_date', ''),
                            'subject': draft.get('inbound_subject', ''),
                            'body': draft.get('inbound_body', ''),
                        },
                        'thread_conversation': draft.get('thread_context', ''),
                        'message_count': draft.get('message_count', 1),
                        'non_email_docs': [],
                        'email_docs': [],
                        'draft_data': draft,
                    }
                    logger.info("Using minimal context (thread only, no vector search)")

            else:
                # For non-skipped drafts or when -dc flag is used, load full context
                auto_respond = False  # No auto-response for non-skipped drafts
                message_context = await self._load_message_context(draft)
                logger.info(f"Loaded message context: {len(message_context.get('non_email_docs', []))} non-email docs, {len(message_context.get('email_docs', []))} email docs")
            
            # Create DraftMode
            from promaia.chat.modes import DraftMode

            mode = DraftMode(
                workspace=self.workspace,
                draft_id=self.draft_id,
                draft_data=draft,
                draft_manager=self.draft_manager,
                user_email=user_email,
                context_builder=self.context_builder,
                response_generator=self.response_generator,
                structured_context=message_context  # Pass structured context for custom prompt
            )
            
            # Set initial_messages if not already set by earlier logic
            # (chat_history branch or skipped menu sets initial_messages and auto_respond)
            if 'initial_messages' not in locals():
                if chat_history:
                    # Use loaded chat history
                    initial_messages = chat_history
                    logger.info(f"Using {len(chat_history)} messages from loaded chat history")
                elif draft_body and draft_body != 'n/a':
                    # No history - load draft body as initial message
                    if '<artifact>' in draft_body and '</artifact>' in draft_body:
                        # Already has artifact tags, use as-is (this is an email draft)
                        message_content = draft_body
                        logger.info(f"Draft body already contains artifact tags, using as-is")
                    else:
                        # No artifact tags - determine if this is an email draft or skip reasoning
                        # Skip reasoning messages start with phrases like "This is", "While", etc.
                        draft_start = draft_body.strip()[:100].lower()
                        is_skip_reasoning = (
                            draft_start.startswith('this is') or
                            draft_start.startswith('while') or
                            "doesn't require" in draft_start or
                            "does not require" in draft_start or
                            "no response needed" in draft_start
                        )

                        if is_skip_reasoning:
                            # This is skip reasoning text, not an email draft
                            message_content = draft_body
                            logger.info(f"Draft body is skip reasoning (no artifact tags), loading as regular message")
                        else:
                            # This is an email draft without artifact tags - wrap it for proper display
                            message_content = f"<artifact>\n{draft_body}\n</artifact>"
                            logger.info(f"Draft body is email content without artifact tags, wrapping for display")

                    initial_messages = [{
                        "role": "assistant",
                        "content": message_content
                    }]
                    logger.info(f"Loaded draft body as initial message")
                else:
                    initial_messages = []
                    logger.info(f"Starting fresh chat session")

            # Ensure auto_respond is set (default to False if not already set)
            if 'auto_respond' not in locals():
                auto_respond = False

            # Launch unified chat with DraftMode
            from promaia.chat.interface import chat

            logger.info(f"Launching unified chat with DraftMode")
            logger.info(f"  Workspace: {self.workspace}")
            logger.info(f"  Message context: {len(message_context.get('non_email_docs', []))} non-email docs, {len(message_context.get('email_docs', []))} email docs")
            logger.info(f"  Initial messages: {len(initial_messages)}")
            logger.info(f"  Auto-respond: {auto_respond}")

            # Note: chat() is synchronous and blocking, run in thread to await properly
            # Chat returns the messages list when it exits
            # NOTE: Don't pass sql_query_content since DraftMode handles its own context
            # (via structured_context passed to DraftMode constructor)
            result = await asyncio.to_thread(
                chat,
                workspace=self.workspace,
                mode=mode,
                sql_query_content=None,  # DraftMode handles its own context
                initial_messages=initial_messages,  # Chat history
                draft_id=self.draft_id,  # Pass draft_id for saving messages
                auto_respond_to_initial=auto_respond,  # Auto-trigger AI response if requested
            )

            logger.info(f"Chat completed, returned {len(result) if result else 0} messages")

            # Save final messages state when chat exits normally
            if result:
                try:
                    self.draft_manager.save_chat_messages(self.draft_id, result)
                    logger.info(f"Saved {len(result)} messages to database on normal exit")
                except Exception as e:
                    logger.error(f"Failed to save chat messages on normal exit: {e}")

        except KeyboardInterrupt:
            print_text("\n\n↩️  Returning to draft list...\n", style="cyan")
            logger.info("Chat interrupted by user (Ctrl+C)")
            # Ensure messages are saved before exiting
            try:
                # The chat function should have saved already, but do a final save to be certain
                chat_messages = self.draft_manager.load_chat_messages(self.draft_id)
                if chat_messages:
                    self.draft_manager.save_chat_messages(self.draft_id, chat_messages)
                    logger.info(f"💾 Final save: {len(chat_messages)} messages on interrupt")
            except Exception as e:
                logger.error(f"Failed to save on interrupt: {e}")
        except EOFError:
            print_text("\n\n↩️  Returning to draft list...\n", style="cyan")
            logger.info("Chat interrupted by EOF (Ctrl+D)")
            # Ensure messages are saved before exiting
            try:
                # The chat function should have saved already, but do a final save to be certain
                chat_messages = self.draft_manager.load_chat_messages(self.draft_id)
                if chat_messages:
                    self.draft_manager.save_chat_messages(self.draft_id, chat_messages)
                    logger.info(f"💾 Final save: {len(chat_messages)} messages on EOF")
            except Exception as e:
                logger.error(f"Failed to save on EOF: {e}")
