"""
Helper functions for AI-assisted email sending from chat.

Provides utilities to:
- Parse AI responses for email intent and gathered information
- Search for email threads based on user descriptions
- Create drafts from gathered information
- Launch draft chat interface
"""
import os
import json
import logging
import re
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from pathlib import Path

from promaia.mail.draft_manager import DraftManager
from promaia.storage.unified_query import get_query_interface

logger = logging.getLogger(__name__)


class EmailSendHelper:
    """Helper for AI-assisted email sending."""

    def __init__(self, workspace: str = "default"):
        self.workspace = workspace
        self.draft_manager = DraftManager()
        self.query_interface = get_query_interface()

    def search_email_threads(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for email threads matching a query string.

        Args:
            query: Search query (subject, sender, content keywords)
            limit: Maximum number of results

        Returns:
            List of matching email thread metadata
        """
        try:
            # Query Gmail content from unified storage
            results = self.query_interface.search_content(
                query=query,
                workspace=self.workspace,
                sources=['gmail']
            )

            # Deduplicate by thread_id and sort by date
            seen_threads = {}
            for result in results:
                metadata = result.get('metadata', {})
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)

                thread_id = metadata.get('thread_id')
                if thread_id and thread_id not in seen_threads:
                    seen_threads[thread_id] = {
                        'thread_id': thread_id,
                        'message_id': result.get('page_id'),
                        'subject': result.get('title', ''),
                        'from': metadata.get('from_email', metadata.get('from', '')),
                        'to': metadata.get('to', ''),
                        'date': result.get('last_edited_time', result.get('created_time', '')),
                        'snippet': metadata.get('snippet', ''),
                    }

            # Sort by date (most recent first) and limit
            threads = list(seen_threads.values())
            threads.sort(key=lambda x: x.get('date', ''), reverse=True)

            return threads[:limit]

        except Exception as e:
            logger.error(f"Error searching email threads: {e}")
            return []

    def find_recipient_email(self, name_or_email: str) -> Optional[str]:
        """
        Find recipient email address from name or partial email.

        Args:
            name_or_email: Recipient name or email address

        Returns:
            Email address if found, None otherwise
        """
        # If it looks like an email already, return it
        if '@' in name_or_email and '.' in name_or_email:
            return name_or_email.strip().lower()

        try:
            # Search recent emails for this person
            results = self.query_interface.search_content(
                query=name_or_email,
                workspace=self.workspace,
                sources=['gmail']
            )

            # Look for email addresses in from/to fields
            for result in results[:20]:  # Check first 20 results
                metadata = result.get('metadata', {})
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)

                # Check from field
                from_email = metadata.get('from_email', '')
                from_name = metadata.get('from_name', '')
                if name_or_email.lower() in from_name.lower() or name_or_email.lower() in from_email.lower():
                    if '@' in from_email:
                        return from_email.strip().lower()

                # Check to field
                to_field = metadata.get('to', '')
                if name_or_email.lower() in to_field.lower():
                    # Extract email from "Name <email@domain.com>" format
                    match = re.search(r'<([^>]+@[^>]+)>', to_field)
                    if match:
                        return match.group(1).strip().lower()
                    elif '@' in to_field:
                        return to_field.strip().lower()

            return None

        except Exception as e:
            logger.error(f"Error finding recipient email: {e}")
            return None

    def create_draft_from_info(
        self,
        recipient: str,
        subject: str,
        message_body: str,
        thread_id: Optional[str] = None,
        message_id: Optional[str] = None,
        attachments: Optional[List[str]] = None,
        context_info: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create an email draft from gathered information.

        Args:
            recipient: Recipient email address
            subject: Email subject
            message_body: Email body content
            thread_id: Gmail thread ID if replying (optional)
            message_id: Gmail message ID if replying (optional)
            attachments: List of file paths to attach (optional)
            context_info: Additional context metadata (optional)

        Returns:
            draft_id of created draft
        """
        # Generate IDs if not provided (new thread)
        if not thread_id:
            thread_id = f"new_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if not message_id:
            message_id = f"new_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        # Build draft dictionary
        draft_data = {
            'workspace': self.workspace,
            'thread_id': thread_id,
            'message_id': message_id,
            'inbound_subject': subject,
            'inbound_from': recipient,  # In this case, we're sending TO them
            'inbound_to': '',  # Will be filled by user's email
            'inbound_cc': '',
            'inbound_snippet': message_body[:200] if message_body else '',
            'inbound_date': datetime.now(timezone.utc).isoformat(),
            'inbound_body': '',  # No inbound body for new emails
            'pertains_to_me': True,
            'is_spam': False,
            'requires_response': True,
            'classification_reasoning': 'User-initiated email via /email send',
            'draft_subject': subject,
            'draft_body': message_body,
            'draft_body_html': '',
            'response_context': json.dumps(context_info or {}),
            'system_prompt': 'User-generated email draft',
            'ai_model': 'user',
            'draft_number': 1,
            'chat_session_id': None,
            'previous_draft_id': None,
            'version': 1,
            'status': 'pending',
            'created_time': datetime.now(timezone.utc).isoformat(),
            'thread_context': json.dumps({
                'recipient': recipient,
                'attachments': attachments or [],
                'created_via': 'chat_email_send',
            }),
            'message_count': 1,
        }

        # Save draft
        try:
            draft_id = self.draft_manager.save_draft(draft_data)
            logger.info(f"Created email draft {draft_id} for {recipient}")

            # Store attachment info if provided
            if attachments:
                # We'll need to handle this in the draft flow
                # For now, store in thread_context
                pass

            return draft_id

        except Exception as e:
            logger.error(f"Failed to create email draft: {e}")
            raise

    def parse_email_intent(self, user_message: str, file_paths: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Parse user message for email sending intent.

        Args:
            user_message: User's message
            file_paths: List of file paths attached to message

        Returns:
            Dictionary with parsed intent, or None if no email intent detected
        """
        # Keywords that indicate email sending intent
        send_keywords = ['send', 'email', 'forward', 'reply']

        message_lower = user_message.lower()
        has_intent = any(keyword in message_lower for keyword in send_keywords)

        if not has_intent:
            return None

        # Try to extract recipient
        recipient = None
        to_pattern = r'(?:send|email|to)\s+(?:this\s+)?(?:to\s+)?([a-zA-Z0-9._%+-]+(?:@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})?|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'
        to_match = re.search(to_pattern, user_message, re.IGNORECASE)
        if to_match:
            recipient = to_match.group(1).strip()

        # Check for attachments
        has_attachments = bool(file_paths) or any(word in message_lower for word in ['attach', 'file', 'pdf', 'doc'])

        return {
            'has_intent': True,
            'recipient': recipient,
            'attachments': file_paths or [],
            'has_attachments': has_attachments,
            'original_message': user_message,
        }

    def format_thread_list_for_display(self, threads: List[Dict[str, Any]]) -> str:
        """
        Format thread list for display to user.

        Args:
            threads: List of thread dictionaries

        Returns:
            Formatted string for display
        """
        if not threads:
            return "No matching email threads found."

        output = ["Found matching email threads:\n"]
        for i, thread in enumerate(threads, 1):
            date_str = thread.get('date', '')
            if date_str:
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    date_str = date_obj.strftime('%b %d, %Y')
                except:
                    pass

            output.append(f"{i}. {thread.get('subject', '(No subject)')}")
            output.append(f"   From: {thread.get('from', 'Unknown')}")
            output.append(f"   Date: {date_str}")
            if thread.get('snippet'):
                snippet = thread['snippet'][:80] + '...' if len(thread['snippet']) > 80 else thread['snippet']
                output.append(f"   Preview: {snippet}")
            output.append("")

        return '\n'.join(output)


def launch_draft_chat_for_email(draft_id: str, workspace: str = "default"):
    """
    Launch draft chat interface for a newly created email draft.

    Args:
        draft_id: Draft ID to open in chat
        workspace: Workspace name
    """
    try:
        from promaia.mail.draft_chat import DraftChatInterface
        from promaia.chat.modes import DraftMode
        from promaia.chat.interface import chat
        from promaia.config.databases import get_database_manager

        # Load draft data
        draft_manager = DraftManager()
        draft_data = draft_manager.get_draft(draft_id)

        if not draft_data:
            logger.error(f"Draft {draft_id} not found")
            return False

        # Get user email from workspace Gmail database
        user_email = None
        try:
            db_manager = get_database_manager()
            gmail_databases = [
                db for db in db_manager.get_workspace_databases(workspace)
                if db.source_type == "gmail"
            ]
            if gmail_databases:
                user_email = gmail_databases[0].database_id
        except Exception as e:
            logger.debug(f"Could not get user email from workspace: {e}")

        if not user_email:
            logger.warning("Could not determine user email, using fallback")
            user_email = "user@example.com"

        # Create draft mode
        mode = DraftMode(
            workspace=workspace,
            draft_id=draft_id,
            draft_data=draft_data,
            draft_manager=draft_manager,
            user_email=user_email
        )

        # Launch chat interface in draft mode
        # This will use the existing draft chat flow
        chat(
            workspace=workspace,
            mode=mode,
            draft_id=draft_id
        )

        return True

    except Exception as e:
        logger.error(f"Failed to launch draft chat: {e}")
        return False
