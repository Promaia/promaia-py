"""
Discord OCR deduplication tracker.

Tracks which Discord image attachments have been processed through the OCR
pipeline, enabling idempotent syncs and reply-based re-processing.
"""
import sqlite3
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class DiscordOCRTracker:
    """Tracks Discord image attachments processed through OCR."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path
        self._ensure_table_exists()

    def _ensure_table_exists(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discord_ocr_processed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    attachment_id TEXT NOT NULL,
                    channel_id TEXT,
                    server_id TEXT,
                    original_filename TEXT,
                    image_path TEXT,
                    annotation TEXT,
                    ocr_status TEXT,
                    markdown_path TEXT,
                    processed_at TEXT NOT NULL,
                    UNIQUE(message_id, attachment_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_discord_ocr_message
                ON discord_ocr_processed(message_id)
            """)
            conn.commit()

    def is_processed(self, message_id: str, attachment_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM discord_ocr_processed WHERE message_id = ? AND attachment_id = ?",
                (message_id, attachment_id)
            ).fetchone()
            return row is not None

    def mark_processed(
        self,
        message_id: str,
        attachment_id: str,
        channel_id: str = None,
        server_id: str = None,
        original_filename: str = None,
        image_path: str = None,
        annotation: str = None,
        ocr_status: str = "completed",
        markdown_path: str = None,
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO discord_ocr_processed (
                    message_id, attachment_id, channel_id, server_id,
                    original_filename, image_path, annotation,
                    ocr_status, markdown_path, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id, attachment_id, channel_id, server_id,
                original_filename, image_path, annotation,
                ocr_status, markdown_path, datetime.now().isoformat()
            ))
            conn.commit()

    def get_by_message_id(self, message_id: str) -> List[Dict[str, Any]]:
        """Get all tracked attachments for a message (for reply re-processing)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM discord_ocr_processed WHERE message_id = ?",
                (message_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_annotation(
        self,
        message_id: str,
        attachment_id: str,
        annotation: str,
        markdown_path: str = None,
    ):
        """Update annotation and optionally markdown path after re-processing."""
        with sqlite3.connect(self.db_path) as conn:
            if markdown_path:
                conn.execute("""
                    UPDATE discord_ocr_processed
                    SET annotation = ?, markdown_path = ?, processed_at = ?
                    WHERE message_id = ? AND attachment_id = ?
                """, (annotation, markdown_path, datetime.now().isoformat(),
                      message_id, attachment_id))
            else:
                conn.execute("""
                    UPDATE discord_ocr_processed
                    SET annotation = ?, processed_at = ?
                    WHERE message_id = ? AND attachment_id = ?
                """, (annotation, datetime.now().isoformat(),
                      message_id, attachment_id))
            conn.commit()
