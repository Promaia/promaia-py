"""
Google Sheets connector for Promaia.

Syncs Google Sheets as CSV-per-sheet text with inline formulas, stored in a
SQLite index for discoverability via query_sql and query_source.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

logger = logging.getLogger(__name__)


class GoogleSheetsConnector(BaseConnector):
    """Google Sheets connector — CSV-per-sheet with inline formulas."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.workspace = config.get("workspace", "default")
        # Google account email — stored separately since database_id is the sheet/folder ID
        self.email = config.get("google_account") or config.get("email")
        self._source_id = config.get("folder_id") or config.get("database_id") or "root"
        self._sheets_service = None
        self._drive_service = None

    async def connect(self, allow_interactive=False) -> bool:
        try:
            from promaia.auth.registry import get_integration
            from googleapiclient.discovery import build

            google_int = get_integration("google")
            # Try explicit account first, then fall back to any authenticated account
            account = self.email
            if not account:
                accounts = google_int.list_authenticated_accounts()
                if accounts:
                    account = accounts[0]
            creds = google_int.get_google_credentials(account=account)
            if not creds:
                self.logger.error("No Google credentials available")
                return False
            self._sheets_service = build('sheets', 'v4', credentials=creds)
            self._drive_service = build('drive', 'v3', credentials=creds)
            self.logger.info("Connected to Google Sheets / Drive")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Google Sheets: {e}")
            return False

    async def test_connection(self) -> bool:
        try:
            if not self._drive_service:
                await self.connect()
            self._drive_service.files().list(
                q="mimeType='application/vnd.google-apps.spreadsheet'",
                pageSize=1,
            ).execute()
            return True
        except Exception as e:
            self.logger.error(f"Sheets connection test failed: {e}")
            return False

    async def get_database_schema(self) -> Dict[str, Any]:
        return {
            "google_sheets": {
                "title": {"type": "text"},
                "sheet_names": {"type": "text"},
                "file_path": {"type": "text"},
            }
        }

    async def query_pages(
        self,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        sort_by: Optional[str] = None,
        sort_direction: str = "desc",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not self._drive_service:
            await self.connect()
        return await self._list_all_spreadsheets(limit=limit)

    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        if not self._sheets_service:
            await self.connect()
        return self._sheets_service.spreadsheets().get(
            spreadsheetId=page_id
        ).execute()

    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        return await self.get_page_content(page_id)

    async def sync_to_local(self, *args, **kwargs) -> SyncResult:
        raise NotImplementedError("Use sync_to_local_unified for Sheets connector")

    async def sync_to_local_unified(
        self,
        storage,
        db_config,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        include_properties: bool = True,
        force_update: bool = False,
        excluded_properties: Optional[List[str]] = None,
        complex_filter: Optional[Dict[str, Any]] = None,
    ) -> SyncResult:
        """Sync spreadsheets as CSV-per-sheet text with inline formulas."""
        import sqlite3 as _sqlite3
        from promaia.utils.env_writer import get_db_path

        result = SyncResult()
        result.start_time = datetime.now()
        result.database_name = getattr(db_config, "name", "google_sheets")

        try:
            if not self._drive_service:
                await self.connect()

            db_file = str(get_db_path())
            conn = _sqlite3.connect(db_file)
            conn.row_factory = _sqlite3.Row
            try:
                self._ensure_tables(conn)

                spreadsheets = await self._list_all_spreadsheets()
                result.pages_fetched = len(spreadsheets)
                self.logger.info(f"Found {len(spreadsheets)} spreadsheets")

                now_str = datetime.now(timezone.utc).isoformat()
                cursor = conn.cursor()

                for ss in spreadsheets:
                    ss_id = ss.get('id', '')
                    ss_name = ss.get('name', 'Untitled')
                    if not ss_id:
                        continue

                    try:
                        # Get sheet metadata
                        meta = self._sheets_service.spreadsheets().get(
                            spreadsheetId=ss_id,
                            fields="sheets.properties.title,sheets.properties.gridProperties",
                        ).execute()
                        result.api_calls_count += 1
                        sheets = meta.get('sheets', [])
                        sheet_names = [
                            s.get('properties', {}).get('title', '')
                            for s in sheets
                        ]

                        # Fetch formula and display values for each sheet
                        sections = []
                        row_counts = {}
                        for sheet_title in sheet_names:
                            safe_range = f"'{sheet_title}'"
                            formula_resp = self._sheets_service.spreadsheets().values().get(
                                spreadsheetId=ss_id,
                                range=safe_range,
                                valueRenderOption='FORMULA',
                            ).execute()
                            display_resp = self._sheets_service.spreadsheets().values().get(
                                spreadsheetId=ss_id,
                                range=safe_range,
                                valueRenderOption='FORMATTED_VALUE',
                            ).execute()
                            result.api_calls_count += 2

                            formula_rows = formula_resp.get('values', [])
                            display_rows = display_resp.get('values', [])
                            row_counts[sheet_title] = len(display_rows)

                            csv_text = self._build_inline_csv(formula_rows, display_rows)
                            if len(sheet_names) > 1:
                                sections.append(f"## Sheet: {sheet_title}\n\n{csv_text}")
                            else:
                                sections.append(csv_text)

                        content = "\n\n".join(sections)

                        properties = json.dumps({
                            "sheet_names": sheet_names,
                            "modified_time": ss.get('modifiedTime', ''),
                            "row_counts": row_counts,
                        })

                        cursor.execute("""
                            INSERT INTO google_sheets (
                                page_id, workspace, database_id, file_path,
                                title, content, properties, source_type,
                                created_at, updated_at, last_synced
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'google_sheets', ?, ?, ?)
                            ON CONFLICT(page_id, workspace) DO UPDATE SET
                                title = excluded.title,
                                content = excluded.content,
                                properties = excluded.properties,
                                file_path = NULL,
                                updated_at = excluded.updated_at,
                                last_synced = excluded.last_synced
                        """, (
                            ss_id, self.workspace, self._source_id,
                            None,
                            ss_name, content, properties,
                            ss.get('createdTime', now_str),
                            ss.get('modifiedTime', now_str),
                            now_str,
                        ))
                        result.pages_saved += 1
                    except Exception as e:
                        self.logger.error(f"Failed to sync spreadsheet {ss_id}: {e}")
                        result.add_error(f"spreadsheet {ss_id}: {e}")

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        except Exception as e:
            self.logger.error(f"Sheets sync failed: {e}", exc_info=True)
            result.add_error(f"Sheets sync failed: {e}")

        result.end_time = datetime.now()
        self.logger.info(
            f"Sheets sync complete: {result.pages_fetched} fetched, "
            f"{result.pages_saved} saved ({result.duration_seconds:.1f}s)"
        )
        return result

    async def _list_all_spreadsheets(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """List spreadsheets to sync.

        Supports three modes based on ``_source_id``:
        - ``"root"`` → all accessible spreadsheets
        - A single spreadsheet ID → just that one file
        - A Drive folder ID → spreadsheets inside that folder
        """
        source = self._source_id

        # Try as a single spreadsheet first (unless it's "root")
        if source and source != "root":
            try:
                file_meta = self._drive_service.files().get(
                    fileId=source,
                    fields="id, name, mimeType, modifiedTime, createdTime",
                ).execute()
                if file_meta.get("mimeType") == "application/vnd.google-apps.spreadsheet":
                    self.logger.info(f"Source ID is a single spreadsheet: {file_meta.get('name')}")
                    return [file_meta]
            except Exception:
                pass  # Not a valid file ID or no access — fall through to folder listing

        # Folder or root listing
        q = "mimeType='application/vnd.google-apps.spreadsheet'"
        if source and source != "root":
            q += f" and '{source}' in parents"

        all_files = []
        page_token = None
        while True:
            resp = self._drive_service.files().list(
                q=q,
                pageSize=min(limit or 100, 100),
                fields="nextPageToken, files(id, name, modifiedTime, createdTime)",
                pageToken=page_token,
            ).execute()
            all_files.extend(resp.get('files', []))
            if limit and len(all_files) >= limit:
                all_files = all_files[:limit]
                break
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        return all_files

    @staticmethod
    def _build_inline_csv(formula_rows: List[List], display_rows: List[List]) -> str:
        """Build CSV text with inline formulas: ``{=formula} display_value``."""
        if not formula_rows and not display_rows:
            return ""

        max_rows = max(len(formula_rows), len(display_rows))
        out = io.StringIO()
        writer = csv.writer(out)

        for i in range(max_rows):
            f_row = formula_rows[i] if i < len(formula_rows) else []
            d_row = display_rows[i] if i < len(display_rows) else []
            max_cols = max(len(f_row), len(d_row))

            cells = []
            for j in range(max_cols):
                f_val = str(f_row[j]) if j < len(f_row) else ""
                d_val = str(d_row[j]) if j < len(d_row) else ""
                if f_val.startswith("=") and f_val != d_val:
                    cells.append(f"{{{f_val}}} {d_val}")
                else:
                    cells.append(d_val)
            writer.writerow(cells)

        return out.getvalue().rstrip("\r\n")

    @staticmethod
    def _ensure_tables(conn):
        """Create google_sheets index table if it doesn't exist."""
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS google_sheets (
                page_id TEXT,
                workspace TEXT,
                database_id TEXT,
                file_path TEXT,
                title TEXT,
                content TEXT,
                properties TEXT,
                source_type TEXT DEFAULT 'google_sheets',
                created_at TEXT,
                updated_at TEXT,
                last_synced TEXT,
                UNIQUE(page_id, workspace)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_google_sheets_workspace
            ON google_sheets(workspace)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_google_sheets_title
            ON google_sheets(title)
        """)
        conn.commit()
