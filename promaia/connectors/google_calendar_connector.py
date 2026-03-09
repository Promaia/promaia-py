"""
Google Calendar connector for Promaia.

Syncs calendar events into SQLite so agents can query them via query_sql and query_source.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

logger = logging.getLogger(__name__)


class GoogleCalendarConnector(BaseConnector):
    """Google Calendar API connector for event synchronization."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.workspace = config.get("workspace", "default")
        self.calendar_id = config.get("database_id", "primary")
        self.email = config.get("email") or config.get("google_account") or config.get("database_id")
        self.service = None

    async def connect(self, allow_interactive=False) -> bool:
        try:
            from promaia.auth.registry import get_integration
            from googleapiclient.discovery import build

            google_int = get_integration("google")
            creds = google_int.get_google_credentials(account=self.email)
            if not creds:
                # Fallback: try each authenticated account
                for acct in google_int.list_authenticated_accounts():
                    creds = google_int.get_google_credentials(account=acct)
                    if creds:
                        self.email = acct
                        break
            if not creds:
                self.logger.error("No Google credentials available")
                return False
            self.service = build('calendar', 'v3', credentials=creds)
            self.logger.info(f"Connected to Google Calendar: {self.calendar_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Google Calendar: {e}")
            return False

    async def test_connection(self) -> bool:
        try:
            if not self.service:
                await self.connect()
            self.service.calendarList().list(maxResults=1).execute()
            return True
        except Exception as e:
            self.logger.error(f"Calendar connection test failed: {e}")
            return False

    async def get_database_schema(self) -> Dict[str, Any]:
        return {
            "calendar_events": {
                "summary": {"type": "text"},
                "start": {"type": "date"},
                "end": {"type": "date"},
                "location": {"type": "text"},
                "status": {"type": "select"},
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
        if not self.service:
            await self.connect()

        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(days=30)).isoformat()
        time_max = (now + timedelta(days=30)).isoformat()

        if date_filter:
            if date_filter.start_date:
                time_min = date_filter.start_date.isoformat()
            if date_filter.end_date:
                time_max = date_filter.end_date.isoformat()

        events_result = self.service.events().list(
            calendarId=self.calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime',
            maxResults=limit or 250,
        ).execute()

        return events_result.get('items', [])

    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        if not self.service:
            await self.connect()
        return self.service.events().get(
            calendarId=self.calendar_id, eventId=page_id
        ).execute()

    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        return await self.get_page_content(page_id)

    async def sync_to_local(self, *args, **kwargs) -> SyncResult:
        raise NotImplementedError("Use sync_to_local_unified for Calendar connector")

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
        """Sync calendar events into SQLite."""
        import sqlite3 as _sqlite3
        from promaia.utils.env_writer import get_db_path

        result = SyncResult()
        result.start_time = datetime.now()
        result.database_name = getattr(db_config, "name", "google_calendar")

        try:
            if not self.service:
                await self.connect()

            db_file = str(get_db_path())
            conn = _sqlite3.connect(db_file)
            conn.row_factory = _sqlite3.Row
            try:
                self._ensure_tables(conn)

                # Determine time range
                now = datetime.now(timezone.utc)
                days_back = 30
                if date_filter and date_filter.days_back:
                    days_back = date_filter.days_back
                time_min = (now - timedelta(days=days_back)).isoformat()
                time_max = (now + timedelta(days=30)).isoformat()

                # Fetch events
                page_token = None
                all_events = []
                while True:
                    events_result = self.service.events().list(
                        calendarId=self.calendar_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        orderBy='startTime',
                        maxResults=250,
                        pageToken=page_token,
                    ).execute()
                    result.api_calls_count += 1
                    items = events_result.get('items', [])
                    all_events.extend(items)
                    page_token = events_result.get('nextPageToken')
                    if not page_token:
                        break

                result.pages_fetched = len(all_events)
                self.logger.info(f"Fetched {len(all_events)} calendar events")

                # Upsert events
                now_str = datetime.now(timezone.utc).isoformat()
                cursor = conn.cursor()

                for event in all_events:
                    event_id = event.get('id', '')
                    if not event_id:
                        continue

                    summary = event.get('summary', '(No title)')
                    start = event.get('start', {})
                    end = event.get('end', {})
                    start_str = start.get('dateTime') or start.get('date', '')
                    end_str = end.get('dateTime') or end.get('date', '')
                    location = event.get('location', '')
                    attendees = event.get('attendees', [])
                    attendee_list = ', '.join(
                        a.get('email', '') for a in attendees
                    )
                    description = event.get('description', '')
                    status = event.get('status', '')

                    # Build readable content
                    content_parts = [f"## {summary}", f"{start_str} - {end_str}"]
                    if location:
                        content_parts.append(f"Location: {location}")
                    if attendee_list:
                        content_parts.append(f"Attendees: {attendee_list}")
                    if description:
                        content_parts.append(f"\n{description}")
                    content = '\n'.join(content_parts)

                    # Store structured properties as JSON
                    properties = json.dumps({
                        "start": start_str,
                        "end": end_str,
                        "location": location,
                        "attendees": [a.get('email', '') for a in attendees],
                        "status": status,
                        "organizer": event.get('organizer', {}).get('email', ''),
                        "html_link": event.get('htmlLink', ''),
                        "recurring_event_id": event.get('recurringEventId', ''),
                    })

                    try:
                        cursor.execute("""
                            INSERT INTO calendar_events (
                                page_id, workspace, database_id, file_path,
                                title, content, properties, source_type,
                                created_at, updated_at, last_synced
                            ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'calendar', ?, ?, ?)
                            ON CONFLICT(page_id, workspace, database_id) DO UPDATE SET
                                title = excluded.title,
                                content = excluded.content,
                                properties = excluded.properties,
                                updated_at = excluded.updated_at,
                                last_synced = excluded.last_synced
                        """, (
                            event_id, self.workspace, self.calendar_id,
                            summary, content, properties,
                            event.get('created', now_str),
                            event.get('updated', now_str),
                            now_str,
                        ))
                        result.pages_saved += 1
                    except Exception as e:
                        self.logger.error(f"Failed to upsert event {event_id}: {e}")
                        result.add_error(f"event {event_id}: {e}")

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        except Exception as e:
            self.logger.error(f"Calendar sync failed: {e}", exc_info=True)
            result.add_error(f"Calendar sync failed: {e}")

        result.end_time = datetime.now()
        self.logger.info(
            f"Calendar sync complete: {result.pages_fetched} fetched, "
            f"{result.pages_saved} saved ({result.duration_seconds:.1f}s)"
        )
        return result

    @staticmethod
    def _ensure_tables(conn):
        """Create calendar_events table if it doesn't exist."""
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                page_id TEXT,
                workspace TEXT,
                database_id TEXT,
                file_path TEXT,
                title TEXT,
                content TEXT,
                properties TEXT,
                source_type TEXT DEFAULT 'calendar',
                created_at TEXT,
                updated_at TEXT,
                last_synced TEXT,
                UNIQUE(page_id, workspace, database_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_calendar_events_workspace
            ON calendar_events(workspace, database_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_calendar_events_title
            ON calendar_events(title)
        """)
        conn.commit()
