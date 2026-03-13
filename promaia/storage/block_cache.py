"""
Persistent block cache for Notion content.
Stores block content keyed by page_id and last_edited_time to avoid redundant API calls.
"""
import sqlite3
import json
import time
from typing import List, Dict, Any, Optional
from pathlib import Path


class BlockCache:
    """
    Manages persistent cache for Notion blocks using SQLite.
    Caches block content to avoid repeated API calls for unchanged pages.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the block cache.

        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Use default path in the user's data directory
            from promaia.utils.env_writer import get_cache_dir
            data_dir = get_cache_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "block_cache.db")

        self.db_path = db_path
        self.conn = None
        self._initialize_db()

    def _initialize_db(self):
        """Initialize the database schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS block_cache (
                page_id TEXT NOT NULL,
                last_edited_time TEXT NOT NULL,
                blocks TEXT NOT NULL,
                cached_at REAL NOT NULL,
                PRIMARY KEY (page_id, last_edited_time)
            )
        """)

        # Create index for faster lookups
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_page_id
            ON block_cache(page_id)
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cached_at
            ON block_cache(cached_at)
        """)

        self.conn.commit()

    def get_blocks(self, page_id: str, last_edited_time: str) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached blocks for a page.

        Args:
            page_id: Notion page ID
            last_edited_time: Last edited timestamp from Notion

        Returns:
            List of block objects or None if not cached or outdated
        """
        cursor = self.conn.execute(
            """
            SELECT blocks FROM block_cache
            WHERE page_id = ? AND last_edited_time = ?
            """,
            (page_id, last_edited_time)
        )
        row = cursor.fetchone()

        if row:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                # Invalid JSON, remove from cache
                self.remove_blocks(page_id, last_edited_time)
                return None

        return None

    def set_blocks(self, page_id: str, last_edited_time: str, blocks: List[Dict[str, Any]]):
        """
        Store blocks in cache.

        Args:
            page_id: Notion page ID
            last_edited_time: Last edited timestamp from Notion
            blocks: List of block objects to cache
        """
        current_time = time.time()
        blocks_json = json.dumps(blocks)

        self.conn.execute(
            """
            INSERT OR REPLACE INTO block_cache
            (page_id, last_edited_time, blocks, cached_at)
            VALUES (?, ?, ?, ?)
            """,
            (page_id, last_edited_time, blocks_json, current_time)
        )
        self.conn.commit()

    def remove_blocks(self, page_id: str, last_edited_time: Optional[str] = None):
        """
        Remove blocks from cache.

        Args:
            page_id: Notion page ID
            last_edited_time: If specified, only removes blocks with this timestamp
        """
        if last_edited_time:
            self.conn.execute(
                "DELETE FROM block_cache WHERE page_id = ? AND last_edited_time = ?",
                (page_id, last_edited_time)
            )
        else:
            # Remove all cached versions for this page
            self.conn.execute(
                "DELETE FROM block_cache WHERE page_id = ?",
                (page_id,)
            )
        self.conn.commit()

    def cleanup_old_entries(self, days: int = 30):
        """
        Remove cache entries older than specified days.

        Args:
            days: Number of days to keep (default: 30)
        """
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        self.conn.execute(
            "DELETE FROM block_cache WHERE cached_at < ?",
            (cutoff_time,)
        )
        self.conn.commit()

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the cache.

        Returns:
            Dictionary with cache statistics
        """
        cursor = self.conn.execute("SELECT COUNT(*) FROM block_cache")
        total_entries = cursor.fetchone()[0]

        cursor = self.conn.execute(
            "SELECT COUNT(DISTINCT page_id) FROM block_cache"
        )
        unique_pages = cursor.fetchone()[0]

        cursor = self.conn.execute(
            """
            SELECT COUNT(*) FROM block_cache
            WHERE cached_at > ?
            """,
            (time.time() - (24 * 60 * 60),)
        )
        recent_entries = cursor.fetchone()[0]

        # Get database size
        cursor = self.conn.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
        db_size_bytes = cursor.fetchone()[0]

        return {
            'total_entries': total_entries,
            'unique_pages': unique_pages,
            'entries_cached_last_24h': recent_entries,
            'db_size_mb': round(db_size_bytes / (1024 * 1024), 2),
            'db_path': self.db_path
        }

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
