"""
Meeting block support using Notion MCP server.

The Notion API doesn't support reading meeting note/transcription blocks directly,
but the Notion MCP server has a notion-fetch tool that can access them.
This module provides utilities to detect and fetch meeting block content.
"""
import os
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class NotionMCPClient:
    """Client for calling Notion MCP server tools."""

    def __init__(self):
        """Initialize the MCP client."""
        self.notion_token = None
        self._load_credentials()

    def _load_credentials(self):
        """Load Notion credentials — auth module first, MCP config fallback."""
        # Primary: unified auth module
        try:
            from promaia.auth import get_integration
            token = get_integration("notion").get_notion_credentials()
            if token:
                self.notion_token = token
                logger.debug("Loaded Notion token from auth module")
                return
        except Exception:
            pass

        # Fallback: mcp_servers.json (legacy)
        try:
            from promaia.utils.env_writer import get_data_dir
            config_paths = [
                Path("mcp_servers.json"),
                get_data_dir() / "mcp_servers.json",
                Path(__file__).parent.parent.parent / "mcp_servers.json"
            ]

            mcp_config_path = None
            for path in config_paths:
                if path.exists():
                    mcp_config_path = path
                    break

            if not mcp_config_path:
                logger.warning("No Notion credentials found — run: maia auth configure notion")
                return

            with open(mcp_config_path, 'r') as f:
                config = json.load(f)

            notion_server = config.get('servers', {}).get('notion', {})
            env_config = notion_server.get('env', {})
            headers_json = env_config.get('OPENAPI_MCP_HEADERS', '{}')
            headers = json.loads(headers_json)
            auth_header = headers.get('Authorization', '')

            if auth_header.startswith('Bearer '):
                self.notion_token = auth_header.replace('Bearer ', '')
                logger.debug("Loaded Notion token from mcp_servers.json (fallback)")

        except Exception as e:
            logger.warning(f"Error loading Notion credentials: {e}")

    async def fetch_block_children(self, block_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch block children including content from meeting blocks.

        Args:
            block_id: Notion block ID

        Returns:
            Dictionary with block children, or None on error
        """
        if not self.notion_token:
            logger.warning("No Notion token available, cannot fetch meeting block content")
            return None

        try:
            # Use the Notion API directly with our token
            from notion_client import AsyncClient

            client = AsyncClient(auth=self.notion_token)

            # Fetch block children
            response = await client.blocks.children.list(
                block_id=block_id,
                page_size=100
            )
            return response

        except Exception as e:
            logger.error(f"Error fetching block children for {block_id}: {e}")
            return None


async def fetch_meeting_block_transcript(block_id: str) -> Optional[str]:
    """
    Fetch the full transcript from a meeting/transcription block.

    Args:
        block_id: ID of the meeting/transcription block

    Returns:
        Full transcript text, or None if unavailable
    """
    client = NotionMCPClient()

    # Try to fetch content using MCP client
    content = await client.fetch_block_children(block_id)

    if not content:
        return None

    # Parse the response to extract transcript content
    # Meeting blocks may contain the transcript in child blocks
    results = content.get('results', [])

    transcript_parts = []
    for block in results:
        block_type = block.get('type')

        # Check for text-containing blocks that might be part of the transcript
        if block_type in ['paragraph', 'bulleted_list_item', 'numbered_list_item']:
            block_content = block.get(block_type, {})
            rich_text = block_content.get('rich_text', [])

            for text_obj in rich_text:
                text = text_obj.get('text', {}).get('content', '')
                if text:
                    transcript_parts.append(text)

    if transcript_parts:
        full_transcript = '\n'.join(transcript_parts)
        logger.info(f"Fetched transcript from meeting block {block_id[:8]}... ({len(full_transcript)} chars)")
        return full_transcript

    return None


def is_meeting_block(block: Dict[str, Any]) -> bool:
    """
    Detect if a block is a meeting/transcription block.

    Meeting blocks are typically returned as:
    - type: "unsupported" with has_children
    - type: "transcript" (if API support is added)

    Args:
        block: Notion block dictionary

    Returns:
        True if block appears to be a meeting block
    """
    block_type = block.get('type', '')

    # Explicit transcript type
    if block_type == 'transcript':
        return True

    # Unsupported blocks with children might be meeting blocks
    if block_type == 'unsupported' and block.get('has_children', False):
        return True

    return False


async def enhance_block_with_meeting_content(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhance a block by fetching meeting content if it's a meeting block.

    Args:
        block: Notion block that may contain meeting content

    Returns:
        Enhanced block with meeting transcript added if available
    """
    if not is_meeting_block(block):
        return block

    block_id = block.get('id')
    if not block_id:
        return block

    # Try to fetch the transcript
    transcript = await fetch_meeting_block_transcript(block_id)

    if transcript:
        # Add transcript as a special property that the markdown converter can use
        block['_meeting_transcript'] = transcript
        logger.debug(f"Enhanced block {block_id[:8]}... with transcript ({len(transcript)} chars)")

    return block


async def enhance_blocks_with_meeting_content(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enhance a list of blocks by fetching meeting content for any meeting blocks.

    Args:
        blocks: List of Notion blocks

    Returns:
        List of blocks with meeting content enhanced
    """
    enhanced_blocks = []

    for block in blocks:
        enhanced_block = await enhance_block_with_meeting_content(block)

        # Recursively enhance children
        if block.get('children'):
            enhanced_children = await enhance_blocks_with_meeting_content(block['children'])
            enhanced_block['children'] = enhanced_children

        enhanced_blocks.append(enhanced_block)

    return enhanced_blocks
