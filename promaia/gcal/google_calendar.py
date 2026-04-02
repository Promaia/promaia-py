"""
Google Calendar integration for scheduling agents.

This allows agents to be managed like team members on your calendar:
- Create recurring calendar events for agents
- Agents run when their calendar event triggers
- Manage scheduling visually in Google Calendar
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from pathlib import Path
from urllib.parse import parse_qs
import json
import threading
import time

logger = logging.getLogger(__name__)

# Fixed port for the OAuth callback server.  Must be mapped in docker-compose
# when running inside a container (ports: ["8085:8085"]).
OAUTH_CALLBACK_PORT = int(os.environ.get("PROMAIA_OAUTH_PORT", "8085"))


def run_oauth_flow_headless(credentials_path: str, scopes: list):
    """Run an OAuth flow that works in headless / Docker environments.

    Instead of opening a browser, prints the authorization URL so the user
    can open it on the host machine.  The callback server binds to 0.0.0.0
    (reachable through Docker port mapping) while the redirect URI uses
    ``localhost`` (required by Google's OAuth for installed apps).

    Returns the authenticated ``Credentials`` object.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    from wsgiref.simple_server import make_server, WSGIRequestHandler

    # Allow HTTP for localhost OAuth callback (oauthlib requires HTTPS by default)
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    port = OAUTH_CALLBACK_PORT

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
    flow.redirect_uri = f"http://localhost:{port}/"

    auth_url, _ = flow.authorization_url(prompt="consent")

    logger.info("OAuth: waiting for authorization on port %s", port)
    print(
        f"\n  Authorize Google Calendar by visiting this URL:\n"
        f"\n    {auth_url}\n"
    )

    # Capture the authorization code from the redirect.
    authorization_response = {}
    got_code = threading.Event()

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

    def _app(environ, start_response):
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        # Store the full URL so flow.fetch_token can extract the code.
        authorization_response["url"] = (
            f"http://localhost:{port}{environ.get('PATH_INFO', '/')}"
            f"?{environ.get('QUERY_STRING', '')}"
        )
        got_code.set()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        if qs.get("error"):
            return [b"<h2>Authorization failed.</h2><p>You can close this tab.</p>"]
        return [b"<h2>Authorized!</h2><p>You can close this tab.</p>"]

    server = make_server("0.0.0.0", port, _app, handler_class=_QuietHandler)

    # Run the server in a daemon thread so we aren't blocked by
    # handle_request() holding the connection open.
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    if not got_code.wait(timeout=120):
        server.shutdown()
        raise RuntimeError("OAuth flow timed out — no callback within 2 minutes")

    server.shutdown()
    server_thread.join(timeout=5)

    if "url" not in authorization_response:
        raise RuntimeError("No authorization response received")

    flow.fetch_token(authorization_response=authorization_response["url"])
    return flow.credentials


class GoogleCalendarManager:
    """Manage agents as Google Calendar events."""

    def __init__(self, credentials_path: Optional[str] = None, account: Optional[str] = None):
        """
        Initialize Google Calendar manager.

        Args:
            credentials_path: Unused (kept for interface compatibility).
            account: Google account email to use for authentication.
        """
        self.service = None
        self.calendar_id = "primary"  # Use primary calendar by default
        self.account = account

    def authenticate(self) -> bool:
        """
        Authenticate with Google Calendar API via the auth module.

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            from promaia.auth.registry import get_integration
            from googleapiclient.discovery import build

            google_int = get_integration("google")
            creds = google_int.get_google_credentials(account=self.account)
            if creds:
                self.service = build('calendar', 'v3', credentials=creds)
                logger.info("Authenticated with Google Calendar (account=%s)", self.account or "default")
                return True

            logger.error("Google not configured. Run: maia auth configure google")
            return False
        except Exception as e:
            logger.error(f"Error authenticating with Google Calendar: {e}")
            return False

    def create_agent_event(
        self,
        agent_name: str,
        schedule: List[tuple],  # [(day, time), ...]
        agent_config: Dict[str, Any],
        calendar_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Create recurring calendar events for an agent.

        Args:
            agent_name: Name of the agent
            schedule: List of (day, time) tuples like [("Mon", "09:00"), ...]
            agent_config: Full agent configuration
            calendar_id: Optional calendar ID (defaults to primary)

        Returns:
            Event ID if successful, None otherwise
        """
        if not self.service:
            if not self.authenticate():
                return None

        cal_id = calendar_id or self.calendar_id

        try:
            # Group schedule by unique times to create fewer events with multiple recurrences
            # For now, create one event per schedule entry
            # Future optimization: group by time and create RRULE with BYDAY

            event_ids = []

            for day, time in schedule:
                # Parse time
                hour, minute = map(int, time.split(':'))

                # Map day names to weekday numbers (MO, TU, WE, etc.)
                day_map = {
                    "Mon": "MO", "Tue": "TU", "Wed": "WE",
                    "Thu": "TH", "Fri": "FR", "Sat": "SA", "Sun": "SU"
                }
                weekday = day_map.get(day, "MO")

                # Create event
                # Start from next occurrence of this day
                today = datetime.now()
                days_ahead = (list(day_map.keys()).index(day) - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7  # Start from next week

                next_occurrence = today + timedelta(days=days_ahead)
                start_datetime = next_occurrence.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0
                )

                # Event lasts 5 minutes (just a placeholder)
                end_datetime = start_datetime + timedelta(minutes=5)

                # Get local timezone
                # Use time.tzname to get local timezone (e.g., 'PST', 'EST')
                # Or detect IANA timezone (e.g., 'America/Los_Angeles')
                import subprocess
                try:
                    # Try to get IANA timezone on macOS/Linux
                    if os.path.exists('/etc/localtime'):
                        tz_result = subprocess.run(['readlink', '/etc/localtime'],
                                                  capture_output=True, text=True)
                        if tz_result.returncode == 0:
                            # Extract timezone from path like /var/db/timezone/zoneinfo/America/Los_Angeles
                            tz_path = tz_result.stdout.strip()
                            if 'zoneinfo/' in tz_path:
                                local_timezone = tz_path.split('zoneinfo/')[-1]
                            else:
                                local_timezone = 'America/Los_Angeles'  # Default fallback
                        else:
                            local_timezone = 'America/Los_Angeles'
                    else:
                        local_timezone = 'America/Los_Angeles'
                except:
                    local_timezone = 'America/Los_Angeles'

                event = {
                    'summary': f'🤖 {agent_name}',
                    'description': self._format_agent_description(agent_config),
                    'start': {
                        'dateTime': start_datetime.isoformat(),
                        'timeZone': local_timezone,
                    },
                    'end': {
                        'dateTime': end_datetime.isoformat(),
                        'timeZone': local_timezone,
                    },
                    'recurrence': [
                        f'RRULE:FREQ=WEEKLY;BYDAY={weekday}'
                    ],
                    'reminders': {
                        'useDefault': False,
                        'overrides': [],
                    },
                    # Store agent metadata in extended properties
                    'extendedProperties': {
                        'private': {
                            'promaia_agent': 'true',
                            'agent_name': agent_name,
                            'agent_schedule_entry': f'{day}_{time}'
                        }
                    },
                    # Use a specific color for agents
                    'colorId': '9'  # Blue color
                }

                result = self.service.events().insert(calendarId=cal_id, body=event).execute()
                event_ids.append(result.get('id'))
                logger.info(f"Created calendar event for {agent_name} on {day} at {time}")

            # Store all event IDs in agent metadata
            return ','.join(event_ids)

        except Exception as e:
            logger.error(f"Error creating calendar event: {e}")
            return None

    def _format_agent_description(self, agent_config: Dict[str, Any]) -> str:
        """Format agent config as calendar event description."""
        parts = [
            f"Promaia Agent: {agent_config.get('name')}",
            "",
            f"Workspace: {agent_config.get('workspace')}",
            f"Databases: {', '.join(agent_config.get('databases', []))}",
            f"Output: {agent_config.get('output_notion_page_id')}",
        ]

        if agent_config.get('description'):
            parts.extend(["", agent_config['description']])

        parts.extend([
            "",
            "---",
            "This event is managed by Promaia.",
            "The agent will run automatically when this event occurs."
        ])

        return "\n".join(parts)

    def delete_agent_events(
        self,
        agent_name: str,
        calendar_id: Optional[str] = None
    ) -> bool:
        """
        Delete all calendar events for an agent.

        Args:
            agent_name: Name of the agent
            calendar_id: Optional calendar ID

        Returns:
            True if successful
        """
        if not self.service:
            if not self.authenticate():
                return False

        cal_id = calendar_id or self.calendar_id

        try:
            # Search for events with this agent name
            events_result = self.service.events().list(
                calendarId=cal_id,
                privateExtendedProperty=f'agent_name={agent_name}',
                maxResults=100
            ).execute()

            events = events_result.get('items', [])

            for event in events:
                self.service.events().delete(
                    calendarId=cal_id,
                    eventId=event['id']
                ).execute()
                logger.info(f"Deleted calendar event {event['id']} for {agent_name}")

            return True

        except Exception as e:
            logger.error(f"Error deleting calendar events: {e}")
            return False

    def list_agent_events(
        self,
        agent_name: Optional[str] = None,
        calendar_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all agent events on the calendar.

        Args:
            agent_name: Optional filter by agent name
            calendar_id: Optional calendar ID

        Returns:
            List of event dictionaries
        """
        if not self.service:
            if not self.authenticate():
                return []

        cal_id = calendar_id or self.calendar_id

        try:
            # Search for agent events
            query = 'promaia_agent=true'
            if agent_name:
                query = f'agent_name={agent_name}'

            events_result = self.service.events().list(
                calendarId=cal_id,
                privateExtendedProperty=query,
                maxResults=100,
                singleEvents=False  # Include recurring events
            ).execute()

            return events_result.get('items', [])

        except Exception as e:
            logger.error(f"Error listing calendar events: {e}")
            return []

    def get_upcoming_agent_runs(
        self,
        hours_ahead: int = 24,
        calendar_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get upcoming agent runs in the next N hours.

        Args:
            hours_ahead: How many hours to look ahead
            calendar_id: Optional calendar ID

        Returns:
            List of upcoming events with agent metadata
        """
        # Always re-authenticate so we pick up freshly-refreshed tokens
        # from the proxy auth flow (tokens expire hourly).
        if not self.authenticate():
            return []

        cal_id = calendar_id or self.calendar_id

        try:
            # Get upcoming events using timezone-aware datetime
            now = datetime.now(timezone.utc)
            time_max = now + timedelta(hours=hours_ahead)

            # Since each agent has its own dedicated calendar, any event on that
            # calendar should trigger the agent - no need for promaia_agent property
            # Format times properly for RFC3339 (Google Calendar API requirement)
            time_min_str = now.isoformat().replace('+00:00', 'Z')
            time_max_str = time_max.isoformat().replace('+00:00', 'Z')

            logger.debug(f"Querying calendar {cal_id} for events from {time_min_str} to {time_max_str}")

            events_result = self.service.events().list(
                calendarId=cal_id,
                timeMin=time_min_str,
                timeMax=time_max_str,
                singleEvents=True,  # Expand recurring events
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            logger.debug(f"Found {len(events)} events in the specified time range")

            # Extract event info - any event on the agent's calendar triggers it
            upcoming = []
            for event in events:
                # Get extended properties if they exist (for backward compatibility)
                props = event.get('extendedProperties', {}).get('private', {})

                upcoming.append({
                    'event_id': event['id'],
                    'agent_name': props.get('agent_name'),  # May be None for manual events
                    'start': event['start'].get('dateTime') or event['start'].get('date'),
                    'summary': event.get('summary'),
                    'description': event.get('description', ''),
                    'html_link': event.get('htmlLink', ''),
                })

            return upcoming

        except Exception as e:
            logger.error(f"Error getting upcoming runs: {e}")
            return []

    def create_agent_calendar(
        self,
        agent_name: str,
        description: str = ""
    ) -> Optional[str]:
        """
        Create a dedicated calendar for an agent.

        Args:
            agent_name: Name of the agent (used as calendar summary)
            description: Calendar description

        Returns:
            Calendar ID if successful, None otherwise
        """
        if not self.service:
            if not self.authenticate():
                return None

        try:
            calendar = {
                'summary': agent_name,
                'description': description or f"Automated schedule for {agent_name} agent",
                'timeZone': 'UTC'
            }

            result = self.service.calendars().insert(body=calendar).execute()
            calendar_id = result.get('id')

            logger.info(f"Created calendar '{agent_name}' with ID: {calendar_id}")
            return calendar_id

        except Exception as e:
            logger.error(f"Error creating calendar: {e}")
            return None

    def delete_agent_calendar(self, calendar_id: str) -> bool:
        """
        Delete an agent's calendar.

        Args:
            calendar_id: Calendar ID to delete

        Returns:
            True if successful, False otherwise
        """
        if not self.service:
            if not self.authenticate():
                return False

        try:
            self.service.calendars().delete(calendarId=calendar_id).execute()
            logger.info(f"Deleted calendar: {calendar_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting calendar: {e}")
            return False

    def list_agent_calendars(self) -> List[Dict[str, Any]]:
        """
        List all calendars (can be filtered for agent calendars if needed).

        Returns:
            List of calendar dictionaries with id, summary, description
        """
        if not self.service:
            if not self.authenticate():
                return []

        try:
            calendar_list = self.service.calendarList().list().execute()
            calendars = calendar_list.get('items', [])

            # Return simplified calendar info
            return [
                {
                    'id': cal.get('id'),
                    'summary': cal.get('summary'),
                    'description': cal.get('description', ''),
                    'primary': cal.get('primary', False)
                }
                for cal in calendars
            ]

        except Exception as e:
            logger.error(f"Error listing calendars: {e}")
            return []

    def share_calendar(
        self,
        calendar_id: str,
        email: str,
        role: str = 'reader'
    ) -> bool:
        """
        Share a calendar with a user.

        Args:
            calendar_id: Calendar ID to share
            email: Email address to share with
            role: Permission role ('reader', 'writer', 'owner')

        Returns:
            True if successful, False otherwise
        """
        if not self.service:
            if not self.authenticate():
                return False

        try:
            rule = {
                'scope': {
                    'type': 'user',
                    'value': email
                },
                'role': role
            }

            self.service.acl().insert(
                calendarId=calendar_id,
                body=rule
            ).execute()

            logger.info(f"Shared calendar {calendar_id} with {email} as {role}")
            return True

        except Exception as e:
            logger.error(f"Error sharing calendar: {e}")
            return False


def google_account_for_workspace(workspace: str | None) -> str | None:
    """Resolve the Google account email for a workspace.

    Looks up gmail databases configured for the workspace and returns
    the first ``database_id`` (which is the account email).  Returns
    ``None`` when no gmail database is found or the workspace is unset.
    """
    if not workspace:
        return None
    try:
        from promaia.config.databases import get_database_manager

        db_manager = get_database_manager()
        for name in db_manager.list_databases(workspace=workspace):
            db = db_manager.get_database(name, workspace=workspace)
            if db and db.source_type == "gmail" and db.database_id:
                return db.database_id
    except Exception:
        pass
    return None


def get_calendar_manager(account: str | None = None) -> GoogleCalendarManager:
    """Get a calendar manager instance, cached per account."""
    global _calendar_managers
    if '_calendar_managers' not in globals():
        _calendar_managers = {}

    key = (account or "").lower()
    if key not in _calendar_managers:
        _calendar_managers[key] = GoogleCalendarManager(account=account)
    return _calendar_managers[key]
