"""
Simplified Claude Agent SDK adapter for Promaia.

This version focuses on getting the basic Agent SDK working with external MCPs (Gmail, Notion).
Custom Promaia tools will be added as a separate MCP server later.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

logger = logging.getLogger(__name__)


def load_mcp_servers_from_claude_config(project_dir: str = None) -> Dict[str, Dict[str, Any]]:
    """
    Load MCP server configurations from .claude.json.

    Args:
        project_dir: Project directory containing .claude.json (default: current)

    Returns:
        Dict of MCP server configurations ready for Agent SDK
    """
    if project_dir is None:
        project_dir = os.getcwd()

    # Try .claude.json in home directory
    claude_config_path = Path.home() / '.claude.json'

    if not claude_config_path.exists():
        logger.warning(f"No .claude.json found at {claude_config_path}")
        return {}

    try:
        with open(claude_config_path) as f:
            config = json.load(f)

        # Get MCP servers for the project
        project_config = config.get('projects', {}).get(project_dir, {})
        mcp_servers_config = project_config.get('mcpServers', {})

        # MCP servers are already in the right format in .claude.json
        # Just need to substitute environment variables
        mcp_servers = {}
        for name, server_config in mcp_servers_config.items():
            if server_config.get('type') == 'stdio':
                # Substitute environment variables in env
                env = {}
                for key, value in server_config.get('env', {}).items():
                    # Handle ${VAR} syntax and direct values
                    if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                        env_var = value[2:-1]
                        env[key] = os.getenv(env_var, '')
                    else:
                        env[key] = value

                mcp_servers[name] = {
                    'command': server_config['command'],
                    'args': server_config.get('args', []),
                    'env': env
                }

        logger.info(f"Loaded {len(mcp_servers)} MCP servers from .claude.json")
        return mcp_servers

    except Exception as e:
        logger.error(f"Error loading MCP servers from .claude.json: {e}")
        return {}


class PromaiaAgentClient:
    """
    Simplified Claude Agent SDK client for Promaia.

    This version uses the Agent SDK with external MCPs only.
    Custom Promaia tools are accessed via direct function calls for now.
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
                multi_source_data={},
                workspace=self.workspace,
                include_query_tools=True
            )

        # Load MCP servers from .claude.json (Gmail, Notion, etc.)
        mcp_servers = load_mcp_servers_from_claude_config()

        # Configure SDK options
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=system_prompt,
            mcp_servers=mcp_servers if mcp_servers else {}
        )

        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        logger.info(f"Initialized PromaiaAgentClient with {len(mcp_servers)} MCP servers")

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

        # Send the query
        await self.client.query(user_message)

        # Collect the full response
        full_response = ""

        try:
            async for message in self.client.receive_messages():
                # Handle different message types
                if hasattr(message, 'role'):
                    if message.role == "assistant":
                        if hasattr(message, 'content'):
                            if isinstance(message.content, list):
                                for block in message.content:
                                    if hasattr(block, 'text'):
                                        full_response += block.text
                            else:
                                full_response += str(message.content)
                    elif message.role == "user":
                        # User message echo, skip
                        pass

            return full_response

        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            raise

    def reset_conversation(self):
        """Clear conversation history for a new chat."""
        self.conversation_history = []
