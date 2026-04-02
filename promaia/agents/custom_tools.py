"""
Custom tools for Claude Agent SDK.

These wrap Promaia-specific query capabilities (SQL, vector, source)
so the SDK can use them alongside built-in tools.

IMPORTANT: These tools return results as tool responses (standard SDK flow).
They do NOT update the system prompt. Context expansion happens naturally
through the conversation as the agent calls these tools.
"""
import logging
from typing import Dict, Any, Optional
from claude_agent_sdk import tool

logger = logging.getLogger(__name__)


def create_query_source_tool(agent_config, workspace: str):
    """
    Create a query_source tool with access to agent config and workspace.

    Returns a tool decorated function that can be passed to create_sdk_mcp_server().
    """

    @tool(
        name="query_source",
        description="""Load pages from a Promaia database with time range filtering.

Use this to expand beyond the initial context boundary. For example, if initial
context includes journal:7 (last 7 days), you can query journal:30 to get more history.

Examples:
- query_source(database="journal", days=30) - Load last 30 days of journal
- query_source(database="gmail", days=14) - Load 2 weeks of emails
- query_source(database="tasks", days=None) - Load all tasks

The result is formatted context data you can analyze.""",
        input_schema={
            "database": str,
            "days": Optional[int],
        }
    )
    async def query_source_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute query_source with permission checking"""
        from promaia.storage.files import load_database_pages_with_filters
        from promaia.ai.prompts import format_context_data

        database = args.get("database")
        days = args.get("days", 7)

        # Check permissions
        if not agent_config.can_query_source(database, days or 999):
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Permission denied: Cannot query '{database}' for {days} days\n\nAllowed sources: {', '.join(agent_config.get_queryable_sources())}"
                }],
                "isError": True
            }

        try:
            # Get database config
            from promaia.config.databases import get_database_config

            database_config = get_database_config(database, workspace)
            if not database_config:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Database '{database}' not found in workspace '{workspace}'"
                    }],
                    "isError": True
                }

            # Load pages
            pages = load_database_pages_with_filters(
                database_config=database_config,
                days=days
            )

            # Format as context (same format as initial context)
            formatted = format_context_data({database: pages})

            result_text = f"✅ Loaded {len(pages)} pages from '{database}'"
            if days:
                result_text += f" (last {days} days)"
            result_text += f"\n\n{formatted}"

            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }]
            }
        except Exception as e:
            logger.error(f"Error executing query_source: {e}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error loading from '{database}': {str(e)}"
                }],
                "isError": True
            }

    return query_source_impl


def create_query_sql_tool(agent_config, workspace: str):
    """
    Create a query_sql tool with access to agent config and workspace.

    Returns a tool decorated function that can be passed to create_sdk_mcp_server().
    """

    @tool(
        name="query_sql",
        description="""Execute SQL queries using natural language.

This searches for EXACT TEXT/KEYWORDS in content (not abstract concepts).
Use for specific data retrieval when you know what you're looking for.

Examples:
- "Find emails from Federico about the project"
- "Show tasks assigned to Alice due this week"
- "Get journal entries mentioning 'budget meeting'"

Returns matching pages from Promaia databases.""",
        input_schema={
            "query": str,
            "reasoning": str,
        }
    )
    async def query_sql_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute SQL query"""
        from promaia.chat.query_tools import QueryToolExecutor

        query = args.get("query")
        reasoning = args.get("reasoning", "")

        try:
            # Use existing QueryToolExecutor logic
            context_state = {
                'ai_queries': [],
                'context_data': {},
                'workspace': workspace
            }
            executor = QueryToolExecutor(context_state)

            tool_call = {
                'tool_name': 'query_sql',
                'parameters': {
                    'query': query,
                    'reasoning': reasoning,
                    'workspace': workspace
                }
            }

            result = await executor._execute_query_only(tool_call)

            if result.get('success'):
                # Format the loaded content for response
                from promaia.ai.prompts import format_context_data
                loaded_content = result.get('loaded_content', {})
                formatted = format_context_data(loaded_content)

                result_text = f"✅ SQL Query: {query}\n"
                result_text += f"Found {result.get('total_pages', 0)} pages"
                if result.get('databases'):
                    result_text += f" in: {', '.join(result.get('databases', []))}"
                if result.get('generated_sql'):
                    result_text += f"\n\n*Generated SQL:* `{result.get('generated_sql')}`"
                result_text += f"\n\n{formatted}"

                return {
                    "content": [{
                        "type": "text",
                        "text": result_text
                    }]
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ SQL query failed: {result.get('error', 'Unknown error')}"
                    }],
                    "isError": True
                }

        except Exception as e:
            logger.error(f"Error executing query_sql: {e}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error executing SQL query: {str(e)}"
                }],
                "isError": True
            }

    return query_sql_impl


def create_query_vector_tool(agent_config, workspace: str):
    """
    Create a query_vector tool with access to agent config and workspace.

    Returns a tool decorated function that can be passed to create_sdk_mcp_server().
    """

    @tool(
        name="query_vector",
        description="""Semantic search using embeddings (abstract concept matching).

Use this for conceptual searches when exact keywords won't work.
Can filter by database properties.

Examples:
- "Find content about project deadlines and pressure"
- "Search for discussions about team morale"
- "Look for technical architecture decisions"

Parameters:
- query: Natural language search query
- top_k: Max results (default 50)
- min_similarity: Threshold 0-1 (default 0.2)
- filters: Dict of property filters (optional)

Returns semantically similar pages from Promaia databases.""",
        input_schema={
            "query": str,
            "reasoning": str,
            "top_k": int,
            "min_similarity": float,
        }
    )
    async def query_vector_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute vector search"""
        from promaia.chat.query_tools import QueryToolExecutor

        query = args.get("query")
        reasoning = args.get("reasoning", "")
        top_k = args.get("top_k", 50)
        min_similarity = args.get("min_similarity", 0.2)

        try:
            # Use existing QueryToolExecutor logic
            context_state = {
                'ai_queries': [],
                'context_data': {},
                'workspace': workspace
            }
            executor = QueryToolExecutor(context_state)

            tool_call = {
                'tool_name': 'query_vector',
                'parameters': {
                    'query': query,
                    'top_k': top_k,
                    'min_similarity': min_similarity,
                    'filters': {},
                    'reasoning': reasoning,
                    'workspace': workspace
                }
            }

            result = await executor._execute_query_only(tool_call)

            if result.get('success'):
                # Format the loaded content for response
                from promaia.ai.prompts import format_context_data
                loaded_content = result.get('loaded_content', {})
                formatted = format_context_data(loaded_content)

                result_text = f"✅ Vector Search: {query}\n"
                result_text += f"Found {result.get('total_pages', 0)} pages"
                if result.get('databases'):
                    result_text += f" in: {', '.join(result.get('databases', []))}"
                result_text += f"\n*Parameters:* top_k={top_k}, min_similarity={min_similarity}"
                result_text += f"\n\n{formatted}"

                return {
                    "content": [{
                        "type": "text",
                        "text": result_text
                    }]
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Vector search failed: {result.get('error', 'Unknown error')}"
                    }],
                    "isError": True
                }

        except Exception as e:
            logger.error(f"Error executing query_vector: {e}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error executing vector search: {str(e)}"
                }],
                "isError": True
            }

    return query_vector_impl


def create_conversation_end_tool(conversation_manager, conversation_id: str, is_dm: bool = False):
    """
    Create a tool that allows the AI to signal conversation end.

    The AI calls this when it detects the user wants to end the conversation.
    This is more reliable than regex-based pattern matching for understanding user intent.

    Args:
        conversation_manager: Reference to the ConversationManager instance
        conversation_id: The conversation ID to end
        is_dm: Whether this is a DM conversation (triggers summary + KB save)

    Returns:
        Tool function for ending conversations
    """
    @tool(
        name="end_conversation",
        description="""End the current conversation gracefully.

Call this tool when the user clearly indicates they want to end the conversation:
- Says goodbye (bye, see you, talk later, etc.)
- Indicates they need to leave (gotta go, have to run, etc.)
- Thanks you and indicates completion (thanks, that's all, we're done, etc.)
- Natural end of conversation reached

Only call this when you are confident the user intends to end the conversation.
You MUST provide a summary of what was discussed — this is saved for future reference.""",
        input_schema={
            "reason": str,
            "summary": str,
        }
    )
    async def end_conversation_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """End the current conversation gracefully."""
        reason = args.get('reason', 'ai_detected_goodbye')
        summary = args.get('summary', '')

        try:
            if is_dm and summary:
                # DM conversations: mark done with summary and save to KB
                await conversation_manager.mark_conversation_done(
                    conversation_id,
                    summary=summary,
                    reason=reason
                )
            else:
                # Channel threads: end normally
                await conversation_manager.end_conversation(
                    conversation_id,
                    reason=reason
                )

            logger.info(f"🏁 AI called end_conversation tool for {conversation_id}: {reason}")

            return {
                "content": [{
                    "type": "text",
                    "text": f"✅ Conversation ended successfully. Reason: {reason}"
                }]
            }

        except Exception as e:
            logger.error(f"Error ending conversation: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error ending conversation: {str(e)}"
                }],
                "isError": True
            }

    return end_conversation_impl


def create_messaging_config_tools(agent_config, workspace: str):
    """
    Create tools for agents to manage their own messaging configuration.

    Returns a tuple of (get_config, update_config, list_channels) tools.
    """
    
    @tool(
        name="get_agent_messaging_config",
        description="""Get current messaging configuration for this agent.

Shows: messaging platform (slack/discord), channel ID, enabled status,
conversation settings (timeout, max turns), and initiation mode.

Use this to check your current messaging setup before making changes.""",
        input_schema={}
    )
    async def get_messaging_config_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get current messaging configuration"""
        try:
            config_data = {
                "agent_id": agent_config.agent_id,
                "agent_name": agent_config.name,
                "messaging_platform": agent_config.messaging_platform,
                "messaging_channel_id": agent_config.messaging_channel_id,
                "messaging_enabled": agent_config.messaging_enabled,
                "initiate_conversation": agent_config.initiate_conversation,
                "conversation_timeout_minutes": agent_config.conversation_timeout_minutes,
                "conversation_max_turns": agent_config.conversation_max_turns
            }
            
            result_text = f"✅ Current Messaging Configuration:\n\n"
            result_text += f"Platform: {config_data['messaging_platform'] or 'Not set'}\n"
            result_text += f"Channel ID: {config_data['messaging_channel_id'] or 'Not set'}\n"
            result_text += f"Enabled: {config_data['messaging_enabled']}\n"
            result_text += f"Mode: {'Interactive conversation' if config_data['initiate_conversation'] else 'One-way post'}\n"
            result_text += f"Timeout: {config_data['conversation_timeout_minutes']} minutes\n"
            result_text += f"Max turns: {config_data['conversation_max_turns'] or 'Unlimited'}\n"
            
            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }]
            }
        
        except Exception as e:
            logger.error(f"Error getting messaging config: {e}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error getting messaging config: {str(e)}"
                }],
                "isError": True
            }
    
    @tool(
        name="update_agent_messaging_config",
        description="""Update agent's messaging configuration.

Allows you to configure or change:
- messaging_platform: "slack" or "discord"
- messaging_channel_id: Platform-specific channel ID (e.g., "C06ABC123" for Slack)
- messaging_enabled: true/false to enable/disable messaging
- initiate_conversation: true for interactive conversations, false for one-way posts
- conversation_timeout_minutes: How long to wait before timing out (default 15)

Example: update_agent_messaging_config(messaging_platform="slack", messaging_channel_id="C06ABC123", messaging_enabled=True)

Only provide the fields you want to change - others remain unchanged.""",
        input_schema={
            "messaging_platform": Optional[str],
            "messaging_channel_id": Optional[str],
            "messaging_enabled": Optional[bool],
            "initiate_conversation": Optional[bool],
            "conversation_timeout_minutes": Optional[int],
        }
    )
    async def update_messaging_config_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """Update agent messaging configuration"""
        try:
            from promaia.agents import save_agents, load_agents
            
            # Reload agents to get latest state
            agents = load_agents()
            agent = next((a for a in agents if a.agent_id == agent_config.agent_id), None)
            
            if not agent:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Agent {agent_config.agent_id} not found"
                    }],
                    "isError": True
                }
            
            # Update fields if provided
            updated_fields = []
            
            if 'messaging_platform' in args and args['messaging_platform'] is not None:
                agent.messaging_platform = args['messaging_platform']
                updated_fields.append(f"platform={args['messaging_platform']}")
            
            if 'messaging_channel_id' in args and args['messaging_channel_id'] is not None:
                agent.messaging_channel_id = args['messaging_channel_id']
                updated_fields.append(f"channel_id={args['messaging_channel_id']}")
            
            if 'messaging_enabled' in args and args['messaging_enabled'] is not None:
                agent.messaging_enabled = args['messaging_enabled']
                updated_fields.append(f"enabled={args['messaging_enabled']}")
            
            if 'initiate_conversation' in args and args['initiate_conversation'] is not None:
                agent.initiate_conversation = args['initiate_conversation']
                updated_fields.append(f"initiate_conversation={args['initiate_conversation']}")
            
            if 'conversation_timeout_minutes' in args and args['conversation_timeout_minutes'] is not None:
                agent.conversation_timeout_minutes = args['conversation_timeout_minutes']
                updated_fields.append(f"timeout={args['conversation_timeout_minutes']} min")
            
            # Save configuration
            save_agents(agents)
            
            result_text = f"✅ Configuration updated!\n\nUpdated fields: {', '.join(updated_fields)}\n\n"
            result_text += "New configuration:\n"
            result_text += f"- Platform: {agent.messaging_platform}\n"
            result_text += f"- Channel: {agent.messaging_channel_id}\n"
            result_text += f"- Enabled: {agent.messaging_enabled}\n"
            result_text += f"- Mode: {'Interactive' if agent.initiate_conversation else 'One-way'}\n"
            
            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }]
            }
        
        except Exception as e:
            logger.error(f"Error updating messaging config: {e}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error updating config: {str(e)}"
                }],
                "isError": True
            }
    
    @tool(
        name="list_available_messaging_channels",
        description="""List available messaging channels for a platform.

Discovers all channels the bot has access to on the specified platform.

Args:
- platform: "slack" or "discord"

Returns list of channels with IDs and names that you can configure for messaging.

Example: list_available_messaging_channels(platform="slack")""",
        input_schema={
            "platform": str,
        }
    )
    async def list_channels_impl(args: Dict[str, Any]) -> Dict[str, Any]:
        """List available messaging channels"""
        try:
            platform = args.get("platform")
            
            if platform == "slack":
                from promaia.connectors.slack_connector import SlackConnector
                import os
                
                bot_token = os.environ.get("SLACK_BOT_TOKEN")
                if not bot_token:
                    return {
                        "content": [{
                            "type": "text",
                            "text": "❌ SLACK_BOT_TOKEN not found in environment"
                        }],
                        "isError": True
                    }
                
                connector = SlackConnector({
                    "workspace": workspace,
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
                    return {
                        "content": [{
                            "type": "text",
                            "text": "❌ DISCORD_BOT_TOKEN or DISCORD_SERVER_ID not found in environment"
                        }],
                        "isError": True
                    }
                
                connector = DiscordConnector({
                    "workspace": workspace,
                    "bot_token": bot_token,
                    "database_id": server_id
                })
                await connector.connect()
                channels_data = await connector.discover_accessible_channels()
                channels = channels_data.get("channels", [])
            
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Unknown platform: {platform}. Use 'slack' or 'discord'."
                    }],
                    "isError": True
                }
            
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
            
            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }]
            }
        
        except Exception as e:
            logger.error(f"Error listing channels: {e}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error listing channels: {str(e)}"
                }],
                "isError": True
            }
    
    return (get_messaging_config_impl, update_messaging_config_impl, list_channels_impl)
