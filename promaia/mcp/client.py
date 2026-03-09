"""
MCP (Model Context Protocol) client implementation.

This module provides a client for connecting to MCP servers and retrieving
their capabilities, tools, and resources for integration into Promaia chat sessions.
"""
import asyncio
import json
import subprocess
import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from ..config.mcp_servers import McpServerConfig
from .protocol import McpProtocolClient

logger = logging.getLogger(__name__)

@dataclass
class McpTool:
    """Represents an MCP tool."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str

@dataclass
class McpResource:
    """Represents an MCP resource."""
    uri: str
    name: str
    description: Optional[str]
    mime_type: Optional[str]
    server_name: str

@dataclass
class McpCapabilities:
    """Represents MCP server capabilities."""
    tools: List[McpTool]
    resources: List[McpResource]
    server_info: Dict[str, Any]

class McpClient:
    """Client for communicating with MCP servers."""
    
    def __init__(self):
        """Initialize the MCP client."""
        self.connected_servers: Dict[str, McpProtocolClient] = {}
        self.server_capabilities: Dict[str, McpCapabilities] = {}
    
    async def connect_to_server(self, config: McpServerConfig) -> bool:
        """Connect to an MCP server and retrieve its capabilities.
        
        Args:
            config: Server configuration
            
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to MCP server: {config.name}")
            
            # Create protocol client
            protocol_client = McpProtocolClient()
            
            # Connect to the server with resolved environment variables
            success = await protocol_client.connect(
                command=config.command,
                args=config.args,
                working_dir=config.working_dir,
                env=config.get_resolved_env()
            )
            
            if success:
                # Get capabilities from the real server
                capabilities = await self._get_server_capabilities_from_protocol(protocol_client, config)
                
                if capabilities:
                    self.server_capabilities[config.name] = capabilities
                    self.connected_servers[config.name] = protocol_client
                    logger.info(f"Successfully connected to {config.name}")
                    return True
                else:
                    await protocol_client.disconnect()
            
        except Exception as e:
            logger.error(f"Failed to connect to MCP server {config.name}: {e}")
        
        return False
    
    async def _get_server_capabilities_from_protocol(self, protocol_client: McpProtocolClient, config: McpServerConfig) -> Optional[McpCapabilities]:
        """Get capabilities from an MCP server using the real protocol.
        
        Args:
            protocol_client: Connected protocol client
            config: Server configuration
            
        Returns:
            Server capabilities if successful, None otherwise
        """
        try:
            # Get server info from protocol client
            server_info = protocol_client.get_server_info() or {}
            server_info.update({
                'name': config.name,
                'description': config.description
            })
            
            # Get tools from the server
            tools_data = await protocol_client.list_tools()
            tools = []
            
            if tools_data:
                for tool_data in tools_data:
                    tool = McpTool(
                        name=tool_data.get('name', ''),
                        description=tool_data.get('description', ''),
                        input_schema=tool_data.get('inputSchema', {}),
                        server_name=config.name
                    )
                    tools.append(tool)
            
            # Resources (not implemented yet)
            resources = []
            
            return McpCapabilities(
                tools=tools,
                resources=resources,
                server_info=server_info
            )
            
        except Exception as e:
            logger.error(f"Error getting capabilities from {config.name}: {e}")
            return None
    
    def get_server_capabilities(self, server_name: str) -> Optional[McpCapabilities]:
        """Get capabilities for a specific server.
        
        Args:
            server_name: Name of the server
            
        Returns:
            Server capabilities if available, None otherwise
        """
        return self.server_capabilities.get(server_name)
    
    def get_all_tools(self) -> List[McpTool]:
        """Get all tools from all connected servers.
        
        Returns:
            List of all available tools
        """
        all_tools = []
        for capabilities in self.server_capabilities.values():
            all_tools.extend(capabilities.tools)
        return all_tools
    
    def get_tools_by_server(self, server_name: str) -> List[McpTool]:
        """Get tools for a specific server.
        
        Args:
            server_name: Name of the server
            
        Returns:
            List of tools for the server
        """
        capabilities = self.server_capabilities.get(server_name)
        return capabilities.tools if capabilities else []
    
    def is_server_connected(self, server_name: str) -> bool:
        """Check if a server is connected.
        
        Args:
            server_name: Name of the server
            
        Returns:
            True if server is connected, False otherwise
        """
        return server_name in self.connected_servers
    
    def get_connected_servers(self) -> List[str]:
        """Get list of connected server names.
        
        Returns:
            List of connected server names
        """
        return list(self.connected_servers.keys())
    
    async def disconnect_server(self, server_name: str) -> bool:
        """Disconnect from a server.
        
        Args:
            server_name: Name of the server to disconnect
            
        Returns:
            True if successfully disconnected, False otherwise
        """
        if server_name in self.connected_servers:
            protocol_client = self.connected_servers[server_name]
            await protocol_client.disconnect()
            del self.connected_servers[server_name]
            del self.server_capabilities[server_name]
            logger.info(f"Disconnected from MCP server: {server_name}")
            return True
        return False
    
    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for server_name in list(self.connected_servers.keys()):
            await self.disconnect_server(server_name)
    
    def format_tools_for_prompt(self, server_names: Optional[List[str]] = None, compact: bool = False) -> str:
        """Format tools information for inclusion in system prompt.
        
        Args:
            server_names: List of specific servers to include (None for all)
            compact: Whether to use compact formatting (for combined content scenarios)
            
        Returns:
            Formatted tools information
        """
        if server_names is None:
            server_names = list(self.server_capabilities.keys())
        
        if compact:
            return self._format_tools_compact(server_names)
        
        prompt_sections = []
        
        for server_name in server_names:
            if server_name not in self.server_capabilities:
                continue
                
            capabilities = self.server_capabilities[server_name]
            server_info = capabilities.server_info
            
            section = f"### MCP Server: {server_name}\n"
            section += f"Description: {server_info.get('description', 'No description available')}\n\n"
            
            if capabilities.tools:
                section += "Available Tools:\n"
                for tool in capabilities.tools:
                    section += f"- **{tool.name}**: {tool.description}\n"
                    # Add input schema summary
                    if tool.input_schema and 'properties' in tool.input_schema:
                        params = list(tool.input_schema['properties'].keys())
                        required = tool.input_schema.get('required', [])
                        if params:
                            param_list = []
                            for param in params:
                                if param in required:
                                    param_list.append(f"{param}*")
                                else:
                                    param_list.append(param)
                            section += f"  Parameters: {', '.join(param_list)} (*required)\n"
                section += "\n"
            else:
                section += "No tools available.\n\n"
            
            # Note about data mirror
            section += f"**IMPORTANT**: The {server_name} MCP server provides access to live data that mirrors "
            section += "or complements the content you have in your context. When the AI mentions using these tools, "
            section += "it understands it's accessing the same underlying data sources that inform your knowledge.\n\n"
            
            prompt_sections.append(section)
        
        if prompt_sections:
            full_section = "## IMPORTANT: Tool Usage Instructions\n\n"
            full_section += "**CRITICAL**: I do NOT have built-in web search capabilities. I must ONLY use external tools provided through MCP (Model Context Protocol) servers when available.\n\n"
            full_section += "**NEVER use `<web_search>` tags** - these do not work and will provide incorrect results.\n\n"
            full_section += "If no MCP tools are available and web search is requested, I should explain that I cannot search the web without proper tool access.\n\n"
            
            full_section += "## MCP (Model Context Protocol) Servers\n\n"
            full_section += "The following external tools are available through MCP servers:\n\n"
            full_section += "\n".join(prompt_sections)
            
            # Add usage instructions
            full_section += "\n### How to Use MCP Tools\n\n"
            full_section += "To call an MCP tool, I MUST use this exact format:\n"
            full_section += "```\n<tool_code>server_name.tool_name(parameter_name=\"value\")</tool_code>\n```\n\n"
            full_section += "Examples:\n"
            full_section += "- `<tool_code>search.web_search(query=\"your search terms\")</tool_code>`\n"
            full_section += "- `<tool_code>filesystem.read_file(path=\"/path/to/file\")</tool_code>`\n"
            full_section += "- `<tool_code>notion.API-post-search(query=\"journal entries\")</tool_code>`\n\n"
            full_section += "**Rules:**\n"
            full_section += "1. Use the exact server and tool names listed above\n"
            full_section += "2. Use exact parameter names from the tool schema\n"
            full_section += "3. Wait for tool execution results before continuing\n"
            full_section += "4. NEVER use built-in `<web_search>` tags - only use MCP format\n\n"

            # Add URL detection and fetching guidance
            full_section += "### 🌐 URL Detection & Web Fetching Guidelines\n\n"
            full_section += "**When user provides URLs (http://, https://, www.):**\n"
            full_section += "1. **ALWAYS fetch the URL first** using `fetch.puppeteer_navigate(url=\"...\")`\n"
            full_section += "2. **Then** optionally search for additional context if needed\n"
            full_section += "3. **Never skip** fetching a URL the user explicitly provides\n\n"

            full_section += "**Example - User provides URL:**\n"
            full_section += "```\n"
            full_section += "User: \"What's X about my brand: https://example.com\"\n"
            full_section += "AI: I'll fetch your website first to understand your brand.\n\n"
            full_section += "<tool_code>fetch.puppeteer_navigate(url=\"https://example.com\")</tool_code>\n"
            full_section += "<tool_code>search.web_search(query=\"what is X in business\")</tool_code>\n"
            full_section += "```\n\n"

            full_section += "**Fetch vs Search Decision:**\n"
            full_section += "- **Fetch**: User provides specific URL to visit\n"
            full_section += "- **Search**: User asks general question without URL\n"
            full_section += "- **Both**: User provides URL AND asks for additional context\n\n"
            
            return full_section
        
        return ""
    
    def _format_tools_compact(self, server_names: List[str]) -> str:
        """Format tools in compact form to avoid triggering content filters."""
        if not server_names:
            return ""
        
        sections = []
        
        for server_name in server_names:
            if server_name not in self.server_capabilities:
                continue
                
            capabilities = self.server_capabilities[server_name]
            server_info = capabilities.server_info
            
            section = f"## {server_name.title()} Tools Available\n"
            section += f"{server_info.get('description', 'External tools')}\n\n"
            
            if capabilities.tools:
                # Include key tools with brief descriptions
                key_tools = ['create_directory', 'write_file', 'read_file', 'list_directory']
                important_tools = [tool for tool in capabilities.tools if tool.name in key_tools]
                other_tools = [tool for tool in capabilities.tools if tool.name not in key_tools]
                
                if important_tools:
                    section += "Key Tools:\n"
                    for tool in important_tools:
                        section += f"- {tool.name}: {tool.description[:50]}...\n"
                    section += "\n"
                
                if other_tools:
                    other_names = [tool.name for tool in other_tools]
                    section += f"Other Tools: {', '.join(other_names)}\n\n"
                
                # Get first available tool for example
                example_tool = capabilities.tools[0] if capabilities.tools else None
                
                section += f"Usage: Use format <tool_code>{server_name}.tool_name(parameter_name=value)</tool_code>\n"
                if example_tool:
                    # Get the first required parameter for the example
                    first_param = None
                    if example_tool.input_schema and 'properties' in example_tool.input_schema:
                        first_param = list(example_tool.input_schema['properties'].keys())[0]
                    
                    if first_param:
                        section += f"Example: <tool_code>{server_name}.{example_tool.name}({first_param}=\"value\")</tool_code>\n"
                    else:
                        section += f"Example: <tool_code>{server_name}.{example_tool.name}()</tool_code>\n"
            else:
                section += "No tools available.\n"
            
            sections.append(section)
        
        if sections:
            return "## External Tools\n\n" + "\n".join(sections)
        
        return "" 