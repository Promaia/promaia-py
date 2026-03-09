"""
MCP (Model Context Protocol) JSON-RPC client implementation.

This module provides a real client for communicating with MCP servers 
using the official MCP protocol over stdio.
"""
import asyncio
import json
import logging
import subprocess
import sys
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import os
import signal

logger = logging.getLogger(__name__)

@dataclass
class McpResponse:
    """Represents an MCP JSON-RPC response."""
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    id: Optional[int] = None

class McpProtocolClient:
    """Real MCP protocol client using JSON-RPC over stdio."""
    
    def __init__(self):
        """Initialize the MCP protocol client."""
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.initialized = False
        self.server_info: Optional[Dict[str, Any]] = None
        self.capabilities: Optional[Dict[str, Any]] = None
    
    async def connect(self, command: List[str], args: List[str] = None, 
                     working_dir: str = None, env: Dict[str, str] = None) -> bool:
        """Connect to an MCP server.
        
        Args:
            command: Command to start the server
            args: Additional arguments  
            working_dir: Working directory for the server
            env: Environment variables
            
        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Build full command
            full_command = command + (args or [])
            
            logger.info(f"Starting MCP server: {' '.join(full_command)}")
            
            # Start the server process
            process_env = os.environ.copy()
            if env:
                process_env.update(env)
            
            self.process = subprocess.Popen(
                full_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,  # Unbuffered
                cwd=working_dir,
                env=process_env
            )
            
            # Initialize the connection
            success = await self._initialize()
            if success:
                logger.info("MCP server connected and initialized successfully")
            else:
                logger.error("Failed to initialize MCP server")
                await self.disconnect()
            
            return success
            
        except Exception as e:
            logger.error(f"Error connecting to MCP server: {e}")
            return False
    
    async def _initialize(self) -> bool:
        """Initialize the MCP connection."""
        try:
            # Send initialize request
            init_request = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "promaia-mcp-client",
                        "version": "1.0.0"
                    }
                }
            }
            
            response = await self._send_request(init_request)
            
            if response.success and response.result:
                self.initialized = True
                self.server_info = response.result.get('serverInfo', {})
                self.capabilities = response.result.get('capabilities', {})
                logger.info(f"Initialized MCP server: {self.server_info.get('name', 'unknown')}")
                return True
            else:
                logger.error(f"Initialize failed: {response.error}")
                return False
                
        except Exception as e:
            logger.error(f"Error during initialization: {e}")
            return False
    
    async def list_tools(self) -> Optional[List[Dict[str, Any]]]:
        """Get the list of available tools from the server.
        
        Returns:
            List of tool definitions or None if failed
        """
        if not self.initialized:
            logger.error("Client not initialized")
            return None
        
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {}
            }
            
            response = await self._send_request(request)
            
            if response.success and response.result:
                tools = response.result.get('tools', [])
                logger.info(f"Retrieved {len(tools)} tools from MCP server")
                return tools
            else:
                logger.error(f"tools/list failed: {response.error}")
                return None
                
        except Exception as e:
            logger.error(f"Error listing tools: {e}")
            return None
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call a tool on the MCP server.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool
            
        Returns:
            Tool result or None if failed
        """
        if not self.initialized:
            logger.error("Client not initialized")
            return None
        
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            response = await self._send_request(request)
            
            if response.success and response.result:
                return response.result
            else:
                logger.error(f"Tool call '{tool_name}' failed: {response.error}")
                return None
                
        except Exception as e:
            logger.error(f"Error calling tool '{tool_name}': {e}")
            return None
    
    async def _send_request(self, request: Dict[str, Any]) -> McpResponse:
        """Send a JSON-RPC request to the server.
        
        Args:
            request: JSON-RPC request
            
        Returns:
            Response from the server
        """
        if not self.process:
            return McpResponse(success=False, error={"message": "No process"})
        
        try:
            # Send request
            request_json = json.dumps(request) + '\n'
            self.process.stdin.write(request_json)
            self.process.stdin.flush()
            
            # Read response
            response_line = self.process.stdout.readline()
            if not response_line:
                return McpResponse(success=False, error={"message": "No response"})
            
            response_data = json.loads(response_line.strip())
            
            # Check for JSON-RPC error
            if 'error' in response_data:
                return McpResponse(
                    success=False,
                    error=response_data['error'],
                    id=response_data.get('id')
                )
            
            return McpResponse(
                success=True,
                result=response_data.get('result'),
                id=response_data.get('id')
            )
            
        except Exception as e:
            logger.error(f"Error sending request: {e}")
            return McpResponse(success=False, error={"message": str(e)})
    
    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self.process:
            try:
                # Try to terminate gracefully
                self.process.terminate()
                try:
                    # Wait up to 5 seconds for graceful shutdown
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't stop
                    self.process.kill()
                    self.process.wait()
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
            finally:
                self.process = None
                self.initialized = False
                self.server_info = None
                self.capabilities = None
    
    def _next_id(self) -> int:
        """Get the next request ID."""
        self.request_id += 1
        return self.request_id
    
    def is_connected(self) -> bool:
        """Check if connected to a server."""
        return self.process is not None and self.initialized
    
    def get_server_info(self) -> Optional[Dict[str, Any]]:
        """Get server information."""
        return self.server_info
    
    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        """Get server capabilities."""
        return self.capabilities 