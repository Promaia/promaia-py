"""
MCP (Model Context Protocol) API endpoints.

These endpoints handle MCP server management and tool execution
for the /mcp commands and AI tool calling functionality.
"""

import logging
import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from promaia.web.models.mcp import (
    McpServersResponse,
    McpToolsResponse, 
    McpResourcesResponse,
    McpExecuteRequest,
    McpExecuteResponse,
    McpSearchRequest,
    McpSearchResponse,
    McpServerInfo,
    McpToolInfo,
    McpResourceInfo,
    McpSearchResult
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/servers", response_model=McpServersResponse)
async def list_mcp_servers():
    """List available MCP servers and their status."""
    try:
        logger.info("🔌 Listing MCP servers")
        
        # Import here to avoid circular imports
        from ...mcp.client import McpClient
        from ...config.mcp_servers import get_mcp_server_configs
        
        # Get configured servers
        server_configs = get_mcp_server_configs()
        
        # Create MCP client if we don't have one
        client = McpClient()
        
        servers = []
        total_connected = 0
        
        for config in server_configs:
            try:
                # Try to connect if not already connected
                if config.name not in client.connected_servers:
                    await client.connect_to_server(config)
                
                connected = config.name in client.connected_servers
                if connected:
                    total_connected += 1
                
                # Get capabilities if connected
                tools_count = 0
                resources_count = 0
                
                if connected and config.name in client.server_capabilities:
                    capabilities = client.server_capabilities[config.name]
                    tools_count = len(capabilities.tools)
                    resources_count = len(capabilities.resources)
                
                server_info = McpServerInfo(
                    name=config.name,
                    description=config.description,
                    version=None,  # Could extract from server info
                    connected=connected,
                    tools_count=tools_count,
                    resources_count=resources_count
                )
                
                servers.append(server_info)
                
            except Exception as e:
                logger.warning(f"⚠️ Error checking server {config.name}: {e}")
                # Add server as disconnected
                servers.append(McpServerInfo(
                    name=config.name,
                    description=config.description,
                    connected=False,
                    tools_count=0,
                    resources_count=0
                ))
        
        logger.info(f"✅ Listed {len(servers)} servers, {total_connected} connected")
        
        return McpServersResponse(
            servers=servers,
            total_connected=total_connected
        )
        
    except Exception as e:
        logger.error(f"❌ Error listing MCP servers: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list MCP servers: {str(e)}")


@router.get("/tools", response_model=McpToolsResponse)
async def list_mcp_tools(server: Optional[str] = Query(None, description="Filter by server name")):
    """List available MCP tools."""
    try:
        logger.info(f"🛠️ Listing MCP tools (server filter: {server})")
        
        from ...mcp.client import McpClient
        
        client = McpClient()
        tools = []
        servers_with_tools = set()
        
        for server_name, capabilities in client.server_capabilities.items():
            if server and server_name != server:
                continue
                
            for tool in capabilities.tools:
                tool_info = McpToolInfo(
                    name=tool.name,
                    description=tool.description,
                    server_name=tool.server_name,
                    input_schema=tool.input_schema
                )
                tools.append(tool_info)
                servers_with_tools.add(tool.server_name)
        
        logger.info(f"✅ Listed {len(tools)} tools from {len(servers_with_tools)} servers")
        
        return McpToolsResponse(
            tools=tools,
            servers=list(servers_with_tools)
        )
        
    except Exception as e:
        logger.error(f"❌ Error listing MCP tools: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list MCP tools: {str(e)}")


@router.get("/resources", response_model=McpResourcesResponse)
async def list_mcp_resources(server: Optional[str] = Query(None, description="Filter by server name")):
    """List available MCP resources."""
    try:
        logger.info(f"📦 Listing MCP resources (server filter: {server})")
        
        from ...mcp.client import McpClient
        
        client = McpClient()
        resources = []
        servers_with_resources = set()
        
        for server_name, capabilities in client.server_capabilities.items():
            if server and server_name != server:
                continue
                
            for resource in capabilities.resources:
                resource_info = McpResourceInfo(
                    uri=resource.uri,
                    name=resource.name,
                    description=resource.description,
                    server_name=server_name
                )
                resources.append(resource_info)
                servers_with_resources.add(server_name)
        
        logger.info(f"✅ Listed {len(resources)} resources from {len(servers_with_resources)} servers")
        
        return McpResourcesResponse(
            resources=resources,
            servers=list(servers_with_resources)
        )
        
    except Exception as e:
        logger.error(f"❌ Error listing MCP resources: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list MCP resources: {str(e)}")


@router.post("/execute", response_model=McpExecuteResponse)
async def execute_mcp_tool(request: McpExecuteRequest):
    """Execute an MCP tool."""
    start_time = time.time()
    
    try:
        logger.info(f"⚡ Executing MCP tool: {request.server}.{request.tool}")
        
        from ...mcp.client import McpClient
        
        client = McpClient()
        
        # Check if server is connected
        if request.server not in client.connected_servers:
            raise HTTPException(
                status_code=400, 
                detail=f"Server '{request.server}' is not connected"
            )
        
        # Execute the tool
        result = await client.call_tool(
            server_name=request.server,
            tool_name=request.tool,
            arguments=request.arguments
        )
        
        execution_time = time.time() - start_time
        
        logger.info(f"✅ MCP tool executed in {execution_time:.2f}s")
        
        return McpExecuteResponse(
            success=True,
            result=result,
            server=request.server,
            tool=request.tool,
            execution_time=execution_time
        )
        
    except HTTPException:
        raise
    except Exception as e:
        execution_time = time.time() - start_time
        logger.error(f"❌ MCP tool execution error: {e}")
        
        return McpExecuteResponse(
            success=False,
            result=None,
            error=str(e),
            server=request.server,
            tool=request.tool,
            execution_time=execution_time
        )


@router.post("/search", response_model=McpSearchResponse)
async def mcp_search(request: McpSearchRequest):
    """Execute MCP search across available search tools."""
    start_time = time.time()
    
    try:
        logger.info(f"🔍 MCP search: {request.query}")
        
        from ...mcp.client import McpClient
        
        client = McpClient()
        results = []
        servers_queried = []
        
        # Find search tools across connected servers
        search_tools = []
        
        for server_name, capabilities in client.server_capabilities.items():
            if request.servers and server_name not in request.servers:
                continue
                
            for tool in capabilities.tools:
                # Look for tools that might be search-related
                if any(search_term in tool.name.lower() for search_term in ['search', 'query', 'find']):
                    search_tools.append((server_name, tool))
        
        # Execute search on each available tool
        for server_name, tool in search_tools:
            try:
                servers_queried.append(server_name)
                
                # Prepare arguments based on tool schema
                args = {"query": request.query}
                
                # Try to match common parameter names
                schema_props = tool.input_schema.get("properties", {})
                if "search_query" in schema_props:
                    args = {"search_query": request.query}
                elif "q" in schema_props:
                    args = {"q": request.query}
                elif "term" in schema_props:
                    args = {"term": request.query}
                
                result = await client.call_tool(server_name, tool.name, args)
                
                # Parse result into search results
                if isinstance(result, list):
                    for item in result:
                        if isinstance(item, dict):
                            search_result = McpSearchResult(
                                title=item.get("title", "Untitled"),
                                content=item.get("content", str(item)),
                                url=item.get("url"),
                                source=item.get("source", tool.name),
                                server=server_name,
                                relevance_score=item.get("score")
                            )
                            results.append(search_result)
                elif isinstance(result, dict):
                    search_result = McpSearchResult(
                        title=result.get("title", "Search Result"),
                        content=result.get("content", str(result)),
                        url=result.get("url"),
                        source=result.get("source", tool.name),
                        server=server_name,
                        relevance_score=result.get("score")
                    )
                    results.append(search_result)
                else:
                    # Handle string or other result types
                    search_result = McpSearchResult(
                        title=f"Result from {tool.name}",
                        content=str(result),
                        source=tool.name,
                        server=server_name
                    )
                    results.append(search_result)
                    
            except Exception as e:
                logger.warning(f"⚠️ Search failed for {server_name}.{tool.name}: {e}")
                continue
        
        execution_time = time.time() - start_time
        
        logger.info(f"✅ MCP search completed: {len(results)} results in {execution_time:.2f}s")
        
        return McpSearchResponse(
            success=True,
            query=request.query,
            results=results,
            total_results=len(results),
            servers_queried=servers_queried,
            execution_time=execution_time
        )
        
    except Exception as e:
        execution_time = time.time() - start_time
        logger.error(f"❌ MCP search error: {e}")
        
        return McpSearchResponse(
            success=False,
            query=request.query,
            results=[],
            total_results=0,
            servers_queried=[],
            execution_time=execution_time
        )
