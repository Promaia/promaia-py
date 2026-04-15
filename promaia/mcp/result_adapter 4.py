"""
Adapter between official mcp.types result objects and the dict format
used by promaia's execution layer (McpToolExecutor / execution.py).

This is the single conversion point — no inline .content[0].text elsewhere.
"""
from typing import Any, Dict, Optional

from mcp.types import CallToolResult, TextContent, ImageContent, EmbeddedResource


def adapt_call_tool_result(result: CallToolResult) -> Optional[Dict[str, Any]]:
    """Convert a CallToolResult from the official mcp client to the dict format
    that execution.py and downstream consumers expect.

    Expected output shape (matching what the old hand-rolled protocol returned):
        {
            "content": [
                {"type": "text", "text": "..."},
                ...
            ],
            "isError": False
        }
    """
    if result is None:
        return None

    content_list = []
    for block in (result.content or []):
        if isinstance(block, TextContent):
            content_list.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            content_list.append({
                "type": "image",
                "data": block.data,
                "mimeType": block.mimeType,
            })
        elif isinstance(block, EmbeddedResource):
            content_list.append({"type": "resource", "resource": block.resource})
        else:
            # Unknown content type — serialize what we can
            content_list.append({"type": "unknown", "text": str(block)})

    return {
        "content": content_list,
        "isError": bool(result.isError),
    }


def adapt_tool_list(tools_result) -> list[Dict[str, Any]]:
    """Convert a ListToolsResult to the list-of-dicts format used by the old
    protocol client and expected by McpClient._get_server_capabilities_from_protocol.

    Each dict has: name, description, inputSchema.
    """
    out = []
    for tool in (tools_result.tools or []):
        out.append({
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.inputSchema or {},
        })
    return out
