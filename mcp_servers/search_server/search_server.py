#!/usr/bin/env python3
"""
Internet Search MCP Server

An enhanced MCP server that provides reliable internet search capabilities 
using Perplexity API with source attribution, result validation, and transparency features.
"""

import asyncio
import json
import logging
import sys
import urllib.parse
import urllib.request
import time
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Get the project root directory (up two levels from this file)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dotenv_path = os.path.join(project_root, '.env')
    load_dotenv(dotenv_path=dotenv_path)
except ImportError:
    print("Warning: python-dotenv not installed, environment variables from .env file won't be loaded")
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SearchServer:
    """Enhanced MCP server for internet search functionality using Perplexity API."""

    def __init__(self):
        self.tools = [
            {
                "name": "web_search",
                "description": "Search the internet for current information using Perplexity AI with source citations",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to perform"
                        },
                        "model": {
                            "type": "string",
                            "description": "Perplexity model to use (default: sonar-pro)",
                            "default": "sonar-pro",
                            "enum": [
                                "sonar-pro",
                                "sonar-medium",
                                "llama-3.1-sonar-small-128k-online",
                                "llama-3.1-sonar-large-128k-online",
                                "llama-3.1-sonar-huge-128k-online"
                            ]
                        }
                    },
                    "required": ["query"]
                }
            }
        ]
        
        # Get API key from environment
        self.api_key = os.getenv('PERPLEXITY_API_KEY')
        if not self.api_key:
            logger.warning("PERPLEXITY_API_KEY not found in environment variables")
        
        # Cache for recent searches to avoid redundant API calls
        self.search_cache = {}
        self.cache_duration = timedelta(minutes=15)  # Shorter cache for more current results

    def search_perplexity(self, query: str, model: str = "sonar-pro") -> Dict[str, Any]:
        """Perform a search using Perplexity API with source citations."""
        if not self.api_key:
            return {
                "query": query,
                "error": "PERPLEXITY_API_KEY not configured",
                "results": [{
                    "title": "Configuration Error",
                    "description": "Perplexity API key is not configured. Please set PERPLEXITY_API_KEY environment variable.",
                    "url": "https://docs.perplexity.ai/docs/getting-started",
                    "source": "Configuration Error",
                    "timestamp": datetime.now().isoformat()
                }]
            }
        
        # Check cache first
        cache_key = f"{query}:{model}"
        if cache_key in self.search_cache:
            cached_result, timestamp = self.search_cache[cache_key]
            if datetime.now() - timestamp < self.cache_duration:
                logger.info(f"Returning cached result for: {query}")
                return cached_result
        
        try:
            # Prepare the request
            url = "https://api.perplexity.ai/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            # Enhanced prompt that encourages source attribution
            enhanced_query = f"""Please search for current, accurate information about: {query}

Provide a comprehensive answer with:
1. Current, factual information
2. Specific details (addresses, phone numbers, hours, etc. if relevant)
3. Multiple reliable sources
4. Any recent updates or changes

Be precise about facts like locations, contact information, and business details."""

            data = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": enhanced_query
                    }
                ]
            }
            
            # Make the request
            request = urllib.request.Request(
                url, 
                data=json.dumps(data).encode('utf-8'),
                headers=headers
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
            
            # Extract the response content
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                
                # Extract citations if available
                citations = []
                if 'citations' in result:
                    citations = result['citations']
                elif 'choices' in result and len(result['choices']) > 0:
                    # Try to extract citations from the message metadata
                    message = result['choices'][0]['message']
                    if 'citations' in message:
                        citations = message['citations']
                
                # Format the response
                search_result = {
                    "query": query,
                    "model_used": model,
                    "content": content,
                    "citations": citations,
                    "timestamp": datetime.now().isoformat(),
                    "cached": False
                }
                
                # Cache the result
                self.search_cache[cache_key] = (search_result, datetime.now())
                
                return search_result
            
            else:
                return {
                    "query": query,
                    "error": "No response from Perplexity API",
                    "timestamp": datetime.now().isoformat()
                }
                
        except urllib.error.HTTPError as e:
            error_msg = f"HTTP Error {e.code}: {e.reason}"
            if e.code == 401:
                error_msg = "Invalid Perplexity API key. Please check your PERPLEXITY_API_KEY environment variable."
            elif e.code == 429:
                error_msg = "Rate limit exceeded. Please try again later."
            
            logger.error(f"Perplexity API error: {error_msg}")
            return {
                "query": query,
                "error": error_msg,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {
                "query": query,
                "error": f"Search failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }

    def handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialization request."""
        return {
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {
                        "listChanged": False
                    }
                },
                "serverInfo": {
                    "name": "internet-search",
                    "version": "1.0.0"
                }
            }
        }

    def handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools list request."""
        return {
            "result": {
                "tools": self.tools
            }
        }

    def handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool call request."""
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        if tool_name == "web_search":
            query = tool_args.get("query", "")
            model = tool_args.get("model", "sonar-pro")

            search_results = self.search_perplexity(query, model)

            # Format results for MCP response
            content = []

            if "error" in search_results:
                content.append({
                    "type": "text",
                    "text": f"❌ Search Error: {search_results['error']}\n"
                           f"Query: {query}\n"
                           f"Timestamp: {search_results.get('timestamp', 'N/A')}"
                })
            else:
                # Header with search info
                cache_indicator = "🔄 (cached)" if search_results.get("cached", False) else "🔍 (live search)"
                content.append({
                    "type": "text",
                    "text": f"🔍 Perplexity Search Results {cache_indicator}\n"
                           f"Query: {query}\n"
                           f"Model: {search_results.get('model_used', model)}\n"
                           f"Timestamp: {search_results.get('timestamp', 'N/A')}\n"
                           f"{'='*50}\n"
                })

                # Main content
                if 'content' in search_results:
                    content.append({
                        "type": "text",
                        "text": search_results['content']
                    })

                # Citations section
                if search_results.get('citations'):
                    content.append({
                        "type": "text",
                        "text": f"\n\n📚 **Sources & Citations:**\n"
                    })
                    
                    for i, citation in enumerate(search_results['citations'], 1):
                        if isinstance(citation, dict):
                            title = citation.get('title', f'Source {i}')
                            url = citation.get('url', 'N/A')
                            content.append({
                                "type": "text",
                                "text": f"{i}. {title}\n   🔗 {url}\n"
                            })
                        else:
                            content.append({
                                "type": "text",
                                "text": f"{i}. {citation}\n"
                            })

                # Transparency note
                content.append({
                    "type": "text",
                    "text": f"\n\n💡 **Search Transparency:**\n"
                           f"• This information was retrieved using Perplexity AI\n"
                           f"• Results include source citations for verification\n"
                           f"• Information is current as of search timestamp\n"
                           f"• Always verify critical information from original sources"
                })

            return {
                "result": {
                    "content": content
                }
            }

        return {
            "result": {
                "content": [{
                    "type": "text",
                    "text": f"Unknown tool: {tool_name}"
                }],
                "isError": True
            }
        }

    async def run(self):
        """Run the MCP server."""
        logger.info("Internet Search MCP Server starting...")

        try:
            # Read messages from stdin
            for line in sys.stdin:
                try:
                    message = json.loads(line.strip())
                    logger.info(f"Received message: {message.get('method', 'unknown')}")

                    # Handle different message types
                    if message.get("method") == "initialize":
                        response = self.handle_initialize(message.get("params", {}))
                        response["jsonrpc"] = "2.0"
                        response["id"] = message.get("id")

                    elif message.get("method") == "tools/list":
                        response = self.handle_tools_list(message.get("params", {}))
                        response["jsonrpc"] = "2.0"
                        response["id"] = message.get("id")

                    elif message.get("method") == "tools/call":
                        response = self.handle_tools_call(message.get("params", {}))
                        response["jsonrpc"] = "2.0"
                        response["id"] = message.get("id")

                    else:
                        response = {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {message.get('method')}"
                            }
                        }

                    # Send response
                    print(json.dumps(response), flush=True)

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON received: {e}")
                    continue

        except KeyboardInterrupt:
            logger.info("Server shutting down...")
        except Exception as e:
            logger.error(f"Server error: {e}")

def main():
    """Main entry point."""
    server = SearchServer()
    asyncio.run(server.run())

if __name__ == "__main__":
    main()
