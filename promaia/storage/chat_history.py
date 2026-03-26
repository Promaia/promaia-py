"""
Chat history management for maia chat threads.
Stores and retrieves the last 10 chat conversations for easy re-execution.
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

@dataclass
class ChatThread:
    """Represents a chat thread/conversation."""
    id: str
    name: str
    messages: List[Dict[str, str]]
    context: Dict[str, Any]
    last_accessed: str
    created_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChatThread':
        """Create from dictionary for JSON deserialization."""
        return cls(**data)
    
    def __str__(self) -> str:
        """Human-readable representation of the thread."""
        # Format: "Thread Name (Jan 15, 14:30)"
        if self.last_accessed:
            try:
                dt = datetime.fromisoformat(self.last_accessed)
                time_str = dt.strftime("%b %d, %H:%M")
                return f"{self.name} ({time_str})"
            except:
                pass
        
        return self.name

class ChatHistoryManager:
    """Manages chat history threads."""

    def __init__(self, max_entries: int = 10):
        self.max_entries = max_entries
        from promaia.utils.env_writer import get_data_dir
        self.history_file = str(get_data_dir() / "chat_history.json")
        self._db_integration_enabled = True  # Flag to enable/disable database integration
        self._backfill_checked = False
    
    def _load_history(self) -> List[ChatThread]:
        """Load chat threads from file."""
        if not os.path.exists(self.history_file):
            return []

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                threads = []
                for item in data:
                    # Ensure timestamp fields are strings (handle legacy data)
                    if 'last_accessed' in item and isinstance(item['last_accessed'], datetime):
                        item['last_accessed'] = item['last_accessed'].isoformat()
                    if 'created_at' in item and isinstance(item['created_at'], datetime):
                        item['created_at'] = item['created_at'].isoformat()
                    threads.append(ChatThread.from_dict(item))
                return threads
        except (json.JSONDecodeError, KeyError, TypeError):
            # If file is corrupted, start fresh
            return []
    
    def _save_history(self, threads: List[ChatThread]) -> None:
        """Save chat threads to file."""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump([item.to_dict() for item in threads], f, indent=2, cls=DateTimeEncoder)
        except Exception as e:
            print(f"Warning: Could not save chat history: {e}")
    
    def _generate_thread_name(self, messages: List[Dict[str, str]]) -> str:
        """Generate a thread name using AI summarization."""
        # For now, use first user message truncated (AI summarization to be added)
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '').strip()
                if content:
                    # Truncate to ~40 characters for display
                    if len(content) > 40:
                        return content[:37] + "..."
                    return content

        # Fallback to timestamp
        return f"Chat - {datetime.now().strftime('%b %d, %H:%M')}"

    def _save_to_database(self, thread: ChatThread) -> bool:
        """
        Save a conversation thread to the unified database with vector embeddings.
        This integrates conversations with the full Maia search system.
        """
        if not self._db_integration_enabled:
            return False

        try:
            from promaia.storage.unified_storage import UnifiedStorage
            from promaia.markdown.converter import conversation_to_markdown
            from promaia.config.databases import get_database_config

            # Get the conversation database config
            db_config = get_database_config('convos')
            if not db_config:
                # Database not configured, skip silently
                return False

            # Convert thread to page-like structure for markdown conversion
            page_data = {
                'id': thread.id,
                'properties': {
                    'thread_id': thread.id,
                    'thread_name': thread.name,
                    'message_count': len(thread.messages),
                    'created_at': thread.created_at,
                    'last_accessed': thread.last_accessed,
                    'context_type': 'sql_query' if thread.context.get('sql_query_prompt') else 'general',
                    'sql_query_prompt': thread.context.get('sql_query_prompt', ''),
                },
                'messages': thread.messages,
                'context': thread.context,
                'created_time': thread.created_at,
                'last_edited_time': thread.last_accessed,
            }

            # Convert to markdown
            markdown_content = conversation_to_markdown(page_data)

            # Extract workspaces used in this conversation
            import json
            workspaces_used = set()
            context = thread.context or {}

            # Add primary workspace if specified
            if context.get('workspace'):
                workspaces_used.add(context.get('workspace'))

            # Extract workspaces from source specifications
            for source in (context.get('sources') or []):
                base_name = source.split(':')[0]  # Remove day specification
                if '.' in base_name:
                    workspace = base_name.split('.')[0]
                    workspaces_used.add(workspace)

            # Add from resolved_workspace if different
            if context.get('resolved_workspace'):
                workspaces_used.add(context.get('resolved_workspace'))

            # Default to 'default' if no workspaces found
            primary_workspace = list(workspaces_used)[0] if workspaces_used else 'default'
            workspaces_json = json.dumps(sorted(list(workspaces_used))) if workspaces_used else None

            # Prepare metadata
            metadata = {
                'page_id': thread.id,
                'title': thread.name,
                'source_id': thread.id,
                'data_source': 'conversation',
                'content_type': 'conversation',
                'workspace': primary_workspace,
                'database_id': 'convos',
                'database_name': 'convos',
                'created_time': thread.created_at,
                'last_edited_time': thread.last_accessed,
                'synced_time': datetime.now().isoformat(),

                # Conversation-specific properties
                'thread_id': thread.id,
                'message_count': len(thread.messages),
                'context_type': page_data['properties']['context_type'],
                'sql_query_prompt': thread.context.get('sql_query_prompt', ''),
                'workspaces_used': workspaces_json,
            }

            # Save to unified storage (includes vector embeddings and SQL)
            storage = UnifiedStorage()

            # Save using the correct UnifiedStorage API
            result = storage.save_content(
                page_id=thread.id,
                title=thread.name,
                content_data=metadata,  # Pass metadata as content_data
                database_config=db_config,
                markdown_content=markdown_content
            )

            return result and 'markdown' in result

        except Exception as e:
            # Silently fail - don't break chat functionality if database integration fails
            import logging
            logging.getLogger(__name__).warning(f"Failed to save conversation to database: {e}")
            return False
    
    def backfill_to_database(self) -> int:
        """
        Backfill any conversations from chat_history.json that aren't yet in unified storage.
        Returns the number of threads backfilled.
        """
        if not self._db_integration_enabled:
            return 0

        try:
            from promaia.config.databases import get_database_config
            db_config = get_database_config('convos')
            if not db_config:
                return 0

            # Check how many conversations are already indexed
            from promaia.storage.hybrid_storage import get_hybrid_registry
            registry = get_hybrid_registry()
            import sqlite3
            with sqlite3.connect(registry.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT page_id FROM conversation_content WHERE database_id = 'convos'")
                indexed_ids = {row[0] for row in cursor.fetchall()}

            threads = self._load_history()
            unindexed = [t for t in threads if t.id not in indexed_ids]

            if not unindexed:
                return 0

            count = 0
            for thread in unindexed:
                if self._save_to_database(thread):
                    count += 1

            if count > 0:
                import logging
                logging.getLogger(__name__).info(f"Backfilled {count} conversations to unified storage")

            return count

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Conversation backfill failed: {e}")
            return 0

    def _ensure_backfill(self) -> None:
        """Run backfill once per instance if needed."""
        if not self._backfill_checked:
            self._backfill_checked = True
            self.backfill_to_database()

    def save_thread(self,
                   messages: List[Dict[str, str]],
                   context: Dict[str, Any],
                   thread_name: Optional[str] = None) -> str:
        """Save a new chat thread."""
        self._ensure_backfill()
        thread_id = f"thread_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        timestamp = datetime.now().isoformat()
        
        # Generate name if not provided
        if not thread_name:
            thread_name = self._generate_thread_name(messages)
        
        new_thread = ChatThread(
            id=thread_id,
            name=thread_name,
            messages=messages.copy(),
            context=context.copy(),
            last_accessed=timestamp,
            created_at=timestamp
        )
        
        threads = self._load_history()

        # Add new thread at the beginning (most recent)
        threads.insert(0, new_thread)

        # Keep only max_entries
        threads = threads[:self.max_entries]

        self._save_history(threads)

        # Also save to database for search integration
        self._save_to_database(new_thread)

        return thread_id
    
    def is_natural_language_thread(self, thread: ChatThread) -> bool:
        """Check if a thread was created with a natural language query."""
        return thread.context.get('sql_query_prompt') is not None
    
    def get_thread_query_command(self, thread: ChatThread) -> str:
        """Get the command string for a thread, prioritizing natural language format."""
        context = thread.context
        
        # Check if this was a natural language thread
        if context.get('sql_query_prompt'):
            return f"maia chat -nl {context['sql_query_prompt']}"
        
        # Fall back to traditional format
        return context.get('query_command', 'maia chat')
    
    def get_threads(self) -> List[ChatThread]:
        """Get list of chat threads ordered by last_accessed."""
        threads = self._load_history()
        # Sort by last_accessed (most recent first)
        threads.sort(key=lambda t: t.last_accessed, reverse=True)
        return threads
    
    def get_thread(self, thread_id: str) -> Optional[ChatThread]:
        """Get a specific thread by ID."""
        threads = self._load_history()
        for thread in threads:
            if thread.id == thread_id:
                return thread
        return None
    
    def update_thread_access(self, thread_id: str) -> None:
        """Update the last_accessed timestamp for a thread."""
        threads = self._load_history()
        for thread in threads:
            if thread.id == thread_id:
                thread.last_accessed = datetime.now().isoformat()
                self._save_history(threads)
                break
    
    def update_thread(self, thread_id: str, messages: List[Dict[str, str]], 
                     context: Dict[str, Any], thread_name: Optional[str] = None) -> bool:
        """Update an existing thread with new messages and context."""
        threads = self._load_history()
        for thread in threads:
            if thread.id == thread_id:
                # Update the thread
                thread.messages = messages.copy()
                thread.context = context.copy()
                thread.last_accessed = datetime.now().isoformat()
                
                # Update name if provided
                if thread_name:
                    thread.name = thread_name

                self._save_history(threads)

                # Also update in database
                self._save_to_database(thread)

                return True
        return False
    
    def clear_history(self) -> None:
        """Clear all chat history."""
        if os.path.exists(self.history_file):
            os.remove(self.history_file)
    
    def clean_duplicates(self) -> int:
        """Remove duplicate threads with the same name, keeping the most recent."""
        threads = self._load_history()
        if len(threads) <= 1:
            return 0
        
        # Group threads by name
        name_groups = {}
        for thread in threads:
            if thread.name not in name_groups:
                name_groups[thread.name] = []
            name_groups[thread.name].append(thread)
        
        # Keep only the most recent thread for each name
        cleaned_threads = []
        removed_count = 0
        
        for name, thread_list in name_groups.items():
            if len(thread_list) > 1:
                # Sort by last_accessed (most recent first)
                thread_list.sort(key=lambda t: t.last_accessed, reverse=True)
                cleaned_threads.append(thread_list[0])  # Keep most recent
                removed_count += len(thread_list) - 1
            else:
                cleaned_threads.append(thread_list[0])
        
        # Sort the cleaned threads by last_accessed
        cleaned_threads.sort(key=lambda t: t.last_accessed, reverse=True)
        
        if removed_count > 0:
            self._save_history(cleaned_threads)
        
        return removed_count
    
    def has_history(self) -> bool:
        """Check if there are any saved threads."""
        return len(self.get_threads()) > 0 