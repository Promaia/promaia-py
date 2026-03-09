"""
Calendar Tools MCP Server (Write-Only)

External stdio MCP server that exposes Google Calendar WRITE tools to Claude Agent SDK.
Read operations are handled through Promaia's unified query layer (if calendar is synced).

Usage:
    python -m promaia.mcp.calendar_tools_server --workspace acme --agent-id my-agent
"""
import asyncio
import sys
import logging
import argparse
from datetime import datetime, timedelta
from typing import List, Optional

# MCP Server imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("ERROR: mcp package not installed. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[Calendar MCP] %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Server instance
server = Server("promaia-calendar-tools")

# Global state
WORKSPACE = None
GOOGLE_ACCOUNT = None
AGENT_CONFIG = None
CALENDAR_SERVICE = None


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Calendar write tools"""
    return [
        Tool(
            name="create_event",
            description="Create a new calendar event. Use Promaia query tools to read calendar data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title/summary"},
                    "description": {"type": "string", "description": "Event description (optional)"},
                    "start_time": {"type": "string", "description": "Start time (ISO 8601 format: 2026-02-01T14:00:00)"},
                    "end_time": {"type": "string", "description": "End time (ISO 8601 format: 2026-02-01T15:00:00)"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"}
                },
                "required": ["summary", "start_time", "end_time"]
            }
        ),
        Tool(
            name="update_event",
            description="Update an existing calendar event. Use Promaia query tools to find event_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Event ID to update"},
                    "summary": {"type": "string", "description": "New event title (optional)"},
                    "description": {"type": "string", "description": "New description (optional)"},
                    "start_time": {"type": "string", "description": "New start time (ISO 8601, optional)"},
                    "end_time": {"type": "string", "description": "New end time (ISO 8601, optional)"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"}
                },
                "required": ["event_id"]
            }
        ),
        Tool(
            name="delete_event",
            description="Delete a calendar event. Use Promaia query tools to find event_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Event ID to delete"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"}
                },
                "required": ["event_id"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a Calendar tool"""
    logger.info(f"Tool call: {name}")

    try:
        await _ensure_authenticated()

        if name == "create_event":
            return await _handle_create_event(arguments)
        elif name == "update_event":
            return await _handle_update_event(arguments)
        elif name == "delete_event":
            return await _handle_delete_event(arguments)
        else:
            return [TextContent(type="text", text=f"❌ Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]


async def _ensure_authenticated():
    """Ensure Calendar service is authenticated via the auth module."""
    global CALENDAR_SERVICE

    if CALENDAR_SERVICE is None:
        from promaia.auth.registry import get_integration
        from googleapiclient.discovery import build

        google_int = get_integration("google")
        creds = google_int.get_google_credentials(account=GOOGLE_ACCOUNT)
        if not creds:
            raise RuntimeError(
                "Google not configured. Run: maia auth configure google"
            )

        CALENDAR_SERVICE = build('calendar', 'v3', credentials=creds)
        logger.info("Authenticated with Google Calendar (account=%s)", GOOGLE_ACCOUNT or "default")


async def _handle_create_event(args: dict) -> list[TextContent]:
    """Create calendar event"""
    calendar_id = args.get("calendar_id", "primary")

    event_body = {
        'summary': args['summary'],
        'start': {'dateTime': args['start_time'], 'timeZone': 'UTC'},
        'end': {'dateTime': args['end_time'], 'timeZone': 'UTC'}
    }

    if args.get('description'):
        event_body['description'] = args['description']

    try:
        event = CALENDAR_SERVICE.events().insert(
            calendarId=calendar_id,
            body=event_body
        ).execute()

        event_id = event.get('id')
        event_link = event.get('htmlLink')

        return [TextContent(
            type="text",
            text=f"✓ Event created\nID: {event_id}\nLink: {event_link}"
        )]
    except Exception as e:
        return [TextContent(type="text", text=f"❌ Failed to create event: {e}")]


async def _handle_update_event(args: dict) -> list[TextContent]:
    """Update calendar event"""
    event_id = args['event_id']
    calendar_id = args.get("calendar_id", "primary")

    try:
        # Get existing event
        event = CALENDAR_SERVICE.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        # Update fields if provided
        if 'summary' in args:
            event['summary'] = args['summary']
        if 'description' in args:
            event['description'] = args['description']
        if 'start_time' in args:
            event['start'] = {'dateTime': args['start_time'], 'timeZone': 'UTC'}
        if 'end_time' in args:
            event['end'] = {'dateTime': args['end_time'], 'timeZone': 'UTC'}

        updated_event = CALENDAR_SERVICE.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()

        return [TextContent(type="text", text=f"✓ Event updated: {event_id}")]
    except Exception as e:
        return [TextContent(type="text", text=f"❌ Failed to update event: {e}")]


async def _handle_delete_event(args: dict) -> list[TextContent]:
    """Delete calendar event"""
    event_id = args['event_id']
    calendar_id = args.get("calendar_id", "primary")

    try:
        CALENDAR_SERVICE.events().delete(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        return [TextContent(type="text", text=f"✓ Event deleted: {event_id}")]
    except Exception as e:
        return [TextContent(type="text", text=f"❌ Failed to delete event: {e}")]


async def main():
    """Run the MCP server"""
    global WORKSPACE, GOOGLE_ACCOUNT, AGENT_CONFIG

    parser = argparse.ArgumentParser(description="Calendar Tools MCP Server (Write-Only)")
    parser.add_argument("--workspace", required=True, help="Workspace name")
    parser.add_argument("--account", required=False, help="Google account email for authentication")
    parser.add_argument("--agent-id", required=False, help="Agent ID")
    args = parser.parse_args()

    WORKSPACE = args.workspace
    GOOGLE_ACCOUNT = getattr(args, "account", None)
    logger.info(f"Starting Calendar MCP (write-only) for workspace: {WORKSPACE}")

    if args.agent_id:
        try:
            from promaia.agents.agent_config import get_agent
            AGENT_CONFIG = get_agent(args.agent_id)
            if AGENT_CONFIG:
                logger.info(f"Agent: {args.agent_id}")
        except Exception as e:
            logger.warning(f"Could not load agent config: {e}")

    logger.info("Tools: create_event, update_event, delete_event")
    logger.info("Note: Use Promaia query tools for reading calendar data")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
