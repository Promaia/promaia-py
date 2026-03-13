#!/usr/bin/env python3
"""
Simple Notion MCP Helper - Creates pages without OpenAPI parameter issues
"""
import asyncio
import json
import os
import sys
from typing import Any

import requests
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Load environment variables (needed for non-Notion keys)
load_dotenv()

app = Server("notion-helper")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools"""
    return [
        Tool(
            name="create_page_in_database",
            description="Create a new page in a Notion database. Handles parameter formatting correctly.",
            inputSchema={
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "The database ID (with or without dashes)"
                    },
                    "title": {
                        "type": "string",
                        "description": "The page title"
                    },
                    "content": {
                        "type": "string",
                        "description": "Optional markdown content for the page body"
                    }
                },
                "required": ["database_id", "title"]
            }
        ),
        Tool(
            name="search_databases",
            description="Search for databases by name",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Database name to search for"
                    }
                },
                "required": ["query"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls"""
    
    from promaia.auth import get_integration
    notion_token = get_integration("notion").get_notion_credentials()
    if not notion_token:
        return [TextContent(
            type="text",
            text=json.dumps({"error": "Notion not configured. Run: maia auth configure notion"})
        )]

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    try:
        if name == "search_databases":
            query = arguments.get("query", "")
            response = requests.post(
                "https://api.notion.com/v1/search",
                headers=headers,
                json={
                    "query": query,
                    "filter": {"value": "database", "property": "object"}
                }
            )
            return [TextContent(type="text", text=response.text)]
        
        elif name == "create_page_in_database":
            database_id = arguments["database_id"].replace("-", "")
            # Add dashes back in UUID format
            database_id_formatted = f"{database_id[:8]}-{database_id[8:12]}-{database_id[12:16]}-{database_id[16:20]}-{database_id[20:]}"
            
            title = arguments["title"]
            content = arguments.get("content", "")
            
            # Create the page
            payload = {
                "parent": {"database_id": database_id_formatted},
                "properties": {
                    "title": {
                        "title": [
                            {
                                "text": {
                                    "content": title
                                }
                            }
                        ]
                    }
                }
            }
            
            response = requests.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                page_data = response.json()
                page_id = page_data["id"]
                
                # Add content if provided
                if content:
                    # Convert markdown to Notion blocks (simple paragraph blocks)
                    blocks = []
                    for paragraph in content.split("\n\n"):
                        if paragraph.strip():
                            blocks.append({
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {
                                    "rich_text": [{
                                        "type": "text",
                                        "text": {"content": paragraph.strip()}
                                    }]
                                }
                            })
                    
                    if blocks:
                        requests.patch(
                            f"https://api.notion.com/v1/blocks/{page_id}/children",
                            headers=headers,
                            json={"children": blocks}
                        )
                
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "success": True,
                        "page_id": page_id,
                        "url": page_data.get("url"),
                        "title": title
                    }, indent=2)
                )]
            else:
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "error": response.text,
                        "status": response.status_code
                    })
                )]
        
        else:
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Unknown tool: {name}"})
            )]
    
    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]


async def main():
    """Run the server"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
