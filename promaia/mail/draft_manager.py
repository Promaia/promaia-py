"""
Draft Manager - SQLite operations for email drafts.

Manages the email_drafts table in hybrid_metadata.db with CRUD operations.
"""
import sqlite3
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


def get_safety_string_from_recipient(recipient: str) -> str:
    """Extract safety confirmation string from recipient email.

    Returns first 5 characters of email address, or everything before @,
    whichever comes first.

    Examples:
        "alice@example.com" -> "alice"
        "joe@example.com" -> "joe"
        "hello@test.com" -> "hello"
    """
    if not recipient:
        return ""

    # Extract just the email if it's in "Name <email>" format
    if '<' in recipient and '>' in recipient:
        recipient = recipient.split('<')[1].split('>')[0]

    recipient = recipient.strip()

    # Find @ position
    at_pos = recipient.find('@')

    if at_pos == -1:
        # No @ found, just take first 5 chars
        return recipient[:5].lower()

    # Take first 5 chars or everything before @, whichever is shorter
    local_part = recipient[:at_pos]
    safety_string = local_part[:5] if len(local_part) > 5 else local_part

    return safety_string.lower()


class DraftManager:
    """SQLite operations for email drafts."""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path
        self._ensure_table()
    
    def _ensure_table(self):
        """Create email_drafts and mail_sync_state tables if they don't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Create mail_sync_state table for tracking last sync times
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mail_sync_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace TEXT UNIQUE NOT NULL,
                        last_sync_time TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS email_drafts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        draft_id TEXT UNIQUE NOT NULL,
                        workspace TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        inbound_subject TEXT,
                        inbound_from TEXT,
                        inbound_snippet TEXT,
                        inbound_date TEXT,
                        inbound_body TEXT,
                        
                        -- Classification results
                        pertains_to_me BOOLEAN DEFAULT TRUE,
                        is_spam BOOLEAN DEFAULT FALSE,
                        requires_response BOOLEAN DEFAULT TRUE,
                        classification_reasoning TEXT,
                        
                        -- Draft response
                        draft_subject TEXT,
                        draft_body TEXT,
                        draft_body_html TEXT,
                        
                        -- Context used for generation
                        response_context TEXT,
                        system_prompt TEXT,
                        ai_model TEXT,
                        
                        -- Draft versioning and chat
                        draft_number INTEGER DEFAULT 1,
                        chat_session_id TEXT,
                        previous_draft_id TEXT,
                        version INTEGER DEFAULT 1,
                        draft_history TEXT,  -- JSON array of all draft versions
                        
                        -- Status tracking
                        status TEXT DEFAULT 'pending',
                        created_time TEXT NOT NULL,
                        reviewed_time TEXT,
                        sent_time TEXT,
                        completed_time TEXT,  -- When draft was marked as sent/archived (final state)
                        
                        -- Safety mechanism
                        safety_string TEXT,
                        
                        -- Thread context
                        thread_context TEXT,
                        message_count INTEGER DEFAULT 1,
                        
                        UNIQUE(draft_id)
                    )
                """)
                
                # Create indexes for common queries
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_drafts_workspace_status 
                    ON email_drafts(workspace, status)
                """)
                
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_drafts_thread_id 
                    ON email_drafts(thread_id)
                """)
                
                conn.commit()
                
                # Migrate existing tables to add draft_history column if missing
                self._migrate_draft_history_column(cursor)
                # Migrate to add completed_time column if missing
                self._migrate_completed_time_column(cursor)
                # Migrate to add inbound_to and inbound_cc columns if missing
                self._migrate_recipient_columns(cursor)
                # Migrate to add chat_messages column if missing
                self._migrate_chat_messages_column(cursor)
                conn.commit()
                
                logger.info("✅ Email drafts table initialized")
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize email_drafts table: {e}")
            raise
    
    def _migrate_draft_history_column(self, cursor):
        """Add draft_history column to existing tables if it doesn't exist."""
        try:
            # Check if draft_history column exists
            cursor.execute("PRAGMA table_info(email_drafts)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'draft_history' not in columns:
                logger.info("🔄 Migrating email_drafts table to add draft_history column...")
                cursor.execute("""
                    ALTER TABLE email_drafts 
                    ADD COLUMN draft_history TEXT
                """)
                logger.info("✅ Added draft_history column")
        except Exception as e:
            logger.warning(f"⚠️  Draft history migration: {e}")
    
    def _migrate_completed_time_column(self, cursor):
        """Add completed_time column to existing tables if it doesn't exist."""
        try:
            # Check if completed_time column exists
            cursor.execute("PRAGMA table_info(email_drafts)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'completed_time' not in columns:
                logger.info("🔄 Migrating email_drafts table to add completed_time column...")
                cursor.execute("""
                    ALTER TABLE email_drafts 
                    ADD COLUMN completed_time TEXT
                """)
                logger.info("✅ Added completed_time column")
                
                # Backfill completed_time for existing sent/archived drafts
                # Use sent_time for sent drafts, reviewed_time for archived drafts
                cursor.execute("""
                    UPDATE email_drafts 
                    SET completed_time = sent_time 
                    WHERE status = 'sent' AND sent_time IS NOT NULL
                """)
                cursor.execute("""
                    UPDATE email_drafts 
                    SET completed_time = reviewed_time 
                    WHERE status = 'archived' AND reviewed_time IS NOT NULL AND completed_time IS NULL
                """)
                logger.info("✅ Backfilled completed_time for existing drafts")
        except Exception as e:
            logger.warning(f"⚠️  Completed time migration: {e}")
    
    def _migrate_recipient_columns(self, cursor):
        """Add inbound_to and inbound_cc columns to existing tables if they don't exist."""
        try:
            # Check if columns exist
            cursor.execute("PRAGMA table_info(email_drafts)")
            columns = [col[1] for col in cursor.fetchall()]

            if 'inbound_to' not in columns:
                logger.info("🔄 Migrating email_drafts table to add inbound_to column...")
                cursor.execute("""
                    ALTER TABLE email_drafts
                    ADD COLUMN inbound_to TEXT
                """)
                logger.info("✅ Added inbound_to column")

            if 'inbound_cc' not in columns:
                logger.info("🔄 Migrating email_drafts table to add inbound_cc column...")
                cursor.execute("""
                    ALTER TABLE email_drafts
                    ADD COLUMN inbound_cc TEXT
                """)
                logger.info("✅ Added inbound_cc column")
        except Exception as e:
            logger.warning(f"⚠️  Recipient columns migration: {e}")

    def _migrate_chat_messages_column(self, cursor):
        """Add chat_messages column to existing tables if it doesn't exist."""
        try:
            # Check if chat_messages column exists
            cursor.execute("PRAGMA table_info(email_drafts)")
            columns = [col[1] for col in cursor.fetchall()]

            if 'chat_messages' not in columns:
                logger.info("🔄 Migrating email_drafts table to add chat_messages column...")
                cursor.execute("""
                    ALTER TABLE email_drafts
                    ADD COLUMN chat_messages TEXT
                """)
                logger.info("✅ Added chat_messages column for conversation history")
        except Exception as e:
            logger.warning(f"⚠️  Chat messages migration: {e}")
    
    def save_draft(self, draft: Dict[str, Any]) -> str:
        """
        Save a new draft, return draft_id.
        
        Args:
            draft: Dictionary containing all draft fields
            
        Returns:
            draft_id of the saved draft
        """
        draft_id = draft.get('draft_id') or str(uuid.uuid4())

        # Generate safety string from recipient email
        recipient = draft.get('inbound_from', '')  # The person we're replying to
        safety_string = get_safety_string_from_recipient(recipient)
        
        # Initialize draft history with first version
        initial_history = {1: draft.get('draft_body', '')}
        draft_history = json.dumps(initial_history)
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO email_drafts (
                        draft_id, workspace, thread_id, message_id,
                        inbound_subject, inbound_from, inbound_to, inbound_cc, inbound_snippet, inbound_date, inbound_body,
                        pertains_to_me, is_spam, requires_response, classification_reasoning,
                        draft_subject, draft_body, draft_body_html,
                        response_context, system_prompt, ai_model,
                        draft_number, chat_session_id, previous_draft_id, version, draft_history,
                        status, created_time, safety_string, thread_context, message_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    draft_id,
                    draft.get('workspace'),
                    draft.get('thread_id'),
                    draft.get('message_id'),
                    draft.get('inbound_subject'),
                    draft.get('inbound_from'),
                    draft.get('inbound_to', ''),
                    draft.get('inbound_cc', ''),
                    draft.get('inbound_snippet'),
                    draft.get('inbound_date'),
                    draft.get('inbound_body'),
                    draft.get('pertains_to_me', True),
                    draft.get('is_spam', False),
                    draft.get('requires_response', True),
                    draft.get('classification_reasoning'),
                    draft.get('draft_subject'),
                    draft.get('draft_body'),
                    draft.get('draft_body_html'),
                    draft.get('response_context'),  # Already a JSON string from serialize_context_for_storage()
                    draft.get('system_prompt'),
                    draft.get('ai_model'),
                    draft.get('draft_number', 1),
                    draft.get('chat_session_id'),
                    draft.get('previous_draft_id'),
                    draft.get('version', 1),
                    draft_history,
                    draft.get('status', 'pending'),
                    draft.get('created_time', datetime.now(timezone.utc).isoformat()),
                    safety_string,
                    draft.get('thread_context'),
                    draft.get('message_count', 1)
                ))
                
                conn.commit()
                logger.info(f"✅ Saved draft {draft_id}")
                return draft_id
                
        except Exception as e:
            logger.error(f"❌ Failed to save draft: {e}")
            raise
    
    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific draft by ID."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("SELECT * FROM email_drafts WHERE draft_id = ?", (draft_id,))
                row = cursor.fetchone()
                
                if row:
                    return dict(row)
                return None
                
        except Exception as e:
            logger.error(f"❌ Failed to get draft {draft_id}: {e}")
            return None
    
    def get_pending_drafts(self, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get drafts with status='pending'."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if workspace:
                    cursor.execute(
                        "SELECT * FROM email_drafts WHERE status = 'pending' AND workspace = ? ORDER BY created_time DESC",
                        (workspace,)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM email_drafts WHERE status = 'pending' ORDER BY created_time DESC"
                    )
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Failed to get pending drafts: {e}")
            return []
    
    def get_drafts_for_workspace(
        self,
        workspace: str,
        include_resolved: bool = False,
        status_filter: Optional[List[str]] = None,
        days: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get drafts for a workspace (queue view).

        By default, excludes sent/archived messages (they go to history instead).

        Args:
            workspace: Workspace name
            include_resolved: If True, includes ALL messages including sent/archived (for display purposes)
                            If False (default), only shows pending/skipped (active queue)
            status_filter: Optional list of statuses to filter by (e.g., ['pending', 'unsure'])
            days: Optional number of days to filter by. Special handling:
                 - pending/unsure drafts are ALWAYS shown regardless of date
                 - Other statuses (skipped) are filtered to last N days
                 - If None, no date filtering is applied
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Build base query based on include_resolved
                if include_resolved:
                    base_condition = "workspace = ?"
                    params = [workspace]
                else:
                    base_condition = "workspace = ? AND status NOT IN ('sent', 'archived')"
                    params = [workspace]

                # Add status filter if provided
                if status_filter:
                    placeholders = ','.join('?' for _ in status_filter)
                    base_condition += f" AND status IN ({placeholders})"
                    params.extend(status_filter)

                # Add date filtering with special handling for pending/unsure
                if days is not None:
                    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
                    # Always show pending/unsure, filter others by date
                    base_condition += " AND (status IN ('pending', 'unsure') OR created_time >= ?)"
                    params.append(cutoff_date)

                # Order by status priority first (pending → unsure → skipped), then by date
                # This ensures important work (pending/unsure) appears before skipped emails
                query = f"""
                    SELECT * FROM email_drafts
                    WHERE {base_condition}
                    ORDER BY
                        CASE status
                            WHEN 'pending' THEN 1
                            WHEN 'unsure' THEN 2
                            WHEN 'skipped' THEN 3
                            ELSE 4
                        END ASC,
                        created_time DESC
                """
                cursor.execute(query, params)

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"❌ Failed to get drafts for workspace {workspace}: {e}")
            return []
    
    def get_history_for_workspace(self, workspace: str) -> List[Dict[str, Any]]:
        """
        Get history for a workspace (completed messages).
        
        Returns sent/archived messages ordered by completion time (most recent first).
        
        Args:
            workspace: Workspace name
            
        Returns:
            List of completed drafts ordered by completed_time DESC
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute(
                    """SELECT * FROM email_drafts 
                    WHERE workspace = ? AND status IN ('sent', 'archived') 
                    ORDER BY completed_time DESC, created_time DESC""",
                    (workspace,)
                )
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Failed to get history for workspace {workspace}: {e}")
            return []
    
    def update_draft_status(self, draft_id: str, status: str):
        """Update draft status and set completed_time for final states."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                now = datetime.now(timezone.utc).isoformat()
                
                # If moving to a final state (sent/archived), set completed_time
                if status in ['sent', 'archived']:
                    cursor.execute(
                        "UPDATE email_drafts SET status = ?, reviewed_time = ?, completed_time = ? WHERE draft_id = ?",
                        (status, now, now, draft_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE email_drafts SET status = ?, reviewed_time = ? WHERE draft_id = ?",
                        (status, now, draft_id)
                    )
                
                conn.commit()
                logger.info(f"✅ Updated draft {draft_id} status to {status}")
                
        except Exception as e:
            logger.error(f"❌ Failed to update draft status: {e}")
            raise
    
    def update_draft_body(self, draft_id: str, new_body: str, version: int = 1):
        """Update the body of a draft."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE email_drafts SET draft_body = ?, version = ? WHERE draft_id = ?",
                    (new_body, version, draft_id)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error updating draft body: {e}")

    def update_draft_body_and_subject(self, draft_id: str, draft_body: str, draft_subject: Optional[str] = None):
        """Update the body and subject of a draft."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if draft_subject is not None:
                    cursor.execute(
                        "UPDATE email_drafts SET draft_body = ?, draft_subject = ? WHERE draft_id = ?",
                        (draft_body, draft_subject, draft_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE email_drafts SET draft_body = ? WHERE draft_id = ?",
                        (draft_body, draft_id)
                    )
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error updating draft body and subject: {e}")

    def update_inbound_body(self, draft_id: str, new_body: str):
        """Update the inbound_body of a draft."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE email_drafts SET inbound_body = ? WHERE draft_id = ?",
                    (new_body, draft_id)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error updating inbound body: {e}")

    def mark_sent(self, draft_id: str):
        """Mark a draft as sent and record sent_time."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()

                cursor.execute(
                    "UPDATE email_drafts SET status = ?, sent_time = ?, completed_time = ? WHERE draft_id = ?",
                    ('sent', now, now, draft_id)
                )

                conn.commit()
                logger.info(f"✅ Marked draft {draft_id} as sent")

        except Exception as e:
            logger.error(f"❌ Failed to mark draft as sent: {e}")
            raise

    def save_chat_messages(self, draft_id: str, messages: List[Dict[str, Any]]):
        """Save chat conversation history for a draft."""
        # Validate inputs
        if not draft_id:
            logger.error("❌ Cannot save chat messages: draft_id is empty")
            raise ValueError("draft_id cannot be empty")
        
        if not isinstance(messages, list):
            logger.error(f"❌ Cannot save chat messages: messages must be a list, got {type(messages)}")
            raise TypeError(f"messages must be a list, got {type(messages)}")
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                messages_json = json.dumps(messages)

                cursor.execute(
                    "UPDATE email_drafts SET chat_messages = ? WHERE draft_id = ?",
                    (messages_json, draft_id)
                )

                rows_affected = cursor.rowcount
                conn.commit()

                if rows_affected > 0:
                    logger.info(f"✅ Saved {len(messages)} chat messages for draft {draft_id} ({len(messages_json)} bytes)")
                else:
                    logger.warning(f"⚠️  No draft found with ID {draft_id} - messages not saved!")
                    raise ValueError(f"No draft found with ID {draft_id}")

        except Exception as e:
            logger.error(f"❌ Failed to save chat messages for draft {draft_id}: {e}", exc_info=True)
            raise

    def load_chat_messages(self, draft_id: str) -> List[Dict[str, Any]]:
        """Load chat conversation history for a draft."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT chat_messages FROM email_drafts WHERE draft_id = ?",
                    (draft_id,)
                )

                row = cursor.fetchone()
                if row and row[0]:
                    return json.loads(row[0])
                return []

        except Exception as e:
            logger.error(f"❌ Failed to load chat messages: {e}")
            return []
    
    def thread_has_draft(self, thread_id: str, workspace: str) -> bool:
        """
        Check if a thread already has an active draft.

        Only returns True for drafts with status in ('pending', 'unsure', 'skipped').
        Returns False for 'sent' or 'archived' drafts, allowing new replies to be processed.

        DEPRECATED: Use message_has_draft() instead for better idempotence.
        This method kept for backward compatibility.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """SELECT COUNT(*) FROM email_drafts
                       WHERE thread_id = ? AND workspace = ?
                       AND status IN ('pending', 'unsure', 'skipped')""",
                    (thread_id, workspace)
                )

                count = cursor.fetchone()[0]
                return count > 0

        except Exception as e:
            logger.error(f"❌ Failed to check for existing draft: {e}")
            return False

    def message_has_draft(self, thread_id: str, message_id: str, workspace: str) -> bool:
        """
        Check if a specific message already has a draft (any status).

        This provides true idempotence by checking the exact message, not just the thread.
        Returns True if this EXACT message has been processed before (any status).
        Returns False only for genuinely new messages.

        Args:
            thread_id: Gmail thread ID
            message_id: Gmail message ID (last message in thread)
            workspace: Workspace name

        Returns:
            True if this exact message already has a draft (prevents duplicates)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check for ANY draft with this exact message_id
                # This prevents re-processing the same message multiple times
                cursor.execute(
                    """SELECT COUNT(*) FROM email_drafts
                       WHERE thread_id = ? AND message_id = ? AND workspace = ?""",
                    (thread_id, message_id, workspace)
                )

                count = cursor.fetchone()[0]
                exists = count > 0

                if exists:
                    logger.debug(f"Message {message_id} already has draft - skipping duplicate")

                return exists

        except Exception as e:
            logger.error(f"❌ Failed to check for existing draft: {e}")
            return False
    
    def get_draft_count_by_status(self, workspace: Optional[str] = None) -> Dict[str, int]:
        """Get count of drafts by status."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if workspace:
                    cursor.execute(
                        "SELECT status, COUNT(*) FROM email_drafts WHERE workspace = ? GROUP BY status",
                        (workspace,)
                    )
                else:
                    cursor.execute("SELECT status, COUNT(*) FROM email_drafts GROUP BY status")
                
                results = cursor.fetchall()
                return {row[0]: row[1] for row in results}
                
        except Exception as e:
            logger.error(f"❌ Failed to get draft counts: {e}")
            return {}
    
    def get_refreshable_drafts(self, workspace: str, cutoff_date: str, statuses: List[str]) -> List[Dict[str, Any]]:
        """
        Get drafts that can be refreshed.
        
        Args:
            workspace: Workspace name
            cutoff_date: ISO format date string - only get drafts created after this date
            statuses: List of statuses to include (e.g., ['pending', 'unsure', 'skipped'])
            
        Returns:
            List of draft dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Build query with dynamic status list
                placeholders = ','.join('?' for _ in statuses)
                query = f"""
                    SELECT * FROM email_drafts 
                    WHERE workspace = ? 
                    AND created_time >= ? 
                    AND status IN ({placeholders})
                    ORDER BY created_time DESC
                """
                
                params = [workspace, cutoff_date] + statuses
                cursor.execute(query, params)
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Failed to get refreshable drafts: {e}")
            return []
    
    def update_draft_refresh(
        self, 
        draft_id: str, 
        status: str,
        classification: Dict[str, Any],
        thread: Dict[str, Any],
        draft_body: str,
        draft_subject: Optional[str] = None,
        response_context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        ai_model: Optional[str] = None
    ):
        """
        Update a draft after refresh operation.
        
        Args:
            draft_id: Draft ID to update
            status: New status
            classification: Classification results
            thread: Fresh thread data from Gmail
            draft_body: New draft body
            draft_subject: Optional new draft subject
            response_context: Serialized context
            system_prompt: System prompt used
            ai_model: AI model used
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Extract thread data
                inbound_body = thread.get('conversation_body', '')
                inbound_to = thread.get('to', '')
                inbound_cc = thread.get('cc', '')
                message_count = thread.get('message_count', 1)
                
                # Update query
                cursor.execute("""
                    UPDATE email_drafts SET
                        status = ?,
                        inbound_body = ?,
                        inbound_to = ?,
                        inbound_cc = ?,
                        message_count = ?,
                        pertains_to_me = ?,
                        is_spam = ?,
                        requires_response = ?,
                        classification_reasoning = ?,
                        draft_subject = ?,
                        draft_body = ?,
                        response_context = ?,
                        system_prompt = ?,
                        ai_model = ?,
                        reviewed_time = ?
                    WHERE draft_id = ?
                """, (
                    status,
                    inbound_body,
                    inbound_to,
                    inbound_cc,
                    message_count,
                    classification.get('pertains_to_me', True),
                    classification.get('is_spam', False),
                    classification.get('requires_response', True),
                    classification.get('reasoning'),
                    draft_subject or f"Re: {thread.get('subject', 'No Subject')}",
                    draft_body,
                    response_context,
                    system_prompt,
                    ai_model,
                    datetime.now(timezone.utc).isoformat(),
                    draft_id
                ))
                
                conn.commit()
                logger.debug(f"✅ Updated draft {draft_id} after refresh")
                
        except Exception as e:
            logger.error(f"❌ Failed to update draft after refresh: {e}")
            raise

    def get_last_sync_time(self, workspace: str) -> Optional[datetime]:
        """
        Get the last sync time for a workspace.

        Args:
            workspace: Workspace name

        Returns:
            Last sync time as datetime, or None if never synced
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT last_sync_time FROM mail_sync_state WHERE workspace = ?",
                    (workspace,)
                )

                row = cursor.fetchone()
                if row:
                    # Parse ISO format datetime string
                    return datetime.fromisoformat(row[0].replace('Z', '+00:00'))
                return None

        except Exception as e:
            logger.error(f"❌ Failed to get last sync time for {workspace}: {e}")
            return None

    def update_last_sync_time(self, workspace: str, sync_time: Optional[datetime] = None):
        """
        Update the last sync time for a workspace.

        Args:
            workspace: Workspace name
            sync_time: Sync time to record (defaults to now)
        """
        if sync_time is None:
            sync_time = datetime.now(timezone.utc)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Use INSERT OR REPLACE to handle both new and existing records
                cursor.execute("""
                    INSERT OR REPLACE INTO mail_sync_state (workspace, last_sync_time, updated_at)
                    VALUES (?, ?, ?)
                """, (
                    workspace,
                    sync_time.isoformat(),
                    datetime.now(timezone.utc).isoformat()
                ))

                conn.commit()
                logger.debug(f"✅ Updated last sync time for {workspace} to {sync_time.isoformat()}")

        except Exception as e:
            logger.error(f"❌ Failed to update last sync time for {workspace}: {e}")
            raise

    def auto_archive_old_skipped_drafts(
        self,
        workspace: str,
        days_threshold: int = 30
    ) -> int:
        """
        Auto-archive skipped drafts older than threshold.

        This helps clean up old notification emails, delivery confirmations,
        and other skipped drafts that are no longer relevant.

        Args:
            workspace: Workspace name
            days_threshold: Number of days after which to archive skipped drafts (default: 30)

        Returns:
            Number of drafts archived
        """
        try:
            cutoff_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Update skipped drafts older than threshold
                cursor.execute("""
                    UPDATE email_drafts
                    SET status = 'archived',
                        completed_time = ?,
                        reviewed_time = ?
                    WHERE workspace = ?
                      AND status = 'skipped'
                      AND created_time < ?
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    workspace,
                    cutoff_date
                ))

                archived_count = cursor.rowcount
                conn.commit()

                if archived_count > 0:
                    logger.info(f"🗑️  Auto-archived {archived_count} old skipped drafts for workspace '{workspace}' (older than {days_threshold} days)")
                else:
                    logger.debug(f"No old skipped drafts to archive for workspace '{workspace}'")

                return archived_count

        except Exception as e:
            logger.error(f"❌ Failed to auto-archive old skipped drafts for {workspace}: {e}")
            return 0

