"""
Recent queries management for maia chat command.
Stores and retrieves the last 20 chat queries for easy re-execution.
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

@dataclass
class RecentQuery:
    """Represents a recent chat query."""
    command: str
    sources: Optional[List[str]] = None
    filters: Optional[List[str]] = None
    workspace: Optional[str] = None
    timestamp: Optional[str] = None
    sql_query_prompt: Optional[str] = None  # New field for NL queries
    original_browse_command: Optional[str] = None  # Original browse command for display
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RecentQuery':
        """Create from dictionary for JSON deserialization."""
        return cls(**data)
    
    def __str__(self) -> str:
        """Human-readable representation of the query."""
        # If this is a natural language query, show it differently
        if self.sql_query_prompt:
            command_str = f"maia chat -nl {self.sql_query_prompt}"
        elif self.original_browse_command:
            # Use the original browse command for display
            command_str = self.original_browse_command
        else:
            # Traditional query format
            parts = []
            if self.sources:
                parts.append(f"-s {' '.join(self.sources)}")
            if self.filters:
                for filter_expr in self.filters:
                    parts.append(f"-f '{filter_expr}'")
            # Only show workspace if there are other parameters or if it's the only parameter and meaningful
            if self.workspace and (self.sources or self.filters or len(str(self.workspace)) > 3):
                parts.append(f"-ws {self.workspace}")

            command_str = f"maia chat {' '.join(parts)}" if parts else "maia chat"
        
        # Add timestamp for display
        if self.timestamp:
            try:
                dt = datetime.fromisoformat(self.timestamp)
                time_str = dt.strftime("%Y-%m-%d %H:%M")
                return f"{command_str} ({time_str})"
            except:
                pass
        
        return command_str

class RecentsManager:
    """Manages recent chat queries."""
    
    def __init__(self, max_entries: int = 20):
        self.max_entries = max_entries
        from promaia.utils.env_writer import get_data_dir
        self.recents_file = str(get_data_dir() / ".maia_recents.json")
    
    def _load_recents(self) -> List[RecentQuery]:
        """Load recent queries from file."""
        if not os.path.exists(self.recents_file):
            return []
        
        try:
            with open(self.recents_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return [RecentQuery.from_dict(item) for item in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            # If file is corrupted, start fresh
            return []
    
    def _save_recents(self, recents: List[RecentQuery]) -> None:
        """Save recent queries to file."""
        try:
            with open(self.recents_file, 'w', encoding='utf-8') as f:
                json.dump([item.to_dict() for item in recents], f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save recent queries: {e}")
    
    def add_query(self, sources: Optional[List[str]] = None, 
                  filters: Optional[List[str]] = None, 
                  workspace: Optional[str] = None,
                  sql_query_prompt: Optional[str] = None,
                  original_browse_command: Optional[str] = None) -> None:
        """Add a new query to recents."""
        new_query = RecentQuery(
            command="chat",
            sources=sources,
            filters=filters,
            workspace=workspace,
            timestamp=datetime.now().isoformat(),
            sql_query_prompt=sql_query_prompt,
            original_browse_command=original_browse_command
        )
        
        recents = self._load_recents()
        
        # Remove duplicate if exists (compare by command parameters, not timestamp)
        recents = [q for q in recents if not self._queries_equal(q, new_query)]
        
        # Add new query at the beginning
        recents.insert(0, new_query)
        
        # Keep only max_entries
        recents = recents[:self.max_entries]
        
        self._save_recents(recents)
    
    def _queries_equal(self, q1: RecentQuery, q2: RecentQuery) -> bool:
        """Check if two queries are equal (ignoring timestamp)."""
        # If both are natural language queries, compare prompts
        if q1.sql_query_prompt and q2.sql_query_prompt:
            return (q1.sql_query_prompt == q2.sql_query_prompt and
                    q1.workspace == q2.workspace)
        
        # If one is NL and one is traditional, they're different
        if q1.sql_query_prompt or q2.sql_query_prompt:
            return False
        
        # Both are traditional queries
        return (q1.sources == q2.sources and 
                q1.filters == q2.filters and 
                q1.workspace == q2.workspace)
    
    def get_recents(self) -> List[RecentQuery]:
        """Get list of recent queries."""
        return self._load_recents()
    
    def clear_recents(self) -> None:
        """Clear all recent queries."""
        if os.path.exists(self.recents_file):
            os.remove(self.recents_file)
    
    def has_recents(self) -> bool:
        """Check if there are any recent queries."""
        return len(self.get_recents()) > 0 