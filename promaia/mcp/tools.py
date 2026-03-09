"""
MCP tools registry and management.

This module provides a registry for organizing and managing tools from
multiple MCP servers in a unified way.
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from .client import McpClient, McpTool
import logging

logger = logging.getLogger(__name__)

class McpToolRegistry:
    """Registry for managing MCP tools from multiple servers."""
    
    def __init__(self, client: McpClient):
        """Initialize the tools registry.
        
        Args:
            client: MCP client instance
        """
        self.client = client
        self._tool_cache: Dict[str, McpTool] = {}
        self._server_tools: Dict[str, List[str]] = {}
    
    def refresh_tools(self) -> None:
        """Refresh the tools cache from all connected servers."""
        self._tool_cache.clear()
        self._server_tools.clear()
        
        for server_name in self.client.get_connected_servers():
            tools = self.client.get_tools_by_server(server_name)
            tool_names = []
            
            for tool in tools:
                # Use server.tool_name as the key for uniqueness
                tool_key = f"{server_name}.{tool.name}"
                self._tool_cache[tool_key] = tool
                tool_names.append(tool.name)
            
            self._server_tools[server_name] = tool_names
        
        logger.info(f"Refreshed tools cache: {len(self._tool_cache)} tools from {len(self._server_tools)} servers")
    
    def get_tool(self, tool_name: str, server_name: Optional[str] = None) -> Optional[McpTool]:
        """Get a specific tool.
        
        Args:
            tool_name: Name of the tool
            server_name: Server name (if None, searches all servers)
            
        Returns:
            Tool if found, None otherwise
        """
        if server_name:
            tool_key = f"{server_name}.{tool_name}"
            return self._tool_cache.get(tool_key)
        
        # Search all servers
        for tool_key, tool in self._tool_cache.items():
            if tool.name == tool_name:
                return tool
        
        return None
    
    def list_tools(self, server_name: Optional[str] = None) -> List[McpTool]:
        """List available tools.
        
        Args:
            server_name: Server name (if None, returns all tools)
            
        Returns:
            List of tools
        """
        if server_name:
            if server_name in self._server_tools:
                return [self._tool_cache[f"{server_name}.{tool_name}"] 
                       for tool_name in self._server_tools[server_name]]
            return []
        
        return list(self._tool_cache.values())
    
    def list_servers_with_tools(self) -> List[str]:
        """List servers that have tools available.
        
        Returns:
            List of server names with tools
        """
        return list(self._server_tools.keys())
    
    def get_tools_by_category(self) -> Dict[str, List[McpTool]]:
        """Organize tools by category/server.
        
        Returns:
            Dictionary mapping server names to their tools
        """
        result = {}
        for server_name in self._server_tools:
            result[server_name] = self.list_tools(server_name)
        return result
    
    def search_tools(self, query: str) -> List[McpTool]:
        """Search for tools by name or description.
        
        Args:
            query: Search query
            
        Returns:
            List of matching tools
        """
        query_lower = query.lower()
        matching_tools = []
        
        for tool in self._tool_cache.values():
            if (query_lower in tool.name.lower() or 
                query_lower in tool.description.lower()):
                matching_tools.append(tool)
        
        return matching_tools
    
    def get_tool_summary(self) -> Dict[str, Any]:
        """Get a summary of available tools.
        
        Returns:
            Summary information about tools
        """
        summary = {
            'total_tools': len(self._tool_cache),
            'servers': len(self._server_tools),
            'by_server': {}
        }
        
        for server_name, tool_names in self._server_tools.items():
            summary['by_server'][server_name] = {
                'tool_count': len(tool_names),
                'tools': tool_names
            }
        
        return summary 