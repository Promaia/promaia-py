"""
MCP tool execution engine.

This module handles parsing tool calls from AI responses and executing them
via connected MCP servers, then formatting the results.
"""
import xml.etree.ElementTree as ET
import re
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from .client import McpClient

logger = logging.getLogger(__name__)

class McpToolExecutor:
    """Executes MCP tools called by the AI."""
    
    def __init__(self, mcp_client: McpClient):
        """Initialize with an MCP client.
        
        Args:
            mcp_client: Connected MCP client with available servers
        """
        self.mcp_client = mcp_client
    
    def parse_tool_calls(self, ai_response: str) -> List[Dict[str, Any]]:
        """Parse tool calls from AI response.
        
        Supports these formats:
        1. <tool_code>server.tool(args)</tool_code>
        2. <invoke name="tool_name"><parameter name="param">value</parameter></invoke>
        
        Args:
            ai_response: The AI's response text
            
        Returns:
            List of tool call dictionaries with server, tool, and arguments
        """
        tool_calls = []
        
        # Pattern: <tool_code>server.tool(args)</tool_code>
        tool_code_pattern = r'<tool_code>\s*(\w+)\.([\w-]+)\s*\((.*?)\)\s*</tool_code>'
        matches = re.findall(tool_code_pattern, ai_response, re.DOTALL)
        
        for match in matches:
            server_name, tool_name, args_str = match
            try:
                arguments = self._parse_arguments(args_str)
                tool_calls.append({
                    'server': server_name,
                    'tool': tool_name,
                    'arguments': arguments,
                    'raw_args': args_str
                })
            except Exception as e:
                logger.error(f"Error parsing tool call arguments: {e}")
        
        # Pattern: <invoke name="tool_name"><parameter name="param">value</parameter></invoke>
        invoke_pattern = r'<invoke name="([\w-]+)">(.*?)</invoke>'
        invoke_matches = re.findall(invoke_pattern, ai_response, re.DOTALL)
        
        for tool_name, invoke_content in invoke_matches:
            try:
                # Extract parameters from <parameter> tags
                param_pattern = r'<parameter name="([^"]+)">([^<]*)</parameter>'
                param_matches = re.findall(param_pattern, invoke_content)
                
                arguments = {}
                for param_name, param_value in param_matches:
                    # Try to parse JSON values, otherwise use as string
                    try:
                        if param_value.strip().startswith(('{', '[')):
                            arguments[param_name] = json.loads(param_value)
                        else:
                            arguments[param_name] = param_value.strip()
                    except json.JSONDecodeError:
                        arguments[param_name] = param_value.strip()
                
                # Determine server based on connected servers and tool name
                server_name = self._determine_server_for_tool(tool_name)
                
                tool_calls.append({
                    'server': server_name,
                    'tool': tool_name,
                    'arguments': arguments,
                    'raw_args': invoke_content
                })
                
            except Exception as e:
                logger.warning(f"Failed to parse invoke format for '{tool_name}': {e}")
                continue
        
        return tool_calls
    
    def _determine_server_for_tool(self, tool_name: str) -> str:
        """Determine which server provides a given tool.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            Server name that provides the tool
        """
        connected_servers = self.mcp_client.get_connected_servers()
        
        # If only one server, use it
        if len(connected_servers) == 1:
            return connected_servers[0]
        
        # Check each server for the tool
        for server_name in connected_servers:
            tools = self.mcp_client.get_tools_by_server(server_name)
            if any(tool.name == tool_name for tool in tools):
                return server_name
        
        # Fallback heuristics
        if tool_name in ['web_search', 'search']:
            return 'search'
        elif 'API-' in tool_name or 'notion' in tool_name.lower():
            return 'notion'
        elif 'file' in tool_name.lower() or 'directory' in tool_name.lower():
            return 'filesystem'
        elif 'git' in tool_name.lower():
            return 'git'
        elif 'sql' in tool_name.lower() or 'query' in tool_name.lower():
            return 'sqlite'
        
        # Default to first connected server
        return connected_servers[0] if connected_servers else 'unknown'
    
    def _parse_arguments(self, args_str: str) -> Dict[str, Any]:
        """Parse tool arguments from string.
        
        Args:
            args_str: Arguments string from the tool call
            
        Returns:
            Dictionary of parsed arguments
        """
        args_str = args_str.strip()
        
        if not args_str:
            return {}
        
        # Try JSON format first
        try:
            return json.loads(f"{{{args_str}}}")
        except:
            pass
        
        # Try Python-style arguments: key=value, key="value"
        arguments = {}
        
        # Split by commas, but respect quotes
        parts = self._split_arguments(args_str)
        
        for part in parts:
            part = part.strip()
            if '=' in part:
                key, value = part.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                # Remove quotes if present
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                
                arguments[key] = value
        
        return arguments
    
    def _split_arguments(self, args_str: str) -> List[str]:
        """Split arguments by comma, respecting quotes."""
        parts = []
        current_part = ""
        in_quotes = False
        quote_char = None
        
        for char in args_str:
            if char in ['"', "'"] and not in_quotes:
                in_quotes = True
                quote_char = char
                current_part += char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
                current_part += char
            elif char == ',' and not in_quotes:
                if current_part.strip():
                    parts.append(current_part.strip())
                current_part = ""
            else:
                current_part += char
        
        if current_part.strip():
            parts.append(current_part.strip())
        
        return parts
    
    async def execute_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute a list of tool calls.
        
        Args:
            tool_calls: List of parsed tool calls
            
        Returns:
            List of execution results
        """
        results = []
        
        for tool_call in tool_calls:
            result = await self.execute_single_tool(tool_call)
            results.append(result)
        
        return results
    
    async def execute_single_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single tool call.
        
        Args:
            tool_call: Tool call dictionary
            
        Returns:
            Execution result
        """
        server_name = tool_call['server']
        tool_name = tool_call['tool']
        arguments = tool_call['arguments']
        
        # Check if server is connected
        if not self.mcp_client.is_server_connected(server_name):
            return {
                'success': False,
                'error': f"Server '{server_name}' is not connected",
                'tool_call': tool_call
            }
        
        # Get the protocol client for this server
        protocol_client = self.mcp_client.connected_servers.get(server_name)
        
        if not protocol_client:
            return {
                'success': False,
                'error': f"No protocol client for server '{server_name}'",
                'tool_call': tool_call
            }
        
        try:
            logger.info(f"Executing {server_name}.{tool_name} with args: {arguments}")
            print(f"🔧 Executing {server_name}.{tool_name}...")
            
            # Execute the tool
            result = await protocol_client.call_tool(tool_name, arguments)
            
            if result:
                return {
                    'success': True,
                    'result': result,
                    'tool_call': tool_call
                }
            else:
                return {
                    'success': False,
                    'error': f"Tool execution returned no result",
                    'tool_call': tool_call
                }
        
        except Exception as e:
            logger.error(f"Error executing tool {server_name}.{tool_name}: {e}")
            return {
                'success': False,
                'error': str(e),
                'tool_call': tool_call
            }
    
    def format_tool_results(self, results: List[Dict[str, Any]], show_raw: bool = False) -> str:
        """Format tool execution results for display.
        
        Args:
            results: List of execution results
            show_raw: Whether to include raw response data for debugging
            
        Returns:
            Formatted results string
        """
        if not results:
            if show_raw:
                print("🔍 No results to format")
            return ""
        
        if show_raw:
            print(f"🔍 Formatting {len(results)} results: {results}")
        formatted = "\n🔧 Tool Execution Results:\n"
        
        for i, result in enumerate(results, 1):
            tool_call = result['tool_call']
            server = tool_call['server']
            tool = tool_call['tool']
            
            if result['success']:
                formatted += f"\n✅ {server}.{tool}:\n"
                
                # Show raw response first for debugging
                if show_raw:
                    result_data = result['result']
                    formatted += f"\n🔍 Raw Response:\n"
                    formatted += f"```json\n{json.dumps(result_data, indent=2)}\n```\n"
                    formatted += f"\n📋 Formatted Output:\n"
                
                # Extract and format the actual result content
                result_data = result['result']
                if 'content' in result_data:
                    content = result_data['content']
                    if isinstance(content, list) and content:
                        # Process all content items, not just the first one
                        for content_item in content:
                            if isinstance(content_item, dict) and 'text' in content_item:
                                formatted += f"{content_item['text']}"
                            else:
                                formatted += f"{content_item}"
                        formatted += "\n"
                    else:
                        formatted += f"{content}\n"
                else:
                    formatted += f"{result_data}\n"
            else:
                formatted += f"\n❌ {server}.{tool} failed:\n"
                formatted += f"Error: {result['error']}\n"
                
                # Show raw error data if available
                if show_raw and 'result' in result:
                    formatted += f"\n🔍 Raw Error Data:\n"
                    formatted += f"```json\n{json.dumps(result.get('result', {}), indent=2)}\n```\n"
        
        return formatted
    
    def has_tool_calls(self, ai_response: str) -> bool:
        """Check if AI response contains tool calls.

        Args:
            ai_response: The AI's response text

        Returns:
            True if tool calls are present
        """
        return (('<tool_code>' in ai_response and '</tool_code>' in ai_response) or
                ('<invoke name="' in ai_response and '</invoke>' in ai_response)) 