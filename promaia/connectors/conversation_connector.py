"""
Conversation history connector implementation for Maia.

This module provides access to Maia's chat conversation history, integrating
it as a first-class content type with full vector search and SQL search capabilities.
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

logger = logging.getLogger(__name__)


class ConversationConnector(BaseConnector):
    """Connector for Maia conversation history."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # Use database_id as an identifier, or default to 'convos'
        self.conversation_source = config.get("database_id", "convos")
        self.workspace = config.get("workspace", "default")

        # Path to the chat history file (matches ChatHistoryManager)
        from promaia.utils.env_writer import get_data_dir
        default_history = str(get_data_dir() / "chat_history.json")
        self.history_file = config.get("history_file", default_history)

        self._connected = False

    async def connect(self) -> bool:
        """Establish connection (verify history file exists)."""
        try:
            if not os.path.exists(self.history_file):
                self.logger.warning(f"Chat history file not found: {self.history_file}")
                # Create empty file if it doesn't exist
                with open(self.history_file, 'w') as f:
                    json.dump([], f)

            self._connected = True
            self.logger.info(f"Conversation connector initialized for {self.history_file}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect to conversation history: {e}")
            return False

    async def test_connection(self) -> bool:
        """Test if we can read the conversation history."""
        if not self._connected:
            await self.connect()

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                json.load(f)
            return True
        except Exception as e:
            self.logger.error(f"Conversation history test failed: {e}")
            return False

    async def get_database_schema(self) -> Dict[str, Any]:
        """Get the schema/properties for conversation threads."""
        return {
            "thread_id": {"type": "text", "description": "Unique thread identifier"},
            "thread_name": {"type": "text", "description": "Thread name/title"},
            "message_count": {"type": "number", "description": "Number of messages in thread"},
            "created_at": {"type": "date", "description": "Thread creation timestamp"},
            "last_accessed": {"type": "date", "description": "Last access timestamp"},
            "context_type": {"type": "text", "description": "Type of context (sql_query, general, etc.)"},
            "sql_query_prompt": {"type": "text", "description": "SQL query if natural language thread"},
        }

    def _load_threads(self) -> List[Dict[str, Any]]:
        """Load all conversation threads from the history file."""
        if not os.path.exists(self.history_file):
            return []

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                threads = json.load(f)
                return threads if isinstance(threads, list) else []
        except Exception as e:
            self.logger.error(f"Failed to load conversation threads: {e}")
            return []

    def _thread_to_page(self, thread: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a conversation thread to a page-like structure."""
        # Extract metadata
        thread_id = thread.get('id', 'unknown')
        thread_name = thread.get('name', 'Untitled Conversation')
        messages = thread.get('messages', [])
        context = thread.get('context', {})
        last_accessed = thread.get('last_accessed', '')
        created_at = thread.get('created_at', '')

        # Determine context type
        context_type = 'general'
        if context.get('sql_query_prompt'):
            context_type = 'sql_query'
        elif context.get('query_command'):
            context_type = 'search'

        # Build page structure
        page = {
            'id': thread_id,
            'properties': {
                'thread_id': thread_id,
                'thread_name': thread_name,
                'message_count': len(messages),
                'created_at': created_at,
                'last_accessed': last_accessed,
                'context_type': context_type,
                'sql_query_prompt': context.get('sql_query_prompt', ''),
            },
            'messages': messages,
            'context': context,
            'created_time': created_at,
            'last_edited_time': last_accessed,
        }

        return page

    async def query_pages(self,
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query conversation threads."""
        if not self._connected:
            await self.connect()

        threads = self._load_threads()
        pages = [self._thread_to_page(thread) for thread in threads]

        # Apply date filter
        if date_filter:
            filtered_pages = []
            for page in pages:
                date_field = date_filter.property_name
                # Map created_time to created_at for conversations
                if date_field == 'created_time':
                    date_field = 'created_at'
                elif date_field == 'last_edited_time':
                    date_field = 'last_accessed'

                if date_field in ['last_accessed', 'created_at']:
                    date_str = page['properties'].get(date_field, '')
                    if date_str:
                        try:
                            page_date = datetime.fromisoformat(date_str)

                            # Check if within range
                            if date_filter.start_date and page_date < date_filter.start_date:
                                continue
                            if date_filter.end_date and page_date > date_filter.end_date:
                                continue

                            filtered_pages.append(page)
                        except:
                            continue
                    else:
                        # No date string - include the page if no date restrictions
                        if not date_filter.start_date and not date_filter.end_date:
                            filtered_pages.append(page)
                else:
                    # Unknown date field - include the page
                    filtered_pages.append(page)
            pages = filtered_pages

        # Apply property filters
        if filters:
            filtered_pages = []
            for page in pages:
                include_page = True
                for f in filters:
                    prop_value = page['properties'].get(f.property_name)

                    if f.operator == 'eq' and prop_value != f.value:
                        include_page = False
                        break
                    elif f.operator == 'ne' and prop_value == f.value:
                        include_page = False
                        break
                    elif f.operator == 'contains' and f.value not in str(prop_value):
                        include_page = False
                        break

                if include_page:
                    filtered_pages.append(page)
            pages = filtered_pages

        # Sort
        if sort_by and sort_by in ['last_accessed', 'created_at', 'message_count']:
            reverse = (sort_direction == 'desc')
            pages.sort(key=lambda p: p['properties'].get(sort_by, ''), reverse=reverse)

        # Limit
        if limit:
            pages = pages[:limit]

        return pages

    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full content of a specific conversation thread."""
        threads = self._load_threads()

        for thread in threads:
            if thread.get('id') == page_id:
                return self._thread_to_page(thread)

        return {}

    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get properties of a specific conversation thread."""
        page = await self.get_page_content(page_id)
        return page.get('properties', {})

    async def sync_to_local(self,
                           output_directory: str,
                           filters: Optional[List[QueryFilter]] = None,
                           date_filter: Optional[DateRangeFilter] = None,
                           include_properties: bool = True,
                           force_update: bool = False,
                           excluded_properties: List[str] = None) -> SyncResult:
        """Sync conversation history to local storage (legacy method)."""
        # This is the old API - forward to sync_to_local_unified
        from promaia.storage.unified_storage import UnifiedStorage

        storage = UnifiedStorage()

        # Get database config
        db_config = {
            'nickname': 'convos',
            'source_type': 'conversation',
            'database_id': self.conversation_source,
            'workspace': self.workspace,
            'markdown_directory': output_directory,
        }

        return await self.sync_to_local_unified(
            storage=storage,
            db_config=db_config,
            filters=filters,
            date_filter=date_filter,
            include_properties=include_properties,
            force_update=force_update,
            excluded_properties=excluded_properties
        )

    async def sync_to_local_unified(self,
                                   storage,
                                   db_config: Dict[str, Any],
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult:
        """
        Sync conversation history to unified storage with vector and SQL search.

        This is the main sync method that integrates conversations with the full Maia system.
        """
        result = SyncResult()
        result.start_time = datetime.now()
        result.database_name = getattr(db_config, 'nickname', 'convos')

        try:
            # Ensure connection
            if not self._connected:
                await self.connect()

            # Query pages with filters
            pages = await self.query_pages(
                filters=filters,
                date_filter=date_filter
            )

            result.pages_fetched = len(pages)
            self.logger.info(f"Found {len(pages)} conversation threads to sync")

            # Process each conversation thread
            for page in pages:
                try:
                    thread_id = page['id']
                    thread_name = page['properties']['thread_name']

                    # Convert conversation to markdown
                    from promaia.markdown.converter import conversation_to_markdown

                    markdown_content = conversation_to_markdown(page)

                    # Prepare metadata for storage
                    metadata = {
                        'page_id': thread_id,
                        'title': thread_name,
                        'source_id': thread_id,
                        'data_source': 'conversation',
                        'content_type': 'conversation',
                        'workspace': self.workspace,
                        'database_id': self.conversation_source,
                        'database_name': result.database_name,
                        'created_time': page.get('created_time', ''),
                        'last_edited_time': page.get('last_edited_time', ''),
                        'synced_time': datetime.now().isoformat(),

                        # Conversation-specific properties
                        'thread_id': thread_id,
                        'message_count': page['properties']['message_count'],
                        'context_type': page['properties']['context_type'],
                        'sql_query_prompt': page['properties'].get('sql_query_prompt', ''),
                    }

                    # Save to unified storage (includes vector embeddings)
                    saved_files = storage.save_content(
                        page_id=thread_id,
                        title=thread_name,
                        content_data=metadata,
                        database_config=db_config,
                        markdown_content=markdown_content
                    )

                    if saved_files and 'markdown' in saved_files:
                        result.add_success(saved_files['markdown'])
                        self.logger.debug(f"Synced conversation: {thread_name}")
                    else:
                        result.add_skip()

                except Exception as e:
                    error_msg = f"Failed to sync conversation {page.get('id', 'unknown')}: {e}"
                    self.logger.error(error_msg)
                    result.add_error(error_msg)

            result.end_time = datetime.now()
            self.logger.info(
                f"Conversation sync complete: {result.pages_saved} saved, "
                f"{result.pages_skipped} skipped, {result.pages_failed} failed"
            )

        except Exception as e:
            self.logger.error(f"Conversation sync failed: {e}")
            result.add_error(str(e))
            result.end_time = datetime.now()

        return result
