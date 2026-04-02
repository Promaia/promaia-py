"""
Gmail Tools MCP Server (Write-Only)

External stdio MCP server that exposes Gmail WRITE tools to Claude Agent SDK.
Read operations are handled through Promaia's unified query layer.

Usage:
    python -m promaia.mcp.gmail_tools_server --workspace koii --agent-id my-agent
"""
import asyncio
import sys
import logging
import argparse
from typing import List

# MCP Server imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("ERROR: mcp package not installed. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

# Promaia imports
from promaia.connectors.gmail_connector import GmailConnector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[Gmail MCP] %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Server instance
server = Server("promaia-gmail-tools")

# Global state
WORKSPACE = None
AGENT_CONFIG = None
GMAIL_CONNECTOR = None


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Gmail write tools"""
    return [
        Tool(
            name="send_message",
            description="Send a new email message. Use Promaia query tools to read emails.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email (comma-separated for multiple)"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body (plain text or HTML)"},
                    "cc": {"type": "string", "description": "CC recipients (optional)"}
                },
                "required": ["to", "subject", "body"]
            }
        ),
        Tool(
            name="create_draft",
            description="Create an email draft (not sent). Use Promaia query tools to read emails.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body"}
                },
                "required": ["to", "subject", "body"]
            }
        ),
        Tool(
            name="reply_to_message",
            description="Reply to an email. Use Promaia query tools to find the thread_id and message_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "Gmail thread ID"},
                    "message_id": {"type": "string", "description": "Original message ID"},
                    "body": {"type": "string", "description": "Reply body text"}
                },
                "required": ["thread_id", "message_id", "body"]
            }
        ),
        Tool(
            name="draft_reply",
            description="Create a draft reply to an existing email thread (not sent). Use Promaia query tools to find the thread_id and message_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "Gmail thread ID"},
                    "message_id": {"type": "string", "description": "Original message ID to reply to"},
                    "body": {"type": "string", "description": "Reply body text"}
                },
                "required": ["thread_id", "message_id", "body"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a Gmail tool"""
    logger.info(f"Tool call: {name}")

    try:
        await _ensure_connected()

        if name == "send_message":
            return await _handle_send_message(arguments)
        elif name == "create_draft":
            return await _handle_create_draft(arguments)
        elif name == "reply_to_message":
            return await _handle_reply_to_message(arguments)
        elif name == "draft_reply":
            return await _handle_draft_reply(arguments)
        else:
            return [TextContent(type="text", text=f"❌ Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]


async def _ensure_connected():
    """Ensure Gmail connector is connected"""
    global GMAIL_CONNECTOR

    if GMAIL_CONNECTOR is None:
        from promaia.config.databases import get_database_config

        gmail_db = get_database_config(f"{WORKSPACE}.gmail") or get_database_config("gmail")
        if not gmail_db:
            raise RuntimeError(f"No Gmail configured for workspace {WORKSPACE}")

        email = gmail_db.get("database_id")
        config = {"database_id": email, "workspace": WORKSPACE}

        GMAIL_CONNECTOR = GmailConnector(config)
        if not await GMAIL_CONNECTOR.connect(allow_interactive=False):
            raise RuntimeError(f"Failed to connect to Gmail: {email}")

        logger.info(f"✓ Connected to Gmail: {email}")


async def _handle_send_message(args: dict) -> list[TextContent]:
    """Send email"""
    success = await GMAIL_CONNECTOR.send_email(
        to=args["to"],
        subject=args["subject"],
        body_text=args["body"],
        cc=args.get("cc")
    )
    return [TextContent(type="text", text="✓ Email sent" if success else "❌ Send failed")]


async def _handle_create_draft(args: dict) -> list[TextContent]:
    """Create draft"""
    draft_id = await GMAIL_CONNECTOR._create_draft(
        to=args["to"],
        subject=args["subject"],
        body=args["body"]
    )
    return [TextContent(type="text", text=f"✓ Draft created: {draft_id}" if draft_id else "❌ Draft creation failed")]


async def _handle_reply_to_message(args: dict) -> list[TextContent]:
    """Send reply"""
    # Get original message for subject
    original = await GMAIL_CONNECTOR._get_message(args["message_id"])
    if not original:
        return [TextContent(type="text", text="❌ Original message not found")]

    headers = original.get('payload', {}).get('headers', [])
    subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
    if not subject.lower().startswith('re:'):
        subject = f"Re: {subject}"

    success = await GMAIL_CONNECTOR.send_reply(
        thread_id=args["thread_id"],
        message_id=args["message_id"],
        subject=subject,
        body_text=args["body"]
    )
    return [TextContent(type="text", text="✓ Reply sent" if success else "❌ Reply failed")]


async def _handle_draft_reply(args: dict) -> list[TextContent]:
    """Create a draft reply in an existing thread"""
    original = await GMAIL_CONNECTOR._get_message(args["message_id"])
    if not original:
        return [TextContent(type="text", text="❌ Original message not found")]

    headers = {h['name'].lower(): h['value']
               for h in original.get('payload', {}).get('headers', [])}

    subject = headers.get('subject', '')
    if not subject.lower().startswith('re:'):
        subject = f"Re: {subject}"

    reply_to = headers.get('reply-to') or headers.get('from')

    # Build references chain for threading
    existing_refs = headers.get('references', '')
    msg_id_header = headers.get('message-id', '')
    references = f"{existing_refs} {msg_id_header}".strip() if msg_id_header else existing_refs or None

    # Build quoted reply body
    full_body = GMAIL_CONNECTOR._build_quoted_reply(args["body"], original)

    draft_id = await GMAIL_CONNECTOR._create_draft(
        to=reply_to,
        subject=subject,
        body=full_body,
        thread_id=args["thread_id"],
        in_reply_to=msg_id_header,
        references=references,
    )
    if draft_id:
        return [TextContent(type="text", text=f"✓ Draft reply created: {draft_id}")]
    return [TextContent(type="text", text="❌ Draft reply creation failed")]


async def main():
    """Run the MCP server"""
    global WORKSPACE, AGENT_CONFIG

    parser = argparse.ArgumentParser(description="Gmail Tools MCP Server (Write-Only)")
    parser.add_argument("--workspace", required=True, help="Workspace name")
    parser.add_argument("--agent-id", required=False, help="Agent ID")
    args = parser.parse_args()

    WORKSPACE = args.workspace
    logger.info(f"Starting Gmail MCP (write-only) for workspace: {WORKSPACE}")

    if args.agent_id:
        try:
            from promaia.agents.agent_config import get_agent
            AGENT_CONFIG = get_agent(args.agent_id)
            if AGENT_CONFIG:
                logger.info(f"Agent: {args.agent_id}")
        except Exception as e:
            logger.warning(f"Could not load agent config: {e}")

    logger.info("Tools: send_message, create_draft, reply_to_message, draft_reply")
    logger.info("Note: Use Promaia query tools for reading emails")

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
