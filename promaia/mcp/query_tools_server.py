"""
Promaia Query Tools MCP Server

External stdio MCP server that exposes query_sql, query_vector, and query_source
to Claude Agent SDK.

Usage:
    python -m promaia.mcp.query_tools_server --workspace koii
"""
import asyncio
import json
import sys
import logging
import argparse
from typing import Dict, Any, List

# MCP Server imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("ERROR: mcp package not installed. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

# Promaia imports
from promaia.ai.nl_processor_wrapper import process_natural_language_to_content, process_vector_search_to_content
from promaia.storage.files import load_database_pages_with_filters
from promaia.config.databases import get_database_config
from promaia.ai.prompts import format_context_data

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[MCP Server] %(message)s',
    stream=sys.stderr  # Log to stderr so stdout is clean for MCP protocol
)
logger = logging.getLogger(__name__)

# Server instance
server = Server("promaia-query-tools")

# Global state (workspace and agent config passed via args)
WORKSPACE = None
AGENT_CONFIG = None
ALLOWED_CHANNEL_IDS = None  # Optional[List[str]] — restrict query results to these channels
ALLOWED_TOOLS = None  # Optional[set[str]] — when set, only these tool names are advertised/dispatched


def _filter_pages_by_channel(pages: list, allowed_ids: list) -> list:
    """Keep only pages whose channel ID is in *allowed_ids*.

    Works for Slack and Discord pages whose metadata stores a channel ID
    under ``slack_channel_id`` or ``discord_channel_id``.  Pages without
    any channel metadata are kept (they're not messaging data).
    """
    allowed_set = set(allowed_ids)
    filtered = []
    for page in pages:
        meta_raw = page.get("metadata")
        if not meta_raw:
            filtered.append(page)
            continue

        # metadata may be a JSON string (from SQLite) or already a dict
        if isinstance(meta_raw, str):
            try:
                meta = json.loads(meta_raw)
            except (json.JSONDecodeError, TypeError):
                filtered.append(page)
                continue
        else:
            meta = meta_raw

        ch_id = meta.get("slack_channel_id") or meta.get("discord_channel_id")
        if ch_id is None or ch_id in allowed_set:
            filtered.append(page)
    return filtered


def _filter_loaded_content_by_channel(
    loaded_content: dict, allowed_ids: list
) -> dict:
    """Apply channel filtering across a multi-source loaded_content dict.

    Sources whose names contain 'slack' or 'discord' are filtered;
    all others are passed through unchanged.
    """
    result = {}
    for source_name, pages in loaded_content.items():
        if any(kw in source_name.lower() for kw in ("slack", "discord")):
            result[source_name] = _filter_pages_by_channel(pages, allowed_ids)
        else:
            result[source_name] = pages
    return result


def _filter_tools_by_permission(tools: list) -> list:
    """Drop any tools not in ALLOWED_TOOLS (None means no restriction)."""
    if ALLOWED_TOOLS is None:
        return tools
    return [t for t in tools if t.name in ALLOWED_TOOLS]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available query tools"""
    all_tools = [
        Tool(
            name="query_sql",
            description="Execute SQL queries using natural language to search for exact text/keywords in content. "
                       "Use this for specific data retrieval when you know what you're looking for. "
                       "Examples: 'Find emails from Federico', 'Show tasks assigned to Alice due this week'",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query (searches for EXACT TEXT/KEYWORDS, not abstract concepts)"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why you need this data (helps with understanding context)"
                    }
                },
                "required": ["query", "reasoning"]
            }
        ),
        Tool(
            name="query_vector",
            description="Semantic search across all sources using embeddings. "
                       "Use this for conceptual searches when exact keywords won't work. "
                       "Examples: 'Find content about project deadlines and pressure', 'Search for discussions about team morale'",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query (semantic/conceptual matching)"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why you need this information"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 50)",
                        "default": 50
                    },
                    "min_similarity": {
                        "type": "number",
                        "description": "Minimum similarity threshold 0-1 (default: 0.2)",
                        "default": 0.2
                    }
                },
                "required": ["query", "reasoning"]
            }
        ),
        Tool(
            name="query_source",
            description="Load pages directly from a specific database with time filtering. "
                       "Use this to expand beyond initial context or load different time ranges. "
                       "Examples: 'query_source(database=\"journal\", days=30)' for more journal history",
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Database name (e.g., 'journal', 'gmail', 'stories', 'tasks')"
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look back (null/0 for all entries)"
                    }
                },
                "required": ["database"]
            }
        ),
        Tool(
            name="get_agent_messaging_config",
            description="Get current messaging configuration for this agent. "
                       "Shows platform (slack/discord), channel ID, enabled status, and conversation settings.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="update_agent_messaging_config",
            description="Update agent's messaging configuration. "
                       "Enable/disable messaging tools or adjust conversation timeout. "
                       "Only provide the fields you want to change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "messaging_enabled": {
                        "type": "boolean",
                        "description": "Enable or disable messaging tools"
                    },
                    "conversation_timeout_minutes": {
                        "type": "integer",
                        "description": "Minutes before conversation times out (default 15)"
                    }
                }
            }
        ),
        Tool(
            name="list_available_messaging_channels",
            description="List available messaging channels for a platform (slack or discord). "
                       "Discovers all channels the bot has access to.",
            inputSchema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "Platform: 'slack' or 'discord'"
                    }
                },
                "required": ["platform"]
            }
        ),
        Tool(
            name="write_agent_journal",
            description="Write a note to your agent journal — your private notebook for tracking insights, "
                       "learnings, and information across runs. This is YOUR agent journal, not the user's "
                       "personal journal database. NOT for execution logs (those are automatic). "
                       "Only write when you have something meaningful to record.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Your journal note content"
                    },
                    "note_type": {
                        "type": "string",
                        "description": "Type of note: 'Note' (default), 'Insight', 'Learning', or 'Change'",
                        "enum": ["Note", "Insight", "Learning", "Change"]
                    }
                },
                "required": ["content"]
            }
        )
    ]
    return _filter_tools_by_permission(all_tools)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a query tool"""
    logger.info(f"Tool call: {name} with args: {arguments}")

    if ALLOWED_TOOLS is not None and name not in ALLOWED_TOOLS:
        msg = f"❌ Permission denied: tool '{name}' is not enabled for this agent"
        logger.warning(msg)
        return [TextContent(type="text", text=msg)]

    try:
        if name == "query_sql":
            return await _handle_query_sql(arguments)
        elif name == "query_vector":
            return await _handle_query_vector(arguments)
        elif name == "query_source":
            return await _handle_query_source(arguments)
        elif name == "get_agent_messaging_config":
            return await _handle_get_messaging_config(arguments)
        elif name == "update_agent_messaging_config":
            return await _handle_update_messaging_config(arguments)
        elif name == "list_available_messaging_channels":
            return await _handle_list_messaging_channels(arguments)
        elif name == "write_agent_journal":
            return await _handle_write_journal(arguments)
        else:
            error_msg = f"Unknown tool: {name}"
            logger.error(error_msg)
            return [TextContent(type="text", text=f"❌ {error_msg}")]
    except Exception as e:
        error_msg = f"Error executing {name}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return [TextContent(type="text", text=f"❌ {error_msg}")]


async def _handle_query_sql(args: dict) -> list[TextContent]:
    """Handle query_sql tool call"""
    query = args.get("query")
    reasoning = args.get("reasoning", "")

    if not query:
        return [TextContent(type="text", text="❌ Missing required parameter: query")]

    logger.info(f"Executing SQL query: {query}")
    logger.info(f"Reasoning: {reasoning}")

    # Check if agent has QUERY permission for any sources
    if AGENT_CONFIG:
        queryable = AGENT_CONFIG.get_queryable_sources()
        if not queryable:
            error_msg = "❌ Permission denied: No queryable sources configured for this agent"
            logger.warning(f"No queryable sources for agent {AGENT_CONFIG.name}")
            return [TextContent(type="text", text=error_msg)]

    try:
        # Process natural language query with metadata return
        loaded_content, metadata = process_natural_language_to_content(
            nl_prompt=query,
            workspace=WORKSPACE,
            verbose=False,
            skip_confirmation=True,  # No interactive prompts in MCP server
            return_metadata=True
        )

        # Post-filter by allowed channels
        if ALLOWED_CHANNEL_IDS:
            loaded_content = _filter_loaded_content_by_channel(loaded_content, ALLOWED_CHANNEL_IDS)

        # Format results
        formatted = format_context_data(loaded_content)
        total_pages = sum(len(pages) for pages in loaded_content.values())

        # Build response
        response_parts = [
            f"✅ SQL Query: {query}",
            f"Found {total_pages} pages across {len(loaded_content)} database(s)",
            "",
            formatted
        ]

        # Add generated SQL for transparency (if available)
        if metadata.get('generated_query'):
            response_parts.insert(2, f"Generated SQL: {metadata['generated_query']}")
            response_parts.insert(3, "")

        text = "\n".join(response_parts)
        logger.info(f"SQL query completed: {total_pages} pages found")

        return [TextContent(type="text", text=text)]

    except Exception as e:
        logger.error(f"SQL query failed: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Query failed: {str(e)}")]


async def _handle_query_vector(args: dict) -> list[TextContent]:
    """Handle query_vector tool call"""
    query = args.get("query")
    reasoning = args.get("reasoning", "")
    top_k = args.get("top_k", 50)
    min_similarity = args.get("min_similarity", 0.2)

    if not query:
        return [TextContent(type="text", text="❌ Missing required parameter: query")]

    logger.info(f"Executing vector search: {query}")
    logger.info(f"Parameters: top_k={top_k}, min_similarity={min_similarity}")

    # Check if agent has QUERY permission for any sources
    if AGENT_CONFIG:
        queryable = AGENT_CONFIG.get_queryable_sources()
        if not queryable:
            error_msg = "❌ Permission denied: No queryable sources configured for this agent"
            logger.warning(f"No queryable sources for agent {AGENT_CONFIG.name}")
            return [TextContent(type="text", text=error_msg)]

    try:
        # Process vector search
        loaded_content = process_vector_search_to_content(
            vs_prompt=query,
            workspace=WORKSPACE,
            n_results=top_k,
            min_similarity=min_similarity,
            verbose=False,
            skip_confirmation=True
        )

        # Post-filter by allowed channels
        if ALLOWED_CHANNEL_IDS:
            loaded_content = _filter_loaded_content_by_channel(loaded_content, ALLOWED_CHANNEL_IDS)

        # Format results
        formatted = format_context_data(loaded_content)
        total_pages = sum(len(pages) for pages in loaded_content.values())

        text = f"""✅ Vector Search: {query}
Found {total_pages} semantically similar pages

{formatted}"""

        logger.info(f"Vector search completed: {total_pages} pages found")

        return [TextContent(type="text", text=text)]

    except Exception as e:
        logger.error(f"Vector search failed: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Query failed: {str(e)}")]


async def _handle_query_source(args: dict) -> list[TextContent]:
    """Handle query_source tool call"""
    database = args.get("database")
    days = args.get("days", 7)

    if not database:
        return [TextContent(type="text", text="❌ Missing required parameter: database")]

    # Handle days=0 as "all"
    if days == 0:
        days = None

    logger.info(f"Loading source: {database} (days={days})")

    # Check permissions if agent config available
    if AGENT_CONFIG:
        # Check if agent can query this source
        queryable_sources = AGENT_CONFIG.get_queryable_sources()
        if database not in queryable_sources:
            error_msg = (
                f"❌ Permission denied: Cannot query '{database}'\n\n"
                f"Allowed sources: {', '.join(queryable_sources)}\n"
            )
            logger.warning(f"Permission denied: {database} for agent {AGENT_CONFIG.name}")
            return [TextContent(type="text", text=error_msg)]

        # Check max_query_days limit if specified
        if not AGENT_CONFIG.can_query_source(database, days or 999):
            # Find max_query_days for this source
            max_days = None
            if hasattr(AGENT_CONFIG, 'source_access') and AGENT_CONFIG.source_access:
                for access in AGENT_CONFIG.source_access:
                    if access.source_name == database and access.max_query_days:
                        max_days = access.max_query_days
                        break

            error_msg = f"❌ Permission denied: Cannot query '{database}' for {days} days\n"
            if max_days:
                error_msg += f"Maximum allowed: {max_days} days\n"

            logger.warning(f"Query days limit exceeded: {database}:{days} for agent {AGENT_CONFIG.name}")
            return [TextContent(type="text", text=error_msg)]

    try:
        # Get database config
        db_config = get_database_config(database, WORKSPACE)
        if not db_config:
            error_msg = f"Database '{database}' not found in workspace '{WORKSPACE}'"
            logger.error(error_msg)
            return [TextContent(type="text", text=f"❌ {error_msg}")]

        # Load pages
        pages = load_database_pages_with_filters(
            database_config=db_config,
            days=days
        )

        # Post-filter by allowed channels for Slack/Discord sources
        if ALLOWED_CHANNEL_IDS and db_config.source_type in ("slack", "discord"):
            pre_count = len(pages)
            pages = _filter_pages_by_channel(pages, ALLOWED_CHANNEL_IDS)
            if pre_count != len(pages):
                logger.info(f"Channel filter: {pre_count} → {len(pages)} pages")

        # Format results
        formatted = format_context_data({database: pages})

        time_range = f"last {days} days" if days else "all time"
        text = f"""✅ Loaded {len(pages)} pages from '{database}' ({time_range})

{formatted}"""

        logger.info(f"Source query completed: {len(pages)} pages loaded")

        return [TextContent(type="text", text=text)]

    except Exception as e:
        logger.error(f"Source query failed: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Query failed: {str(e)}")]


async def _handle_get_messaging_config(args: dict) -> list[TextContent]:
    """Handle get_agent_messaging_config tool call"""
    try:
        if not AGENT_CONFIG:
            return [TextContent(type="text", text="❌ No agent config loaded")]
        
        config_data = {
            "agent_id": AGENT_CONFIG.agent_id,
            "agent_name": AGENT_CONFIG.name,
            "messaging_enabled": AGENT_CONFIG.messaging_enabled,
            "conversation_timeout_minutes": AGENT_CONFIG.conversation_timeout_minutes,
        }

        result_text = f"Current Messaging Configuration:\n\n"
        result_text += f"Enabled: {config_data['messaging_enabled']}\n"
        result_text += f"Conversation timeout: {config_data['conversation_timeout_minutes']} minutes\n"
        result_text += f"\nPlatforms are environment-based (available if bot tokens are set).\n"
        
        logger.info("Retrieved messaging config")
        return [TextContent(type="text", text=result_text)]
    
    except Exception as e:
        logger.error(f"Error getting messaging config: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]


async def _handle_update_messaging_config(args: dict) -> list[TextContent]:
    """Handle update_agent_messaging_config tool call"""
    try:
        if not AGENT_CONFIG:
            return [TextContent(type="text", text="❌ No agent config loaded")]
        
        from promaia.agents import save_agents, load_agents
        
        # Reload agents to get latest state
        agents = load_agents()
        agent = next((a for a in agents if a.agent_id == AGENT_CONFIG.agent_id), None)
        
        if not agent:
            return [TextContent(type="text", text=f"❌ Agent {AGENT_CONFIG.agent_id} not found")]
        
        # Update fields if provided
        updated_fields = []

        if 'messaging_enabled' in args and args['messaging_enabled'] is not None:
            agent.messaging_enabled = args['messaging_enabled']
            updated_fields.append(f"enabled={args['messaging_enabled']}")

        if 'conversation_timeout_minutes' in args and args['conversation_timeout_minutes'] is not None:
            agent.conversation_timeout_minutes = args['conversation_timeout_minutes']
            updated_fields.append(f"timeout={args['conversation_timeout_minutes']} min")

        # Save configuration
        save_agents(agents)

        result_text = f"Configuration updated!\n\nUpdated fields: {', '.join(updated_fields)}\n\n"
        result_text += "New configuration:\n"
        result_text += f"- Enabled: {agent.messaging_enabled}\n"
        result_text += f"- Conversation timeout: {agent.conversation_timeout_minutes} min\n"
        
        logger.info(f"Updated messaging config: {updated_fields}")
        return [TextContent(type="text", text=result_text)]
    
    except Exception as e:
        logger.error(f"Error updating messaging config: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]


async def _handle_list_messaging_channels(args: dict) -> list[TextContent]:
    """Handle list_available_messaging_channels tool call"""
    try:
        platform = args.get("platform")
        
        if not platform:
            return [TextContent(type="text", text="❌ Missing required parameter: platform")]
        
        if platform == "slack":
            from promaia.connectors.slack_connector import SlackConnector
            import os
            
            bot_token = os.environ.get("SLACK_BOT_TOKEN")
            if not bot_token:
                return [TextContent(type="text", text="❌ SLACK_BOT_TOKEN not found in environment")]
            
            connector = SlackConnector({
                "workspace": WORKSPACE,
                "bot_token": bot_token
            })
            await connector.connect()
            channels_data = await connector.discover_accessible_channels()
            channels = channels_data.get("channels", [])
        
        elif platform == "discord":
            from promaia.connectors.discord_connector import DiscordConnector
            import os
            
            bot_token = os.environ.get("DISCORD_BOT_TOKEN")
            server_id = os.environ.get("DISCORD_SERVER_ID")
            
            if not bot_token or not server_id:
                return [TextContent(type="text", text="❌ DISCORD_BOT_TOKEN or DISCORD_SERVER_ID not found in environment")]
            
            connector = DiscordConnector({
                "workspace": WORKSPACE,
                "bot_token": bot_token,
                "database_id": server_id
            })
            await connector.connect()
            channels_data = await connector.discover_accessible_channels()
            channels = channels_data.get("channels", [])
        
        else:
            return [TextContent(type="text", text=f"❌ Unknown platform: {platform}. Use 'slack' or 'discord'.")]
        
        # Format channel list
        result_text = f"✅ Found {len(channels)} accessible channels on {platform}:\n\n"
        
        for channel in channels[:20]:  # Show first 20
            name = channel.get('name', 'unknown')
            channel_id = channel.get('id', 'unknown')
            is_private = channel.get('is_private', False)
            privacy_marker = "🔒" if is_private else "#"
            result_text += f"- {privacy_marker}{name} (ID: {channel_id})\n"
        
        if len(channels) > 20:
            result_text += f"\n... and {len(channels) - 20} more channels"
        
        logger.info(f"Listed {len(channels)} channels for {platform}")
        return [TextContent(type="text", text=result_text)]
    
    except Exception as e:
        logger.error(f"Error listing channels: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]


async def _handle_write_journal(args: dict) -> list[TextContent]:
    """Handle write_agent_journal tool call - Agent writes to its own journal"""
    try:
        content = args.get("content", "")
        note_type = args.get("note_type", "Note")
        
        if not content:
            return [TextContent(type="text", text="❌ Error: 'content' is required")]
        
        # Get agent config to find journal database
        if not AGENT_CONFIG or not hasattr(AGENT_CONFIG, 'agent_id'):
            return [TextContent(type="text", text="❌ Error: Agent not configured for journal writing")]
        
        # Write to journal using the journal writer
        from promaia.agents.notion_journal import write_journal_entry
        
        await write_journal_entry(
            agent_id=AGENT_CONFIG.agent_id,
            workspace=WORKSPACE,
            entry_type=note_type,
            content=content,
            execution_id=None  # No execution ID for agent-initiated notes
        )
        
        logger.info(f"✍️ Agent '{AGENT_CONFIG.agent_id}' wrote {note_type} to journal ({len(content)} chars)")
        return [TextContent(type="text", text=f"✅ Wrote {note_type} to journal successfully")]
    
    except Exception as e:
        logger.error(f"Error writing to journal: {e}", exc_info=True)
        return [TextContent(type="text", text=f"❌ Error writing to journal: {str(e)}")]


async def main():
    """Run the MCP server"""
    global WORKSPACE, AGENT_CONFIG, ALLOWED_CHANNEL_IDS, ALLOWED_TOOLS

    parser = argparse.ArgumentParser(description="Promaia Query Tools MCP Server")
    parser.add_argument("--workspace", required=True, help="Workspace name (e.g., 'koii')")
    parser.add_argument("--agent-id", required=False, help="Agent ID for permission enforcement")
    parser.add_argument("--allowed-channels", required=False,
                        help="JSON list of channel IDs this agent may access (omit for all)")
    parser.add_argument("--allowed-tools", required=False,
                        help="JSON list of tool names this agent may call (omit for all)")
    args = parser.parse_args()

    WORKSPACE = args.workspace
    logger.info(f"Starting Promaia MCP server for workspace: {WORKSPACE}")

    # Parse channel restrictions
    if args.allowed_channels:
        try:
            ALLOWED_CHANNEL_IDS = json.loads(args.allowed_channels)
            logger.info(f"Channel restrictions active: {ALLOWED_CHANNEL_IDS}")
        except Exception as e:
            logger.warning(f"Could not parse --allowed-channels: {e}")

    # Parse tool-level permissions
    if args.allowed_tools:
        try:
            tool_list = json.loads(args.allowed_tools)
            ALLOWED_TOOLS = set(tool_list)
            logger.info(f"Tool restrictions active: {sorted(ALLOWED_TOOLS)}")
        except Exception as e:
            logger.warning(f"Could not parse --allowed-tools: {e}")

    # Load agent config if provided (for permission enforcement)
    if args.agent_id:
        try:
            from promaia.agents.agent_config import get_agent
            AGENT_CONFIG = get_agent(args.agent_id)
            if AGENT_CONFIG:
                logger.info(f"Loaded agent config for: {args.agent_id}")
                logger.info(f"Queryable sources: {AGENT_CONFIG.get_queryable_sources()}")
            else:
                logger.warning(f"Agent '{args.agent_id}' not found, running without permission enforcement")
        except Exception as e:
            logger.warning(f"Could not load agent config: {e}, running without permission enforcement")

    logger.info("Tools available: query_sql, query_vector, query_source, get_agent_messaging_config, update_agent_messaging_config, list_available_messaging_channels, write_agent_journal")

    # Run stdio server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)
