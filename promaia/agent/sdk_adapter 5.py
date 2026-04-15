"""
Claude Agent SDK adapter for Promaia.

This module integrates the Claude Agent SDK into Promaia's chat interface,
replacing direct Anthropic API calls with the agentic SDK while preserving
all of Promaia's query tools and functionality.
"""

import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional, AsyncIterator
from pathlib import Path

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions
)

logger = logging.getLogger(__name__)


# ============================================================================
# Promaia Query Tools as Callback Functions
# ============================================================================

# Note: The Claude Agent SDK doesn't support in-process custom tools the same way
# as MCP. Instead, we'll create an external MCP server for Promaia tools separately.
# For now, tools will be provided via system prompt instructions.

async def query_sql_callback(
    query: str,
    workspace: Optional[str] = None,
    max_results: Optional[int] = None
) -> Dict[str, Any]:
    """
    Execute a natural language SQL query against Promaia databases.

    Converts natural language to SQL and queries across unified_content,
    gmail_content, and other specialized tables. Uses learned patterns
    and schema discovery to generate accurate queries.

    Args:
        query: Natural language query (e.g., "emails from Federico about launch")
        workspace: Workspace to search in (default: user's default workspace)
        max_results: Maximum number of results to return

    Returns:
        Dictionary with:
        - success: Whether the query succeeded
        - loaded_content: Dict mapping database names to lists of pages
        - total_pages: Total number of pages loaded
        - databases: List of database names queried
        - query: The original query
    """
    from promaia.ai.nl_processor_wrapper import process_natural_language_to_content

    try:
        logger.info(f"Executing query_sql: {query} (workspace: {workspace})")

        loaded_content = process_natural_language_to_content(
            nl_prompt=query,
            workspace=workspace,
            verbose=False,
            skip_confirmation=True
        )

        total_pages = sum(len(pages) for pages in loaded_content.values())

        return {
            "success": True,
            "loaded_content": loaded_content,
            "total_pages": total_pages,
            "databases": list(loaded_content.keys()),
            "query": query,
            "workspace": workspace
        }
    except Exception as e:
        logger.error(f"Error in query_sql: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "query": query
        }


@tool
async def query_vector(
    query: str,
    workspace: Optional[str] = None,
    top_k: int = 60,
    min_similarity: float = 0.2
) -> Dict[str, Any]:
    """
    Execute a vector semantic search query against Promaia databases.

    Uses embeddings to find semantically similar content across all databases.
    Best for finding conceptually related content even when exact keywords differ.

    Args:
        query: Search query for semantic matching (e.g., "project management issues")
        workspace: Workspace to search in (default: user's default workspace)
        top_k: Number of results to return (default: 60, max: 200)
        min_similarity: Minimum similarity threshold 0-1 (default: 0.2)

    Returns:
        Dictionary with:
        - success: Whether the query succeeded
        - loaded_content: Dict mapping database names to lists of pages
        - total_pages: Total number of pages loaded
        - databases: List of database names searched
        - query: The original query
    """
    from promaia.ai.nl_processor_wrapper import process_vector_search_to_content

    try:
        logger.info(f"Executing query_vector: {query} (top_k: {top_k}, workspace: {workspace})")

        loaded_content = process_vector_search_to_content(
            vs_prompt=query,
            workspace=workspace,
            n_results=top_k,
            min_similarity=min_similarity,
            verbose=False
        )

        total_pages = sum(len(pages) for pages in loaded_content.values())

        return {
            "success": True,
            "loaded_content": loaded_content,
            "total_pages": total_pages,
            "databases": list(loaded_content.keys()),
            "query": query,
            "workspace": workspace,
            "top_k": top_k,
            "min_similarity": min_similarity
        }
    except Exception as e:
        logger.error(f"Error in query_vector: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "query": query
        }


@tool
async def query_source(
    source: str,
    workspace: Optional[str] = None,
    filters: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute a direct source query against a specific Promaia database.

    Loads content from a specific database with optional property filters.
    Faster than SQL/vector search when you know exactly which database to query.

    Args:
        source: Database source in format "database:days" or "database:all"
                Examples: "stories:7", "gmail:30", "journal:all"
        workspace: Workspace containing the database
        filters: Optional JSON string of property filters
                 Example: '{"status": "Done", "priority": "P1"}'

    Returns:
        Dictionary with:
        - success: Whether the query succeeded
        - loaded_content: Dict mapping database name to list of pages
        - total_pages: Number of pages loaded
        - source: The original source specification
    """
    from promaia.storage.files import load_database_pages_with_filters
    from promaia.config.databases import get_database_manager

    try:
        logger.info(f"Executing query_source: {source} (workspace: {workspace})")

        # Parse source format
        if ':' in source:
            database_name, days_str = source.split(':', 1)
            days = None if days_str == 'all' else int(days_str)
        else:
            database_name = source
            days = None

        # Get database configuration
        db_manager = get_database_manager()
        database_config = db_manager.get_database(database_name, workspace)

        if not database_config:
            return {
                "success": False,
                "error": f"Database '{database_name}' not found in workspace '{workspace}'",
                "source": source
            }

        # Parse filters if provided
        property_filters = None
        if filters:
            try:
                property_filters = json.loads(filters)
            except json.JSONDecodeError as e:
                return {
                    "success": False,
                    "error": f"Invalid JSON in filters: {e}",
                    "source": source
                }

        # Load pages
        pages = load_database_pages_with_filters(
            database_config=database_config,
            days=days,
            property_filters=property_filters
        )

        qualified_name = f"{database_config.workspace}.{database_name}"
        loaded_content = {qualified_name: pages}

        return {
            "success": True,
            "loaded_content": loaded_content,
            "total_pages": len(pages),
            "databases": [qualified_name],
            "source": source,
            "workspace": workspace,
            "filters": property_filters
        }
    except Exception as e:
        logger.error(f"Error in query_source: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "source": source
        }


def create_promaia_tools_mcp_server():
    """
    Create an in-process MCP server with all Promaia custom tools.

    Returns:
        SDK MCP server configuration with query_sql, query_vector, query_source
    """
    return create_sdk_mcp_server(
        name="promaia-tools",
        tools=[query_sql, query_vector, query_source]
    )


# ============================================================================
# MCP Configuration Loading
# ============================================================================

def load_mcp_servers_from_claude_config(project_dir: str = None) -> List[Dict[str, Any]]:
    """
    Load MCP server configurations from .claude.json.

    Args:
        project_dir: Project directory containing .claude.json (default: current)

    Returns:
        List of MCP server configurations ready for Agent SDK
    """
    if project_dir is None:
        project_dir = os.getcwd()

    # Try .claude.json in home directory
    claude_config_path = Path.home() / '.claude.json'

    if not claude_config_path.exists():
        logger.warning(f"No .claude.json found at {claude_config_path}")
        return []

    try:
        with open(claude_config_path) as f:
            config = json.load(f)

        # Get MCP servers for the project
        project_config = config.get('projects', {}).get(project_dir, {})
        mcp_servers_config = project_config.get('mcpServers', {})

        mcp_servers = []
        for name, server_config in mcp_servers_config.items():
            if server_config.get('type') == 'stdio':
                # Substitute environment variables in env
                env = {}
                for key, value in server_config.get('env', {}).items():
                    # Handle ${VAR} syntax
                    if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                        env_var = value[2:-1]
                        env[key] = os.getenv(env_var, '')
                    else:
                        env[key] = value

                mcp_servers.append({
                    'command': server_config['command'],
                    'args': server_config.get('args', []),
                    'env': env
                })

        logger.info(f"Loaded {len(mcp_servers)} MCP servers from .claude.json")
        return mcp_servers

    except Exception as e:
        logger.error(f"Error loading MCP servers from .claude.json: {e}")
        return []


# ============================================================================
# Promaia Agent Client
# ============================================================================

class PromaiaAgentClient:
    """
    Claude Agent SDK client configured for Promaia.

    This class wraps the Claude Agent SDK and provides a Promaia-specific
    interface that's compatible with the existing chat interface code.
    """

    def __init__(
        self,
        workspace: str = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
        temperature: float = 0.7
    ):
        """
        Initialize the Promaia Agent client.

        Args:
            workspace: Default workspace for queries
            model: Claude model to use
            max_tokens: Maximum tokens per response
            temperature: Sampling temperature (0-1)
        """
        self.workspace = workspace
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client: Optional[ClaudeSDKClient] = None
        self.conversation_history: List[Dict[str, str]] = []
        self.context_state: Dict[str, Any] = {
            'workspace': workspace,
            'loaded_content': {},
            'ai_queries': [],
            'total_pages_loaded': 0
        }

    async def initialize(self, system_prompt: str = None):
        """
        Initialize the Claude Agent SDK client.

        Args:
            system_prompt: Custom system prompt (uses Promaia's default if not provided)
        """
        # Generate system prompt if not provided
        if system_prompt is None:
            from promaia.ai.prompts import create_system_prompt
            system_prompt = create_system_prompt(
                workspace=self.workspace,
                multi_source_data=self.context_state.get('loaded_content', {}),
                context_type="chat"
            )

        # Load MCP servers from .claude.json
        external_mcp_servers = load_mcp_servers_from_claude_config()

        # Add Promaia tools MCP server
        promaia_tools_server = create_promaia_tools_mcp_server()
        all_mcp_servers = [promaia_tools_server] + external_mcp_servers

        # Configure SDK options
        options = ClaudeAgentOptions(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            mcp_servers=all_mcp_servers
        )

        self.client = ClaudeSDKClient(options=options)
        logger.info(f"Initialized PromaiaAgentClient with {len(all_mcp_servers)} MCP servers")

    async def send_message(self, user_message: str) -> str:
        """
        Send a message and get the complete response.

        Args:
            user_message: The user's message

        Returns:
            Complete assistant response as string
        """
        if not self.client:
            await self.initialize()

        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Collect the full response
        full_response = ""

        async for message in self.client.send_messages(self.conversation_history):
            if message.type == "content":
                full_response += message.content
            elif message.type == "tool_use":
                logger.info(f"Tool called: {message.tool_name}")
            elif message.type == "tool_result":
                # Update context state with tool results
                self._update_context_from_tool_result(message)

        # Add assistant response to history
        self.conversation_history.append({
            "role": "assistant",
            "content": full_response
        })

        return full_response

    async def send_message_streaming(self, user_message: str) -> AsyncIterator:
        """
        Send a message and stream the response.

        Args:
            user_message: The user's message

        Yields:
            Message objects as they arrive from Claude
        """
        if not self.client:
            await self.initialize()

        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        full_response = ""

        async for message in self.client.send_messages(self.conversation_history):
            yield message

            if message.type == "content":
                full_response += message.content
            elif message.type == "tool_result":
                self._update_context_from_tool_result(message)

        # Store complete response
        self.conversation_history.append({
            "role": "assistant",
            "content": full_response
        })

    def _update_context_from_tool_result(self, message):
        """Update context state when query tools return results."""
        if message.tool_name in ["query_sql", "query_vector", "query_source"]:
            try:
                result = json.loads(message.result) if isinstance(message.result, str) else message.result

                if result.get("success"):
                    # Merge loaded content into context
                    loaded_content = result.get("loaded_content", {})
                    self.context_state['loaded_content'].update(loaded_content)

                    # Update total pages
                    pages_loaded = result.get("total_pages", 0)
                    self.context_state['total_pages_loaded'] += pages_loaded

                    # Track AI queries
                    self.context_state['ai_queries'].append({
                        'tool': message.tool_name,
                        'query': result.get('query', ''),
                        'databases': result.get('databases', []),
                        'pages_loaded': pages_loaded
                    })

                    logger.info(f"Loaded {pages_loaded} pages via {message.tool_name}")
            except Exception as e:
                logger.error(f"Error updating context from tool result: {e}")

    def reset_conversation(self):
        """Clear conversation history for a new chat."""
        self.conversation_history = []
        self.context_state['ai_queries'] = []
        self.context_state['loaded_content'] = {}
        self.context_state['total_pages_loaded'] = 0

    def get_context_state(self) -> Dict[str, Any]:
        """Get the current context state."""
        return self.context_state.copy()
