"""
Gmail connector implementation for Maia.

This module provides a Gmail API connector that integrates with the existing
Maia architecture for email synchronization and storage.
"""
import os
import json
import base64
import logging
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email import policy
import re

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("Gmail integration requires google-api-python-client")
    print("Install with: pip install google-api-python-client")
    raise

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

logger = logging.getLogger(__name__)

class GmailConnector(BaseConnector):
    """Gmail API connector for email synchronization.
    
    Enhanced Features:
    - Intelligent date range chunking for large syncs
    - Batch processing with rate limit protection  
    - Exponential backoff retry logic for reliability
    - Removes artificial sync limits (was capped at 100 emails)
    - Complete email coverage for specified date ranges
    - Smart content extraction with concise mode (default)
    
    Configuration Options:
    - max_threads_per_batch (default: 10): Number of threads to process per batch
    - chunk_size_days (default: 15): Days per chunk for large date ranges
    - max_retry_attempts (default: 5): Maximum retry attempts for failed requests
    - gmail_content_mode (default: "latest_only"): Content extraction mode
        * "latest_only": Only latest message (much more concise, recommended)
        * "full_thread": All messages (legacy behavior, can be very verbose)
    
    Usage Examples:
    # Sync all emails from March 1st to June 30th, 2025:
    maia database sync --source acme.gmail --start-date 2025-03-01 --end-date 2025-06-30

    # Sync last 30 days (no artificial limits):
    maia database sync --source acme.gmail --days 30
    
    # Enable full thread mode for complete email history (verbose):
    Configure gmail_content_mode: "full_thread" in database config
    
    # For very large date ranges, the system automatically:
    # 1. Breaks the range into 15-day chunks (configurable)
    # 2. Processes threads in batches of 10 (configurable)  
    # 3. Handles rate limits with exponential backoff
    # 4. Provides progress reporting
    # 5. Uses concise content extraction to reduce token usage
    """
    
    # Gmail API scopes
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send'  # Required for maia mail
    ]
    
    # Rate limiting and batching configuration
    MAX_THREADS_PER_BATCH = 10  # More conservative to avoid rate limits and improve reliability
    MAX_RETRY_ATTEMPTS = 5
    BASE_RETRY_DELAY = 1.0  # seconds
    MAX_RETRY_DELAY = 32.0  # seconds
    RATE_LIMIT_RETRY_DELAY = 2.0  # seconds for 429 errors
    CHUNK_SIZE_DAYS = 15  # Break large date ranges into smaller chunks

    # Content extraction configuration
    GMAIL_CONTENT_MODE_LATEST_ONLY = "latest_only"  # Only latest message content
    GMAIL_CONTENT_MODE_FULL_THREAD = "full_thread"  # All messages (old behavior)
    DEFAULT_CONTENT_MODE = GMAIL_CONTENT_MODE_FULL_THREAD  # Default to full thread for Maia Mail
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.email = config.get("database_id")  # Use email as database_id
        self.workspace = config.get("workspace")

        # Override default batching configuration from config if provided
        self.max_threads_per_batch = config.get("max_threads_per_batch", self.MAX_THREADS_PER_BATCH)
        self.chunk_size_days = config.get("chunk_size_days", self.CHUNK_SIZE_DAYS)
        self.max_retry_attempts = config.get("max_retry_attempts", self.MAX_RETRY_ATTEMPTS)

        self.service = None

    def _get_raw_thread_id(self, thread_id: str) -> str:
        """Strip internal 'thread_' prefix to get raw Gmail thread ID.

        Gmail API expects raw hexadecimal thread IDs without any prefix.
        Our internal storage adds 'thread_' prefix for page_id purposes.

        Args:
            thread_id: Thread ID (may be prefixed with 'thread_')

        Returns:
            Raw thread ID without prefix
        """
        if not thread_id:
            return thread_id
        return thread_id.replace('thread_', '', 1) if thread_id.startswith('thread_') else thread_id

    async def connect(self, allow_interactive=False) -> bool:
        """Establish connection to Gmail API.
        
        Args:
            allow_interactive: If False, won't prompt for authentication (default: False)
        """
        try:
            self.service = await self._get_authenticated_service(allow_interactive=allow_interactive)
            return self.service is not None
        except Exception as e:
            self.logger.error(f"Failed to connect to Gmail: {e}")
            return False
    
    async def test_connection(self) -> bool:
        """Test if the Gmail connection is working."""
        if not self.service:
            if not await self.connect():
                return False
        
        try:
            # Test with a simple profile request
            profile = self.service.users().getProfile(userId='me').execute()
            self.logger.info(f"Connected to Gmail for {profile.get('emailAddress')}")
            return True
        except Exception as e:
            self.logger.error(f"Gmail connection test failed: {e}")
            return False
    
    def get_user_email(self) -> Optional[str]:
        """Get the authenticated user's email address."""
        try:
            if not self.service:
                return None
            profile = self.service.users().getProfile(userId='me').execute()
            return profile.get('emailAddress')
        except Exception as e:
            self.logger.error(f"Failed to get user email: {e}")
            return None
    
    async def _get_authenticated_service(self, allow_interactive=False):
        """Get authenticated Gmail service via the auth module.

        Args:
            allow_interactive: Unused (kept for interface compatibility).
        """
        from promaia.auth.registry import get_integration

        google_int = get_integration("google")
        creds = google_int.get_google_credentials(account=self.email)
        if creds:
            return build('gmail', 'v1', credentials=creds)

        raise ValueError(
            f"Google not configured for {self.email}. "
            f"Run: maia auth configure google --account {self.email}"
        )
    
    async def _retry_with_backoff(self, func, *args, **kwargs):
        """Execute a function with exponential backoff retry logic for rate limits."""
        for attempt in range(self.max_retry_attempts):
            try:
                return func(*args, **kwargs)
            except HttpError as e:
                if e.resp.status == 429:  # Rate limit exceeded
                    if attempt < self.max_retry_attempts - 1:
                        delay = min(self.RATE_LIMIT_RETRY_DELAY * (2 ** attempt), self.MAX_RETRY_DELAY)
                        self.logger.warning(f"Rate limit hit, retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retry_attempts})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger.error(f"Rate limit exceeded, max retries reached")
                        raise
                elif e.resp.status >= 500:  # Server errors
                    if attempt < self.max_retry_attempts - 1:
                        delay = min(self.BASE_RETRY_DELAY * (2 ** attempt), self.MAX_RETRY_DELAY)
                        self.logger.warning(f"Server error {e.resp.status}, retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retry_attempts})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger.error(f"Server error {e.resp.status}, max retries reached")
                        raise
                else:
                    # Other HTTP errors, don't retry
                    raise
            except Exception as e:
                # Non-HTTP errors, don't retry
                raise
        
        # Should never reach here, but just in case
        raise Exception("Unexpected retry loop exit")
    
    async def _get_thread_data_batch(self, thread_ids: List[str]) -> List[Optional[Dict[str, Any]]]:
        """Get thread data in batches with rate limiting."""
        results = []
        
        for thread_id in thread_ids:
            try:
                thread_data = await self._retry_with_backoff(
                    lambda: self.service.users().threads().get(
                        userId='me', 
                        id=thread_id,
                        format='full'
                    ).execute()
                )
                
                if thread_data:
                    processed_data = self._process_thread_data(thread_data)
                    if processed_data:
                        results.append(processed_data)
                
                # Small delay between individual thread requests to be respectful
                await asyncio.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"Failed to get thread data for {thread_id}: {e}")
                results.append(None)
        
        return [r for r in results if r is not None]
    
    def _process_thread_data(self, thread: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process raw thread data from Gmail API into standardized format."""
        try:
            messages = thread.get('messages', [])
            if not messages:
                return None
            
            thread_id = thread.get('id')
            
            # Sort messages by date (oldest first for conversation flow)
            messages.sort(key=lambda m: int(m.get('internalDate', 0)))
            
            # Use the latest message for thread metadata
            latest_message = messages[-1]
            
            # Extract thread-level metadata from latest message
            headers = {h['name'].lower(): h['value'] 
                      for h in latest_message.get('payload', {}).get('headers', [])}
            
            subject = headers.get('subject', 'No Subject')
            from_addr = headers.get('from', 'Unknown')
            to_addr = headers.get('to', '')
            cc_addr = headers.get('cc', '')
            date_str = headers.get('date', '')
            
            # Parse date
            try:
                # Gmail provides date in various formats, try to parse
                from email.utils import parsedate_to_datetime
                date_obj = parsedate_to_datetime(date_str)
                if date_obj.tzinfo is None:
                    date_obj = date_obj.replace(tzinfo=timezone.utc)
            except Exception:
                date_obj = datetime.now(timezone.utc)
            
            # Extract labels from latest message
            labels = latest_message.get('labelIds', [])

            # Extract attachment metadata from all messages in thread
            all_attachments = []
            for msg in messages:
                msg_attachments = self._extract_attachment_metadata(msg)
                if msg_attachments:
                    # Add message_id to each attachment for reference
                    for attachment in msg_attachments:
                        attachment['message_id'] = msg['id']
                    all_attachments.extend(msg_attachments)

            # Keep has_attachments boolean for backwards compatibility
            has_attachments = len(all_attachments) > 0

            # Check if thread has unread messages
            is_unread = 'UNREAD' in labels
            
            # Generate conversation body by combining all messages
            conversation_body = self._extract_thread_conversation(messages)
            
            # Check if the last message was sent by the user (not inbound)
            # Get the user's email and check if it matches the FROM address
            user_email = self.get_user_email()
            last_message_from_user = False
            if user_email:
                # Extract just the email address from "Name <email>" format
                from_email = from_addr
                if '<' in from_addr and '>' in from_addr:
                    from_email = from_addr.split('<')[1].split('>')[0].strip().lower()
                else:
                    from_email = from_addr.strip().lower()
                
                last_message_from_user = from_email == user_email.lower()
            
            return {
                "id": f"thread_{thread_id}",
                "thread_id": thread_id,
                "message_ids": [msg['id'] for msg in messages],
                "subject": subject,
                "from": from_addr,
                "to": to_addr,
                "cc": cc_addr,
                "date": date_obj.isoformat(),
                "date_obj": date_obj,
                "labels": labels,
                "has_attachments": has_attachments,
                "attachments": all_attachments,
                "is_unread": is_unread,
                "message_count": len(messages),
                "conversation_body": conversation_body,
                "internal_date": latest_message.get('internalDate'),
                "snippet": latest_message.get('snippet', ''),
                "messages": messages,  # Store full message data for detailed processing
                "body_html": self._get_latest_html_body(messages),
                "last_message_from_user": last_message_from_user  # Flag to filter out user's own messages
            }
            
        except Exception as e:
            self.logger.error(f"Failed to process thread data: {e}")
            return None
    
    def _chunk_date_range(self, date_filter: DateRangeFilter) -> List[DateRangeFilter]:
        """Break large date ranges into smaller chunks to avoid API timeouts."""
        if not date_filter or not date_filter.start_date:
            return [date_filter] if date_filter else []
        
        # For incremental syncs (no end_date), don't chunk - just return the original filter
        # This preserves the incremental nature and avoids setting an artificial end_date
        if not date_filter.end_date:
            self.logger.debug("Incremental sync detected (no end_date), skipping date chunking")
            return [date_filter]
        
        chunks = []
        current_start = date_filter.start_date
        end_date = date_filter.end_date  # Don't default to now() - respect the original filter
        
        while current_start < end_date:
            chunk_end = min(current_start + timedelta(days=self.chunk_size_days), end_date)
            
            chunk_filter = DateRangeFilter(
                property_name=date_filter.property_name,
                start_date=current_start,
                end_date=chunk_end
            )
            chunks.append(chunk_filter)
            
            current_start = chunk_end
        
        return chunks
    
    async def get_database_schema(self) -> Dict[str, Any]:
        """Get the schema/properties for Gmail emails."""
        return {
            "from": {"type": "email", "description": "Sender email address"},
            "to": {"type": "email", "description": "Recipient email addresses"},
            "subject": {"type": "text", "description": "Email subject"},
            "date": {"type": "date", "description": "Email date"},
            "labels": {"type": "multi_select", "description": "Gmail labels"},
            "thread_id": {"type": "text", "description": "Gmail thread ID"},
            "message_id": {"type": "text", "description": "Gmail message ID"},
            "has_attachments": {"type": "checkbox", "description": "Has attachments"},
            "is_unread": {"type": "checkbox", "description": "Is unread"},
            "body_snippet": {"type": "text", "description": "Email body preview"},
            "snippet": {"type": "text", "description": "Email snippet/preview"},
            "body_html": {"type": "text", "description": "Email HTML body"}
        }
    
    async def query_pages(self, 
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query emails from Gmail with intelligent chunking and batching."""
        if not self.service:
            # Don't allow interactive auth during automated syncs
            connected = await self.connect(allow_interactive=False)
            if not connected:
                self.logger.error("Failed to connect to Gmail - authentication required")
                return []
        
        try:
            all_email_data = []
            all_messages = []  # Collect all messages first, then deduplicate threads
            
            # Break large date ranges into smaller chunks
            date_chunks = self._chunk_date_range(date_filter) if date_filter else [None]
            
            total_chunks = len(date_chunks)
            if total_chunks > 1:
                self.logger.info(f"Breaking sync into {total_chunks} date chunks for better reliability")
            
            for chunk_idx, chunk_filter in enumerate(date_chunks):
                if total_chunks > 1:
                    if chunk_filter and chunk_filter.start_date and chunk_filter.end_date:
                        start_str = chunk_filter.start_date.strftime('%Y-%m-%d')
                        end_str = chunk_filter.end_date.strftime('%Y-%m-%d')
                        self.logger.info(f"Processing chunk {chunk_idx + 1}/{total_chunks}: {start_str} to {end_str}")
                
                # Build Gmail search query for this chunk
                query = self._build_gmail_query(filters, chunk_filter)
                self.logger.info(f"Gmail query: {query}")
                
                # Search for messages with pagination and retry logic
                messages = []
                page_token = None
                
                while True:
                    try:
                        result = await self._retry_with_backoff(
                            lambda: self.service.users().messages().list(
                                userId='me',
                                q=query,
                                maxResults=100,  # Max allowed by API
                                pageToken=page_token
                            ).execute()
                        )
                        
                        batch_messages = result.get('messages', [])
                        messages.extend(batch_messages)
                        page_token = result.get('nextPageToken')
                        
                        if not page_token:
                            break
                            
                        # Respect limit across all chunks
                        if limit and len(all_email_data) + len(messages) >= limit:
                            messages = messages[:limit - len(all_email_data)]
                            break
                            
                    except HttpError as e:
                        if e.resp.status == 500:
                            # Gmail backend error - try simplified query
                            self.logger.warning(f"Gmail API 500 error with query: {query}")
                            self.logger.info("Trying simplified query without category filters...")
                            
                            # Fallback to basic query
                            simple_query = self._build_simple_gmail_query(chunk_filter)
                            self.logger.info(f"Simplified Gmail query: {simple_query}")
                            
                            result = await self._retry_with_backoff(
                                lambda: self.service.users().messages().list(
                                userId='me',
                                q=simple_query,
                                maxResults=100,
                                pageToken=page_token
                            ).execute()
                            )
                            
                            batch_messages = result.get('messages', [])
                            messages.extend(batch_messages)
                            page_token = result.get('nextPageToken')
                            
                            if not page_token:
                                break
                        else:
                            raise
                
                if not messages:
                    if total_chunks > 1:
                        self.logger.info(f"No messages found in chunk {chunk_idx + 1}/{total_chunks}")
                    continue
                
                chunk_message_count = len(messages)
                self.logger.info(f"Found {chunk_message_count} messages in this chunk")

                # Add messages to the overall collection (we'll process all threads at the end)
                all_messages.extend(messages)
                
                # Stop if we've hit the limit
                if limit and len(all_messages) >= limit:
                    all_messages = all_messages[:limit]
                    break
            
            # Group ALL messages by thread to reduce API calls and avoid duplicates
            threads = {}
            for msg in all_messages:
                thread_id = msg.get('threadId')
                if thread_id not in threads:
                    threads[thread_id] = []
                threads[thread_id].append(msg['id'])
            
            thread_ids = list(threads.keys())
            thread_count = len(thread_ids)
            
            # Log deduplication results
            if len(all_messages) > thread_count:
                duplicates_removed = len(all_messages) - thread_count
                self.logger.info(f"Deduplicated {duplicates_removed} duplicate messages across {thread_count} unique threads")
            
            self.logger.info(f"Processing {thread_count} unique threads from {len(all_messages)} total messages")
            
            # Process threads in batches to respect rate limits
            for batch_start in range(0, thread_count, self.max_threads_per_batch):
                batch_end = min(batch_start + self.max_threads_per_batch, thread_count)
                batch_thread_ids = thread_ids[batch_start:batch_end]
                
                self.logger.debug(f"Processing thread batch {batch_start // self.max_threads_per_batch + 1} "
                                f"({len(batch_thread_ids)} threads)")
                
                # Get thread data for this batch
                batch_threads = await self._get_thread_data_batch(batch_thread_ids)
                all_email_data.extend(batch_threads)
        
                # Respect limit
                if limit and len(all_email_data) >= limit:
                    all_email_data = all_email_data[:limit]
                    break
            
            self.logger.info(f"Gmail sync complete. Found {len(all_email_data)} total email threads.")
            return all_email_data
            
        except Exception as e:
            self.logger.error(f"Failed to query Gmail messages: {e}")
            return []
    
    async def _get_thread_data(self, thread_id: str, message_ids: List[str]) -> Optional[Dict[str, Any]]:
        """DEPRECATED: Use _get_thread_data_batch instead. Kept for backward compatibility."""
        self.logger.warning("_get_thread_data is deprecated, use batch processing instead")
        batch_result = await self._get_thread_data_batch([thread_id])
        return batch_result[0] if batch_result else None
    
    def _get_latest_html_body(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Extracts the HTML body from the latest message."""
        if not messages:
            return None
        
        try:
            latest_message = sorted(messages, key=lambda m: int(m.get('internalDate', 0)))[-1]
            return self._extract_html_from_payload(latest_message.get('payload', {}))
        except Exception:
            return None
    
    def _extract_thread_conversation(self, messages: List[Dict[str, Any]]) -> str:
        """Extract conversation text from messages in a thread.
        
        Supports two modes:
        - latest_only: Only the latest message (default, much more concise)
        - full_thread: All messages (legacy behavior, can be very verbose)
        """
        if not messages:
            return ""
        
        # Sort messages by date to ensure chronological order (oldest first)
        sorted_messages = sorted(messages, key=lambda m: int(m.get('internalDate', 0)))
        
        # Get content mode from config
        content_mode = self.config.get('gmail_content_mode', self.DEFAULT_CONTENT_MODE)
        
        if content_mode == self.GMAIL_CONTENT_MODE_LATEST_ONLY:
            # Only extract the latest message for concise output
            self.logger.debug(f"Extracting latest message only from {len(messages)} messages (concise mode)")
            return self._extract_latest_message_only(sorted_messages)
        else:
            # Legacy behavior: extract all messages (can be very verbose)
            self.logger.debug(f"Extracting complete conversation from {len(messages)} messages (full thread mode)")
        return self._extract_individual_messages(sorted_messages)
    
    def _extract_latest_message_only(self, messages: List[Dict[str, Any]]) -> str:
        """Extract only the latest message content for concise email threads."""
        if not messages:
            return ""
        
        # Get the latest message
        latest_message = messages[-1]
        
        headers = {h['name'].lower(): h['value'] 
                  for h in latest_message.get('payload', {}).get('headers', [])}
        
        from_addr = headers.get('from', 'Unknown')
        to_addr = headers.get('to', '')
        date_str = headers.get('date', '')
        subject = headers.get('subject', '')
        
        # Extract the message body
        body = self._extract_message_body(latest_message)
        
        # Use snippet as fallback if body is empty
        raw_content = body.strip() if body.strip() else latest_message.get('snippet', '')
        
        # Extract only new content (strip quoted/forwarded parts)
        content = self._extract_new_content(raw_content)
        
        # If we have multiple messages, add a summary header
        message_count = len(messages)
        if message_count > 1:
            thread_summary = f"**Thread Summary:** {message_count} messages in this conversation. Showing latest message only.\n\n"
        else:
            thread_summary = ""
        
        # Format with essential headers
        formatted_message = f"""{thread_summary}From: {from_addr}
Sent: {date_str}
To: {to_addr}
Subject: {subject}

{content}"""
        
        return formatted_message.strip()
    
    def _extract_individual_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Extract and format all individual messages in a thread chronologically."""
        if not messages:
            return ""
            
        conversation_parts = []
        
        for i, message in enumerate(messages):
            headers = {h['name'].lower(): h['value'] 
                      for h in message.get('payload', {}).get('headers', [])}
            
            from_addr = headers.get('from', 'Unknown')
            to_addr = headers.get('to', '')
            date_str = headers.get('date', '')
            subject = headers.get('subject', '')
            
            # Extract the message body
            body = self._extract_message_body(message)
            
            # Use snippet as fallback if body is empty
            raw_content = body.strip() if body.strip() else message.get('snippet', '')
            
            # Extract only new content (strip quoted/forwarded parts)
            content = self._extract_new_content(raw_content)

            # Add a separator between messages
            if i > 0:
                conversation_parts.append("\n" + "─" * 80 + "\n")

            conversation_parts.append(f"""From: {from_addr}
Date: {date_str}
Subject: {subject}

{content}""")
        
        return "\n".join(conversation_parts).strip()
    
    def _extract_new_content(self, content: str) -> str:
        """Extract only the new content from an email, stripping quoted/forwarded parts."""
        if not content:
            return ""
        
        original_length = len(content)
        
        # First, try to split inline quotes using multiple regex patterns
        inline_quote_patterns = [
            # Gmail format: "On [date] at [time] [sender] <email> wrote:"
            r'\s+On\s+[A-Za-z]+,\s+[A-Za-z]+\s+\d+,\s+202\d\s+at\s+\d+:\d+\s+(AM|PM)\s+[^<]+<[^>]+>\s+wrote:',
            # Outlook format: "From: [sender] Sent: [date] To: [recipient]"
            r'\s+From:\s+[^<\n]+<[^>]+>\s+Sent:\s+[A-Za-z]+\s+\d+,\s+202\d',
            # Simple Outlook: "From: [sender] Sent:"
            r'\s+From:\s+[^\n]+\s+Sent:\s+[A-Za-z]',
            # Original message marker
            r'\s+-----Original Message-----',
            # Generic "From:" header in quotes
            r'\s+From:\s+[^\n]*@[^\n]*\s+(Sent|Date):',
        ]
        
        for pattern in inline_quote_patterns:
            match = re.search(pattern, content)
            if match:
                # Split at the quote and return only the part before it
                clean_content = content[:match.start()].strip()
                self.logger.debug(f"Inline quote detected at position {match.start()}/{original_length}, reduced content from {original_length} to {len(clean_content)} chars")
                return clean_content
        
        # Fallback to line-by-line processing for other quote formats
        lines = content.split('\n')
        new_content_lines = []
        
        for line in lines:
            line_stripped = line.strip()
            
            # Skip empty lines at the start
            if not new_content_lines and not line_stripped:
                continue
            
            # Enhanced quote indicators - be more aggressive at stopping
            line_lower = line_stripped.lower()
            
            if (line_stripped.startswith('>') or 
                # Gmail quote patterns - more comprehensive
                (line_stripped.startswith('On ') and (
                    'wrote:' in line_stripped or 'sent:' in line_lower or 
                    ('at ' in line_stripped and ('AM' in line_stripped or 'PM' in line_stripped) and '<' in line_stripped)
                )) or
                line_stripped.startswith('From:') and '@' in line_stripped or
                line_stripped.startswith('-----Original Message-----') or
                line_stripped.startswith('________________________________') or
                # More flexible dash patterns for quoted content
                (line_stripped.startswith('---') and (' on ' in line_lower or ' On ' in line_stripped)) or
                (line_stripped.startswith('----') and (' on ' in line_lower or ' On ' in line_stripped)) or
                # Common email quote patterns
                'Begin forwarded message:' in line_stripped or
                line_stripped.startswith('Sent from ') or
                (line_stripped.startswith('*From:*') and '@' in line_stripped) or
                # Gmail-specific patterns
                'EXTERNAL EMAIL' in line_stripped or
                'This email was sent by a person from outside your organization' in line_stripped or
                'Exercise caution when clicking links' in line_stripped or
                'CAUTION: This email originated from outside' in line_stripped or
                # Email header patterns (be careful not to catch legitimate headers at start)
                (line_stripped.startswith('To:') and '@' in line_stripped and len(new_content_lines) > 3) or
                (line_stripped.startswith('Sent:') and ('AM' in line_stripped or 'PM' in line_stripped) and len(new_content_lines) > 2) or
                (line_stripped.startswith('Subject: Re:') and len(new_content_lines) > 2) or
                (line_stripped.startswith('Subject: Fwd:') and len(new_content_lines) > 2) or
                # Stop at quoted content patterns
                line_stripped.startswith('< ') or  # Some email clients use this
                (len(line_stripped) > 0 and line_stripped[0] in '|' and line_stripped.count('|') > 2) or
                # More aggressive email thread patterns
                (' wrote:' in line_lower and '@' in line_stripped) or
                (' said:' in line_lower and '@' in line_stripped) or
                # Additional Gmail quote patterns
                ('On ' in line_stripped and ', 202' in line_stripped and ' at ' in line_stripped and ('AM' in line_stripped or 'PM' in line_stripped) and len(new_content_lines) > 2)):
                break
                
            # Stop at email signatures (common patterns)
            if (line_stripped.startswith('--') and len(line_stripped) <= 4 or
                'unsubscribe' in line_stripped.lower() or
                'privacy policy' in line_stripped.lower() or
                'terms and conditions' in line_stripped.lower() or
                line_stripped.startswith('This e-mail message may contain confidential') or
                'confidential and proprietary' in line_stripped.lower() or
                'Best regards,' in line_stripped or
                'Thanks,' in line_stripped and len(line_stripped) < 10):
                break
            
            new_content_lines.append(line)
        
        # Clean up the result
        result = '\n'.join(new_content_lines).strip()
        
        # Additional cleanup: remove excessive whitespace and empty lines at the end
        result_lines = result.split('\n')
        # Remove trailing empty lines
        while result_lines and not result_lines[-1].strip():
            result_lines.pop()
        result = '\n'.join(result_lines)
        
        # If we stripped everything, return a meaningful fallback
        if not result and content:
            # Take first reasonable chunk before any quote indicators
            first_chunk = content[:500].strip()
            self.logger.warning(f"Content extraction stripped everything, using fallback (500 chars from {original_length})")
            return first_chunk if first_chunk else "[Message content could not be extracted]"
        
        if len(result) < original_length * 0.5:
            self.logger.debug(f"Content significantly reduced: {original_length} -> {len(result)} chars ({len(result)/original_length*100:.1f}%)")
        
        return result
    
    def _extract_message_body(self, message: Dict[str, Any]) -> str:
        """Extract readable text from a Gmail message."""
        payload = message.get('payload', {})
        
        # Recursively extract text from message parts
        text_content = self._extract_text_from_payload(payload)
        
        return text_content.strip()
    
    def _extract_text_from_payload(self, payload: Dict[str, Any]) -> str:
        """Recursively extract text content from a message payload."""
        mime_type = payload.get('mimeType', '')
        
        # Handle multipart content first (recursively)
        if mime_type.startswith('multipart/'):
            parts = payload.get('parts', [])
            
            # Prefer text/html over text/plain in multipart
            html_part = None
            text_part = None

            for part in parts:
                part_mime_type = part.get('mimeType', '')
                if part_mime_type == 'text/html':
                    html_part = part
                elif part_mime_type == 'text/plain':
                    text_part = part
                
                # Handle nested multipart (e.g., multipart/alternative inside multipart/mixed)
                if part_mime_type.startswith('multipart/'):
                    nested_content = self._extract_text_from_payload(part)
                    if nested_content:
                        return nested_content

            if html_part:
                return self._extract_text_from_payload(html_part)
            if text_part:
                return self._extract_text_from_payload(text_part)

            return "" # No usable text/html or text/plain part found

        # Handle direct text content
        if mime_type == 'text/plain':
            data = payload.get('body', {}).get('data', '')
            if data:
                try:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                except Exception as e:
                    self.logger.warning(f"Failed to decode text/plain data: {e}")
                    return ''
        
        elif mime_type == 'text/html':
            data = payload.get('body', {}).get('data', '')
            if data:
                try:
                    html_content = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    # Better HTML to text conversion that preserves line structure
                    import html
                    text = re.sub('<style.*?</style>', '', html_content, flags=re.DOTALL)
                    text = re.sub('<script.*?</script>', '', text, flags=re.DOTALL)
                    
                    # Convert block-level elements to newlines BEFORE stripping tags
                    text = re.sub(r'</?(br|BR)[^>]*>', '\n', text)
                    text = re.sub(r'</?(p|P|div|DIV|h[1-6]|H[1-6]|li|LI|tr|TR)[^>]*>', '\n', text)
                    
                    # Now remove remaining HTML tags
                    text = re.sub('<[^<]+?>', ' ', text)
                    
                    # Collapse multiple spaces on same line, but preserve newlines
                    text = re.sub(r'[ \t]+', ' ', text)  # Collapse spaces/tabs but not newlines
                    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Collapse multiple newlines to max 2
                    
                    return html.unescape(text).strip()
                except Exception as e:
                    self.logger.warning(f"Failed to decode text/html data: {e}")
                    return ''
        
        # For other MIME types (attachments, etc.), return empty string
        return ''
    
    def _extract_html_from_payload(self, payload: Dict[str, Any]) -> Optional[str]:
        """Recursively extracts the first available HTML content from a message payload."""
        mime_type = payload.get('mimeType', '')

        if mime_type == 'text/html':
            data = payload.get('body', {}).get('data', '')
            if data:
                try:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                except Exception as e:
                    self.logger.warning(f"Failed to decode text/html data for body: {e}")
                    return None
        
        if mime_type.startswith('multipart/'):
            parts = payload.get('parts', [])
            for part in parts:
                html_content = self._extract_html_from_payload(part)
                if html_content:
                    return html_content
        
        return None
    
    def _message_has_attachments(self, message: Dict[str, Any]) -> bool:
        """Check if a message has attachments."""
        payload = message.get('payload', {})

        # Check parts for attachments
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('filename'):
                    return True
                # Recursive check for nested parts (e.g. multipart/mixed)
                if 'parts' in part:
                    if self._message_has_attachments(part):
                        return True
        return False

    def _extract_attachment_metadata(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract detailed attachment metadata from a message.

        Args:
            message: Gmail message dict with payload

        Returns:
            List of attachment metadata dicts with keys:
                - filename: Name of the attachment file
                - mime_type: MIME type (e.g., 'application/pdf')
                - size_bytes: Size in bytes
                - attachment_id: Gmail's attachment ID for downloading
                - part_id: Part identifier in the message structure
        """
        attachments = []

        def extract_from_part(part: Dict[str, Any], part_id: str = ""):
            """Recursively extract attachments from message parts."""
            filename = part.get('filename', '')

            # If this part has a filename, it's an attachment
            if filename:
                body = part.get('body', {})
                attachment_info = {
                    'filename': filename,
                    'mime_type': part.get('mimeType', 'application/octet-stream'),
                    'size_bytes': body.get('size', 0),
                    'attachment_id': body.get('attachmentId', ''),
                    'part_id': part.get('partId', part_id)
                }
                attachments.append(attachment_info)

            # Recursively check nested parts
            if 'parts' in part:
                for idx, nested_part in enumerate(part['parts']):
                    nested_part_id = f"{part_id}.{idx}" if part_id else str(idx)
                    extract_from_part(nested_part, nested_part_id)

        # Start extraction from message payload
        payload = message.get('payload', {})
        if 'parts' in payload:
            for idx, part in enumerate(payload['parts']):
                extract_from_part(part, str(idx))

        return attachments

    def _build_gmail_query(self, 
                          filters: Optional[List[QueryFilter]] = None,
                          date_filter: Optional[DateRangeFilter] = None) -> str:
        """Build Gmail search query from filters."""
        query_parts = []
        
        # Add property filters
        if filters:
            for filter_obj in filters:
                gmail_query = self._query_filter_to_gmail(filter_obj)
                if gmail_query:
                    query_parts.append(gmail_query)
        
        # Add date filter
        if date_filter:
            date_query = self._date_filter_to_gmail(date_filter)
            if date_query:
                query_parts.append(date_query)
        
        # Default filters - allow overriding from config
        # Simplified query to avoid Gmail API 500 errors
        query_parts.append('in:inbox')
        
        # Add category exclusions only if explicitly configured 
        excluded_categories = self.config.get('gmail_query_exclude_categories', ['promotions', 'social', 'forums'])
        for category in excluded_categories:
            query_parts.append(f'-category:{category}')
        
        # Basic exclusions that are generally safe
        query_parts.append('-in:spam')
        query_parts.append('-in:trash')
        
        # Get label filters from config
        label_filters = self.config.get('property_filters', {}).get('label')
        if label_filters:
            if isinstance(label_filters, list):
                for label in label_filters:
                    query_parts.append(f'label:{label}')
            else:
                query_parts.append(f'label:{label_filters}')
        
        return ' '.join(query_parts)
    
    def _build_simple_gmail_query(self, date_filter: Optional[DateRangeFilter] = None) -> str:
        """Build a simplified Gmail search query for fallback when complex queries fail."""
        query_parts = []
        
        # Add date filter
        if date_filter:
            date_query = self._date_filter_to_gmail(date_filter)
            if date_query:
                query_parts.append(date_query)
        
        # Only basic filters to avoid API errors
        query_parts.append('in:inbox')
        query_parts.append('-in:spam')
        query_parts.append('-in:trash')
        
        return ' '.join(query_parts)
    
    def _query_filter_to_gmail(self, query_filter: QueryFilter) -> Optional[str]:
        """Convert QueryFilter to Gmail search syntax."""
        prop_name = query_filter.property_name.lower()
        operator = query_filter.operator
        value = query_filter.value
        
        if prop_name == 'from' and operator == 'eq':
            return f'from:{value}'
        elif prop_name == 'to' and operator == 'eq':
            return f'to:{value}'
        elif prop_name == 'subject' and operator == 'contains':
            return f'subject:{value}'
        elif prop_name == 'label' and operator == 'eq':
            return f'label:{value}'
        elif prop_name == 'is_unread' and operator == 'eq':
            return 'is:unread' if value else 'is:read'
        elif prop_name == 'has_attachments' and operator == 'eq':
            return 'has:attachment' if value else '-has:attachment'
        elif operator == 'contains':
            # Generic contains operator for full email content search
            # Use Gmail's quoted search syntax to search body, subject, and other content
            return f'"{value}"'
        
        return None
    
    def _date_filter_to_gmail(self, date_filter: DateRangeFilter) -> Optional[str]:
        """Convert DateRangeFilter to Gmail search syntax.
        
        Gmail's after:/before: syntax interprets dates in the account's local timezone,
        so we need to convert UTC dates to local timezone before formatting.
        
        For incremental syncs (start_date only), we use newer_than: which finds threads
        with any activity in the specified timeframe, not just threads that started then.
        """
        from promaia.utils.timezone_utils import to_local
        
        query_parts = []
        
        # Check if this looks like an incremental sync (start_date only, no end_date)
        is_incremental_sync = date_filter.start_date and not date_filter.end_date
        
        if date_filter.start_date:
            # Convert UTC date to local timezone before formatting for Gmail
            local_date = to_local(date_filter.start_date)
            
            if is_incremental_sync:
                # For incremental syncs, use after: with the exact date
                # This is more precise than newer_than and avoids duplicate criteria
                date_str = local_date.strftime('%Y/%m/%d')
                query_parts.append(f'after:{date_str}')
                
                self.logger.debug(f"Incremental Gmail sync: using after:{date_str}")
            else:
                # For date range syncs, use standard after: syntax
                date_str = local_date.strftime('%Y/%m/%d')
                query_parts.append(f'after:{date_str}')
                self.logger.debug(f"Gmail date range sync: using after:{date_str}")
        
        if date_filter.end_date:
            # Convert UTC date to local timezone before formatting for Gmail
            local_date = to_local(date_filter.end_date)
            date_str = local_date.strftime('%Y/%m/%d')
            query_parts.append(f'before:{date_str}')
            self.logger.debug(f"Gmail date range sync: using before:{date_str}")
        
        return ' '.join(query_parts) if query_parts else None
    
    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full content of a specific email thread."""
        # page_id format: "thread_{thread_id}"
        thread_id = page_id.replace('thread_', '')
        
        try:
            thread_data = await self._get_thread_data_batch([thread_id])
            return thread_data[0] if thread_data else {}
        except Exception as e:
            self.logger.error(f"Failed to get email thread content for {page_id}: {e}")
            return {}
    
    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get properties of a specific email thread."""
        content = await self.get_page_content(page_id, include_properties=True)
        
        return {
            "from": content.get("from"),
            "to": content.get("to"), 
            "subject": content.get("subject"),
            "date": content.get("date"),
            "labels": content.get("labels", []),
            "thread_id": content.get("thread_id"),
            "has_attachments": content.get("has_attachments", False),
            "is_unread": content.get("is_unread", False),
            "message_count": content.get("message_count", 1)
        }
    
    async def sync_to_local(self, 
                           output_directory: str,
                           filters: Optional[List[QueryFilter]] = None,
                           date_filter: Optional[DateRangeFilter] = None,
                           include_properties: bool = True,
                           force_update: bool = False,
                           excluded_properties: List[str] = None) -> SyncResult:
        """Sync Gmail threads to local storage - placeholder for backwards compatibility."""
        # This will be implemented in sync_to_local_unified
        raise NotImplementedError("Use sync_to_local_unified for Gmail connector")
    
    async def sync_to_local_unified(self, 
                                   storage,
                                   db_config,
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult:
        """Sync Gmail threads to local storage using unified storage system with message-level appending."""
        # Ensure connection before syncing (without interactive auth)
        if not self.service:
            connected = await self.connect(allow_interactive=False)
            if not connected:
                result = SyncResult()
                result.start_time = datetime.now()
                result.end_time = datetime.now()
                result.errors.append(
                    "Google not configured or token expired. "
                    "Run: maia auth configure google"
                )
                self.logger.error(result.errors[0])
                return result
        
        # Check if we're in message-level mode (new appending strategy)
        content_mode = self.config.get('gmail_content_mode', 'latest_only')
        
        if content_mode == 'latest_only':
            # Use new message-level appending strategy
            return await self._sync_messages_with_appending(
                storage, db_config, filters, date_filter, include_properties, force_update, excluded_properties, complex_filter
            )
        else:
            # Use legacy thread-level sync (full thread replacement)
            return await self._sync_threads_legacy(
                storage, db_config, filters, date_filter, include_properties, force_update, excluded_properties, complex_filter
            )
    
    async def _sync_threads_legacy(self, 
                                   storage,
                                   db_config,
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult:
        """Sync Gmail threads to local storage using unified storage system."""
        result = SyncResult()
        result.start_time = datetime.now()
        
        try:
            # Query Gmail for recent threads
            self.logger.info(f"Querying Gmail with date_filter: {date_filter}")
            email_threads = await self.query_pages(
                filters=filters, 
                date_filter=date_filter
            )
            
            if not email_threads:
                self.logger.info("No new email threads found from Gmail query.")
                return result
            
            self.logger.info(f"Found {len(email_threads)} email threads from Gmail query.")
            result.pages_fetched = len(email_threads)
            
            pages_to_save = []
            for thread in email_threads:
                # Prepare page data for unified storage
                page_data = self._prepare_page_for_storage(thread, db_config, excluded_properties)
                pages_to_save.append(page_data)
            
            if not pages_to_save:
                self.logger.info("No new or updated threads to save after filtering.")
                return result
            
            # Process pages with proper skipping logic  
            saved_count = 0
            skipped_count = 0
            
            try:
                for page in pages_to_save:
                    page_id = page['page_id']
                    title = page['metadata']['title']
                    
                    # Check if we should skip this page
                    should_skip = False
                    
                    if not force_update:
                        # Check if files exist locally
                        file_status = storage.files_exist_locally(page_id, title, db_config)
                        
                        if file_status['markdown']:
                            # File exists, check timestamps
                            page_date_str = page['metadata'].get('date')
                            db_last_sync_time_str = db_config.last_sync_time
                            
                            if page_date_str and db_last_sync_time_str:
                                try:
                                    # Parse page date (Gmail uses the email date)
                                    page_dt = datetime.fromisoformat(page_date_str.replace("Z", "+00:00"))
                                    sync_dt = datetime.fromisoformat(db_last_sync_time_str.replace("Z", "+00:00"))
                                    
                                    # Add 1 second tolerance for sync time comparison
                                    if page_dt <= (sync_dt + timedelta(seconds=1)):
                                        should_skip = True
                                        self.logger.debug(f"Skipping email thread {page_id} ('{title}'). Exists locally and up-to-date.")
                                        
                                except ValueError as ve:
                                    self.logger.warning(f"Could not parse dates for thread {page_id}: {ve}. Proceeding with sync.")
                    
                    if should_skip:
                        skipped_count += 1
                        result.pages_skipped += 1
                        continue
                    
                    # Save the page
                    try:
                        # Note: storage.save_content is a synchronous method
                        storage.save_content(
                            page_id=page_id,
                            title=title,
                            content_data=page['metadata'],
                            database_config=db_config,
                            markdown_content=page['content']
                        )
                        saved_count += 1
                        result.pages_saved += 1
                    except Exception as page_error:
                        self.logger.error(f"Failed to save page {page_id}: {page_error}")
                        result.errors.append(f"Failed to save {page_id}: {str(page_error)}")
                        result.pages_failed += 1
                
                result.success = saved_count > 0
                
                self.logger.info(f"Sync completed. {saved_count} email threads saved, {skipped_count} skipped")
                # Note: Individual processing messages removed for clean 3-line output per database

            except Exception as e:
                self.logger.error(f"Failed during unified sync save: {e}")
                result.errors.append(f"Failed during unified sync save: {str(e)}")

            result.end_time = datetime.now()
            return result
            
        except Exception as e:
            result.end_time = datetime.now()
            result.errors.append(f"Gmail sync failed: {str(e)}")
            self.logger.error(f"Gmail sync failed: {e}")
            return result
    
    async def _sync_messages_with_appending(self, 
                                            storage,
                                            db_config,
                                            filters: Optional[List[QueryFilter]] = None,
                                            date_filter: Optional[DateRangeFilter] = None,
                                            include_properties: bool = True,
                                            force_update: bool = False,
                                            excluded_properties: List[str] = None,
                                            complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult:
        """Sync Gmail using message-level appending strategy to avoid content duplication."""
        result = SyncResult()
        result.start_time = datetime.now()
        
        try:
            # Get hybrid storage registry for message-level operations
            from promaia.storage.hybrid_storage import get_hybrid_registry
            hybrid_registry = get_hybrid_registry()
            
            # Query Gmail for recent threads
            self.logger.info(f"Querying Gmail with date_filter: {date_filter}")
            email_threads = await self.query_pages(
                filters=filters, 
                date_filter=date_filter
            )
            
            if not email_threads:
                self.logger.info("No new email threads found from Gmail query.")
                return result
            
            self.logger.info(f"Found {len(email_threads)} email threads from Gmail query.")
            result.pages_fetched = len(email_threads)
            
            # Process each thread and extract individual messages
            messages_to_save = []
            for thread in email_threads:
                thread_id = thread.get('id')
                messages = thread.get('messages', [])
                
                if not messages:
                    continue
                
                # Get existing message IDs for this thread to avoid duplicates
                existing_message_ids = hybrid_registry.get_existing_message_ids_for_thread(
                    thread_id, db_config.workspace
                )
                
                # Process each message in the thread
                for i, message in enumerate(messages):
                    message_id = message.get('id')
                    
                    # Skip if message already exists
                    if message_id in existing_message_ids:
                        self.logger.debug(f"Skipping existing message {message_id} in thread {thread_id}")
                        continue
                    
                    # Prepare message data for storage
                    message_data = self._prepare_message_for_storage(
                        message, thread, i, len(messages), db_config, excluded_properties
                    )
                    messages_to_save.append(message_data)
            
            if not messages_to_save:
                self.logger.info("No new messages to save after filtering.")
                return result
            
            # Save individual messages
            saved_count = 0
            
            for message_data in messages_to_save:
                try:
                    # Save message to storage
                    storage.save_content(
                        page_id=message_data['page_id'],
                        title=message_data['metadata']['title'],
                        content_data=message_data['metadata'],
                        database_config=db_config,
                        markdown_content=message_data['content']
                    )
                    
                    # Update latest message flags if this is the latest message in its thread
                    if message_data['metadata'].get('is_latest_in_thread'):
                        hybrid_registry.update_latest_message_flags(
                            message_data['metadata']['thread_id'],
                            message_data['metadata']['message_id'],
                            db_config.workspace
                        )
                    
                    saved_count += 1
                    result.pages_saved += 1
                    
                except Exception as page_error:
                    self.logger.error(f"Failed to save message {message_data['page_id']}: {page_error}")
                    result.errors.append(f"Failed to save {message_data['page_id']}: {str(page_error)}")
                    result.pages_failed += 1
            
            result.success = saved_count > 0
            self.logger.info(f"Message-level sync completed. {saved_count} messages saved")
            
        except Exception as e:
            result.end_time = datetime.now()
            result.errors.append(f"Gmail message-level sync failed: {str(e)}")
            self.logger.error(f"Gmail message-level sync failed: {e}")
            return result
        
        result.end_time = datetime.now()
        return result
    
    def _prepare_page_for_storage(self, thread: Dict[str, Any], db_config, excluded_properties: List[str] = None) -> Dict[str, Any]:
        """Prepare thread data for the unified storage format."""
        
        page_id = thread['id']
        markdown_content = self._thread_to_markdown(thread)
        
        # Extract properties from thread data
        properties = {
            "title": thread.get('subject', 'No Subject'),
            "from": thread.get('from', 'Unknown'),
            "to": thread.get('to', ''),
            "cc": thread.get('cc', ''),
            "date": thread.get('date'),
            "labels": thread.get('labels', []),
            "has_attachments": thread.get('has_attachments', False),
            "is_unread": thread.get('is_unread', False),
            "snippet": thread.get('snippet', ''),
            "message_count": thread.get('message_count', 0)
        }
        
        # Metadata for registry
        metadata = {
            "page_id": page_id,
            "title": thread.get('subject', 'No Subject'),
            "created_time": thread.get('date'),
            "last_edited_time": thread.get('date'),
            "email_date": thread.get('date'),  # Add email_date for hybrid storage
            "synced_time": datetime.now(timezone.utc).isoformat(),
            "source_id": thread.get('thread_id'),
            "data_source": "gmail",
            "content_type": "email_thread",
            "properties": properties,
            "html_available": thread.get('body_html') is not None
        }
        
        return {
            "page_id": page_id,
            "content": markdown_content,
            "html_content": thread.get('body_html'),
            "metadata": metadata
        }

    def _thread_to_markdown(self, thread: Dict[str, Any]) -> str:
        """Convert a Gmail thread dictionary to a markdown string."""
        subject = thread.get("subject", "No Subject")
        from_addr = thread.get("from", "Unknown")
        to_addr = thread.get("to", "")
        cc_addr = thread.get("cc", "")
        date_str = thread.get("date", "")
        labels = thread.get("labels", [])
        message_count = thread.get("message_count", 1)
        has_attachments = thread.get("has_attachments", False)

        # Note: Date prefix for filename is now handled by unified storage using the 'date' field
        # No need to manually add it here to avoid double prefixing

        # Build CC line if present
        cc_line = f"**Cc:** {cc_addr}  \n" if cc_addr else ""

        # Create header
        header = f"""# Email Thread: {subject}

**From:** {from_addr}
**To:** {to_addr}
{cc_line}**Date:** {date_str}
**Messages:** {message_count}
**Labels:** {', '.join(labels)}
**Has Attachments:** {'Yes' if has_attachments else 'No'}

---

"""
        
        # Add conversation body
        conversation = thread.get("conversation_body", "")

        # Add attachment details if applicable
        attachment_note = ""
        attachments = thread.get("attachments", [])
        if attachments:
            attachment_note = "\n\n---\n## Attachments\n\n"
            for attachment in attachments:
                filename = attachment.get('filename', 'Unknown')
                size_bytes = attachment.get('size_bytes', 0)
                mime_type = attachment.get('mime_type', 'unknown')

                # Format size nicely
                if size_bytes < 1024:
                    size_str = f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    size_str = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

                attachment_note += f"- **{filename}** ({size_str}, {mime_type})\n"

            attachment_note += "\n*Note: Attachment files are not downloaded, only metadata is stored.*\n"

        return header + conversation + attachment_note 
    
    def _prepare_message_for_storage(self, message: Dict[str, Any], thread: Dict[str, Any], 
                                   message_index: int, total_messages: int, db_config, 
                                   excluded_properties: List[str] = None) -> Dict[str, Any]:
        """Prepare individual message data for storage with thread context."""
        
        # Extract message headers
        headers = {h['name'].lower(): h['value']
                  for h in message.get('payload', {}).get('headers', [])}

        message_id = message.get('id')
        thread_id = thread.get('id')
        subject = headers.get('subject', 'No Subject')
        from_addr = headers.get('from', 'Unknown')
        to_addr = headers.get('to', '')
        cc_addr = headers.get('cc', '')
        date_str = headers.get('date', '')
        
        # Parse date to ISO format for database consistency
        from email.utils import parsedate_to_datetime
        try:
            date_obj = parsedate_to_datetime(date_str)
            if date_obj.tzinfo is None:
                date_obj = date_obj.replace(tzinfo=timezone.utc)
            date_iso = date_obj.isoformat()
        except Exception:
            # Fallback to current time if parsing fails
            date_obj = datetime.now(timezone.utc)
            date_iso = date_obj.isoformat()
        
        # Create unique page_id for this individual message
        page_id = f"msg_{message_id}"
        
        # Extract clean message content (no quoted history)
        body = self._extract_message_body(message)
        raw_content = body.strip() if body.strip() else message.get('snippet', '')
        clean_content = self._extract_new_content(raw_content)
        
        # Determine if this is the latest message in the thread
        is_latest = message_index == (total_messages - 1)

        # Build CC line if present
        cc_line = f"**Cc:** {cc_addr}  \n" if cc_addr else ""

        # Create markdown content for individual message
        markdown_content = f"""# Email Message: {subject}

**From:** {from_addr}
**To:** {to_addr}
{cc_line}**Date:** {date_str}
**Thread:** {thread_id}
**Position:** {message_index + 1} of {total_messages}
**Is Latest:** {'Yes' if is_latest else 'No'}

---

{clean_content}

---
**Thread Context:** This is message {message_index + 1} of {total_messages} in thread {thread_id}
"""
        
        # Prepare metadata for storage
        # Parse CC recipients (can be multiple addresses separated by commas)
        cc_recipients = []
        if cc_addr:
            cc_recipients = [addr.strip() for addr in cc_addr.split(',') if addr.strip()]

        metadata = {
            "page_id": page_id,
            "title": f"{subject} (Message {message_index + 1})",
            "created_time": date_iso,  # ISO format for database consistency
            "last_edited_time": thread.get('date'),  # Thread's latest message date (already ISO)
            "email_date": date_iso,  # Email date in ISO format
            "thread_id": thread_id,
            "message_id": message_id,
            "subject": subject,
            "sender_email": from_addr,
            "sender_name": from_addr.split('<')[0].strip() if '<' in from_addr else from_addr,
            "recipient_emails": [to_addr] if to_addr else [],
            "cc_recipients": cc_recipients,
            "labels": thread.get('labels', []),
            "has_attachments": thread.get('has_attachments', False),
            "is_unread": thread.get('is_unread', False),
            "body_snippet": message.get('snippet', ''),
            "message_content": clean_content,
            "thread_position": message_index,
            "is_latest_in_thread": is_latest
        }
        
        return {
            'page_id': page_id,
            'content': markdown_content,
            'metadata': metadata
        }
    
    async def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
        attachments: Optional[List[str]] = None
    ) -> bool:
        """
        Send an email via Gmail API.

        Args:
            to: Recipient email address
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            thread_id: Gmail thread ID (for replies)
            in_reply_to: Message ID being replied to
            references: Full references chain for threading
            attachments: List of file paths to attach (optional)

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Create message container (multipart if we have attachments)
            if attachments:
                from email.mime.base import MIMEBase
                from email.mime.image import MIMEImage
                from email import encoders
                import mimetypes

                message = MIMEMultipart()
                message.attach(MIMEText(body_text, 'plain', _charset='utf-8'))
            else:
                # Create the email message with format=flowed to prevent hard wrapping
                # Use email.policy.default which doesn't add hard line breaks
                message = MIMEText(body_text, _charset='utf-8')

                # Prevent MIMEText from adding line breaks by using a custom policy
                # The default email generator wraps at 78 chars - we need to disable this
                message.set_param('format', 'flowed')
                message['Content-Type'] = 'text/plain; charset=utf-8; format=flowed'

            message['to'] = to
            message['subject'] = subject

            # Add threading headers for replies
            if in_reply_to:
                message['In-Reply-To'] = in_reply_to
            if references:
                message['References'] = references
            elif in_reply_to:
                # If no references provided but we have in-reply-to, use that as references
                message['References'] = in_reply_to

            # Add attachments if provided
            if attachments:
                for file_path in attachments:
                    try:
                        if not os.path.exists(file_path):
                            self.logger.warning(f"Attachment not found: {file_path}")
                            continue

                        # Guess MIME type
                        mime_type, _ = mimetypes.guess_type(file_path)
                        if mime_type is None:
                            mime_type = 'application/octet-stream'

                        main_type, sub_type = mime_type.split('/', 1)

                        # Read file content
                        with open(file_path, 'rb') as f:
                            file_data = f.read()

                        # Create appropriate MIME object based on type
                        if main_type == 'image':
                            attachment = MIMEImage(file_data, _subtype=sub_type)
                        else:
                            attachment = MIMEBase(main_type, sub_type)
                            attachment.set_payload(file_data)
                            encoders.encode_base64(attachment)

                        # Add header with filename
                        filename = os.path.basename(file_path)
                        attachment.add_header('Content-Disposition', 'attachment', filename=filename)
                        message.attach(attachment)

                        self.logger.info(f"📎 Attached: {filename}")
                    except Exception as e:
                        self.logger.error(f"Failed to attach {file_path}: {e}")
                        # Continue with other attachments
            
            # Encode the message using policy that prevents line wrapping
            from email import generator
            from io import BytesIO
            
            # Use policy with max_line_length=None to prevent wrapping
            policy_no_wrap = policy.EmailPolicy(max_line_length=None)
            fp = BytesIO()
            g = generator.BytesGenerator(fp, policy=policy_no_wrap)
            g.flatten(message)
            raw_message = base64.urlsafe_b64encode(fp.getvalue()).decode('utf-8')
            
            # Build the send request
            send_request = {'raw': raw_message}
            if thread_id:
                # Strip "thread_" prefix if present (Gmail API expects raw hex thread ID)
                send_request['threadId'] = self._get_raw_thread_id(thread_id)
            
            # Send the email
            result = self.service.users().messages().send(
                userId='me',
                body=send_request
            ).execute()
            
            self.logger.info(f"✅ Email sent successfully. Message ID: {result.get('id')}")
            return True
            
        except HttpError as e:
            self.logger.error(f"❌ Failed to send email (HTTP {e.resp.status}): {e}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Failed to send email: {e}")
            return False
    
    async def send_reply(
        self,
        thread_id: str,
        message_id: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None
    ) -> bool:
        """
        Send a reply to an existing thread.
        
        Args:
            thread_id: Gmail thread ID
            message_id: Original message ID being replied to
            subject: Email subject (should include RE: prefix)
            body_text: Plain text body
            body_html: HTML body (optional)
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Get the original message to extract recipient info and references
            # If message_id doesn't exist, fall back to latest message in thread
            try:
                original = await self._retry_with_backoff(
                    lambda: self.service.users().messages().get(
                        userId='me',
                        id=message_id,
                        format='full'
                    ).execute()
                )
            except HttpError as e:
                if e.resp.status == 404:
                    # Message not found - likely thread mismatch
                    # Get the latest message in the thread instead
                    self.logger.warning(f"Message {message_id} not found. Fetching latest message from thread {thread_id}")

                    # Get raw thread ID (strip prefix if present)
                    raw_thread_id = self._get_raw_thread_id(thread_id)

                    # Get thread data to find latest message
                    thread_data = await self._retry_with_backoff(
                        lambda: self.service.users().threads().get(
                            userId='me',
                            id=raw_thread_id,
                            format='full'
                        ).execute()
                    )

                    messages = thread_data.get('messages', [])
                    if not messages:
                        self.logger.error(f"Thread {thread_id} has no messages")
                        return False

                    # Use the latest message
                    original = messages[-1]
                    message_id = original['id']
                    self.logger.info(f"Using latest message {message_id} from thread {thread_id}")
                else:
                    raise

            # Extract headers from original message
            headers = {h['name'].lower(): h['value']
                      for h in original.get('payload', {}).get('headers', [])}
            
            # Determine reply-to address
            reply_to = headers.get('reply-to') or headers.get('from')
            
            # Build references chain
            existing_references = headers.get('references', '')
            message_id_header = headers.get('message-id', '')
            
            if existing_references and message_id_header:
                references = f"{existing_references} {message_id_header}"
            elif message_id_header:
                references = message_id_header
            else:
                references = None
            
            # Build reply with quoted original message
            full_body = self._build_quoted_reply(body_text, original)

            # Send the reply
            return await self.send_email(
                to=reply_to,
                subject=subject if subject.startswith('RE:') else f"RE: {subject}",
                body_text=full_body,
                body_html=body_html,
                thread_id=thread_id,
                in_reply_to=message_id_header,
                references=references
            )
            
        except Exception as e:
            self.logger.error(f"❌ Failed to send reply: {e}")
            return False

    async def _get_message(self, message_id: str) -> Optional[Dict]:
        """Fetch a single Gmail message by ID."""
        try:
            msg = await self._retry_with_backoff(
                lambda: self.service.users().messages().get(
                    userId='me', id=message_id, format='full'
                ).execute()
            )
            return msg
        except Exception as e:
            self.logger.error(f"Failed to fetch message {message_id}: {e}")
            return None

    async def _create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = None,
        thread_id: str = None,
        in_reply_to: str = None,
        references: str = None,
    ) -> Optional[str]:
        """Create a Gmail draft. Returns draft ID."""
        try:
            message = MIMEText(body, _charset='utf-8')
            message.set_param('format', 'flowed')
            message['Content-Type'] = 'text/plain; charset=utf-8; format=flowed'
            message['to'] = to
            message['subject'] = subject
            if cc:
                message['cc'] = cc
            if in_reply_to:
                message['In-Reply-To'] = in_reply_to
            if references:
                message['References'] = references

            from io import BytesIO
            from email import generator
            policy_no_wrap = policy.EmailPolicy(max_line_length=None)
            fp = BytesIO()
            g = generator.BytesGenerator(fp, policy=policy_no_wrap)
            g.flatten(message)
            raw_message = base64.urlsafe_b64encode(fp.getvalue()).decode('utf-8')

            draft_body: Dict[str, Any] = {'message': {'raw': raw_message}}
            if thread_id:
                draft_body['message']['threadId'] = self._get_raw_thread_id(thread_id)

            draft = self.service.users().drafts().create(
                userId='me', body=draft_body
            ).execute()

            draft_id = draft.get('id')
            self.logger.info(f"Draft created: {draft_id}")
            return draft_id
        except Exception as e:
            self.logger.error(f"Failed to create draft: {e}")
            return None

    def _build_quoted_reply(self, reply_text: str, original_message: Dict) -> str:
        """Build reply body with Gmail-style quoted original message."""
        headers = {
            h['name'].lower(): h['value']
            for h in original_message.get('payload', {}).get('headers', [])
        }
        sender = headers.get('from', 'someone')
        date = headers.get('date', '')

        original_body = self._extract_message_body(original_message)
        if not original_body:
            return reply_text

        # Quote original body
        quoted_lines = '\n'.join(
            f"> {line}" for line in original_body.strip().split('\n')
        )

        return f"{reply_text}\n\nOn {date}, {sender} wrote:\n{quoted_lines}"