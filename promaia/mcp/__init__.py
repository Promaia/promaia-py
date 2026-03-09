"""
MCP (Model Context Protocol) integration for Promaia.

This package provides client capabilities for connecting to and communicating
with MCP servers to extend Promaia's capabilities with external tools and data sources.
"""

from .client import McpClient
from .tools import McpToolRegistry
from .protocol import McpProtocolClient
from .execution import McpToolExecutor

__all__ = ['McpClient', 'McpToolRegistry', 'McpProtocolClient', 'McpToolExecutor'] 