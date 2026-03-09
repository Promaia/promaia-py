"""
Persistent caching system for CMS sync operations.
Tracks content hashes to skip unchanged pages between sync runs.
"""
import sqlite3
import hashlib
import json
import time
from typing import Dict, Any, Optional
from pathlib import Path
import os


class SyncCache:
    """
    Manages persistent cache for sync operations using SQLite.
    Stores content hashes to detect changes and avoid unnecessary processing.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the sync cache.

        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Use default path in the user's data directory
            from promaia.utils.env_writer import get_cache_dir
            data_dir = get_cache_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "sync_cache.db")

        self.db_path = db_path
        self.conn = None
        self._initialize_db()

    def _initialize_db(self):
        """Initialize the database schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS page_cache (
                page_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                last_edited_time TEXT,
                last_synced_at REAL NOT NULL,
                webflow_id TEXT
            )
        """)

        # Create index for faster lookups
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_last_synced
            ON page_cache(last_synced_at)
        """)

        self.conn.commit()

    def compute_content_hash(self, page_data: Dict[str, Any]) -> str:
        """
        Compute a hash of the page content for change detection.

        Args:
            page_data: Notion page object

        Returns:
            SHA256 hash of the page content
        """
        # Include key fields that affect sync output
        content = {
            'last_edited_time': page_data.get('last_edited_time'),
            'properties': page_data.get('properties', {})
        }

        # Create a stable JSON representation
        content_json = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_json.encode()).hexdigest()

    def should_process_page(self, page_id: str, page_data: Dict[str, Any]) -> bool:
        """
        Check if a page should be processed based on content changes.

        Args:
            page_id: Notion page ID
            page_data: Notion page object

        Returns:
            True if page should be processed (changed or new), False if unchanged
        """
        current_hash = self.compute_content_hash(page_data)
        last_edited_time = page_data.get('last_edited_time')

        cursor = self.conn.execute(
            """
            SELECT content_hash, last_edited_time
            FROM page_cache
            WHERE page_id = ?
            """,
            (page_id,)
        )
        row = cursor.fetchone()

        if not row:
            # New page, should process
            return True

        cached_hash, cached_time = row

        # Check if content has changed
        if current_hash != cached_hash:
            return True

        # Check if last_edited_time has changed
        if last_edited_time != cached_time:
            return True

        # No changes detected
        return False

    def update_cache(self, page_id: str, page_data: Dict[str, Any], webflow_id: Optional[str] = None):
        """
        Update the cache for a processed page.

        Args:
            page_id: Notion page ID
            page_data: Notion page object
            webflow_id: Webflow item ID (optional)
        """
        content_hash = self.compute_content_hash(page_data)
        last_edited_time = page_data.get('last_edited_time')
        current_time = time.time()

        self.conn.execute(
            """
            INSERT OR REPLACE INTO page_cache
            (page_id, content_hash, last_edited_time, last_synced_at, webflow_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (page_id, content_hash, last_edited_time, current_time, webflow_id)
        )
        self.conn.commit()

    def get_cached_webflow_id(self, page_id: str) -> Optional[str]:
        """
        Get the cached Webflow ID for a page.

        Args:
            page_id: Notion page ID

        Returns:
            Webflow item ID or None if not cached
        """
        cursor = self.conn.execute(
            "SELECT webflow_id FROM page_cache WHERE page_id = ?",
            (page_id,)
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def remove_page(self, page_id: str):
        """
        Remove a page from the cache.

        Args:
            page_id: Notion page ID
        """
        self.conn.execute("DELETE FROM page_cache WHERE page_id = ?", (page_id,))
        self.conn.commit()

    def cleanup_old_entries(self, days: int = 90):
        """
        Remove cache entries older than specified days.

        Args:
            days: Number of days to keep (default: 90)
        """
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        self.conn.execute(
            "DELETE FROM page_cache WHERE last_synced_at < ?",
            (cutoff_time,)
        )
        self.conn.commit()

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the cache.

        Returns:
            Dictionary with cache statistics
        """
        cursor = self.conn.execute("SELECT COUNT(*) FROM page_cache")
        total_entries = cursor.fetchone()[0]

        cursor = self.conn.execute(
            """
            SELECT COUNT(*) FROM page_cache
            WHERE last_synced_at > ?
            """,
            (time.time() - (24 * 60 * 60),)
        )
        recent_entries = cursor.fetchone()[0]

        return {
            'total_entries': total_entries,
            'entries_synced_last_24h': recent_entries,
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
