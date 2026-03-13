"""
Pydantic models for MCP (Model Context Protocol) API endpoints.

These models define the request/response schemas for MCP tool execution
and server management functionality.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class McpServerInfo(BaseModel):
    """Information about an MCP server."""
    name: str
    description: Optional[str] = None
    version: Optional[str] = None
    connected: bool
    tools_count: int
    resources_count: int


class McpToolInfo(BaseModel):
    """Information about an MCP tool."""
    name: str
    description: str
    server_name: str
    input_schema: Dict[str, Any]


class McpResourceInfo(BaseModel):
    """Information about an MCP resource."""
    uri: str
    name: Optional[str] = None
    description: Optional[str] = None
    server_name: str


class McpServersResponse(BaseModel):
    """Response for GET /api/mcp/servers."""
    servers: List[McpServerInfo]
    total_connected: int


class McpToolsResponse(BaseModel):
    """Response for GET /api/mcp/tools."""
    tools: List[McpToolInfo]
    servers: List[str]  # Server names that have tools


class McpResourcesResponse(BaseModel):
    """Response for GET /api/mcp/resources."""
    resources: List[McpResourceInfo]
    servers: List[str]


class McpExecuteRequest(BaseModel):
    """Request to execute an MCP tool."""
    server: str
    tool: str
    arguments: Dict[str, Any]


class McpExecuteResponse(BaseModel):
    """Response for MCP tool execution."""
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    server: str
    tool: str
    execution_time: Optional[float] = None


class McpSearchRequest(BaseModel):
    """Request for MCP search command."""
    query: str
    servers: Optional[List[str]] = None  # Limit to specific servers


class McpSearchResult(BaseModel):
    """Single search result from MCP."""
    title: str
    content: str
    url: Optional[str] = None
    source: str
    server: str
    relevance_score: Optional[float] = None


class McpSearchResponse(BaseModel):
    """Response for MCP search."""
    success: bool
    query: str
    results: List[McpSearchResult]
    total_results: int
    servers_queried: List[str]
    execution_time: float


