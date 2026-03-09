"""
Functions for fetching and managing prompts from Notion.
"""
import os
import logging
import asyncio
from typing import Optional
from pathlib import Path
from datetime import datetime

from promaia.utils.env_writer import get_prompts_dir

logger = logging.getLogger(__name__)

# Legacy hardcoded ID - kept for backward compatibility
MAIN_PROMPT_PAGE_ID = "292d1339-6967-80a5-84ed-cc171358ccb7"

# Local prompt file path
LOCAL_PROMPT_PATH = get_prompts_dir() / "prompt.md"


def get_main_prompt(workspace: Optional[str] = None) -> Optional[str]:
    """
    Fetch the main Promaia prompt from Notion.

    Args:
        workspace: Optional workspace name. If not provided, uses default workspace.

    Returns:
        Prompt text from Notion, or None if fetch fails
    """
    try:
        # Get workspace-specific main prompt page ID
        from promaia.config.workspaces import get_workspace_manager

        workspace_mgr = get_workspace_manager()

        # Use provided workspace or default
        if workspace is None:
            workspace = workspace_mgr.get_default_workspace()

        # Get workspace config
        workspace_config = workspace_mgr.get_workspace(workspace) if workspace else None

        # Determine which page ID to use
        page_id = None
        if workspace_config and workspace_config.main_prompt_page_id:
            page_id = workspace_config.main_prompt_page_id
            logger.debug(f"Using workspace-specific Main prompt page ID for '{workspace}'")
        else:
            # Fall back to hardcoded ID for backward compatibility
            page_id = MAIN_PROMPT_PAGE_ID
            logger.debug("Using legacy hardcoded Main prompt page ID")

        if not page_id:
            logger.debug("No Main prompt page ID configured")
            return None
        # Import MCP client functions
        from promaia.mcp.client import McpClient
        from promaia.config.mcp_servers import get_mcp_manager

        # Get Notion server config
        mcp_manager = get_mcp_manager()
        notion_config = mcp_manager.get_server("notion")

        if not notion_config:
            logger.debug("Notion MCP server not configured")
            return None

        # Create MCP client and connect to Notion
        mcp_client = McpClient()

        # Run async operation in sync context
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Connect to Notion server
        connected = loop.run_until_complete(mcp_client.connect_to_server(notion_config))

        if not connected:
            logger.warning("Failed to connect to Notion MCP server")
            return None

        # Fetch page blocks
        from promaia.mcp.execution import McpToolExecutor

        executor = McpToolExecutor(mcp_client)

        # Call the get-block-children tool
        tool_call = {
            'server': 'notion',
            'tool': 'API-get-block-children',
            'arguments': {'block_id': page_id}
        }
        result_dict = loop.run_until_complete(
            executor.execute_single_tool(tool_call)
        )

        # Extract the actual result
        if not result_dict.get('success'):
            logger.warning(f"Failed to fetch prompt blocks from Notion: {result_dict.get('error')}")
            return None

        result = result_dict.get('result')
        logger.debug(f"Raw MCP result: {result}")

        # Disconnect
        loop.run_until_complete(mcp_client.disconnect_all())

        # Handle MCP protocol result format
        if isinstance(result, dict) and 'content' in result:
            # Extract content from MCP response
            content = result['content']
            if isinstance(content, list) and len(content) > 0:
                # Get the first content item's text
                first_content = content[0]
                if isinstance(first_content, dict) and 'text' in first_content:
                    result_text = first_content['text']
                    # Parse as JSON if it's a string
                    if isinstance(result_text, str):
                        import json
                        try:
                            result = json.loads(result_text)
                        except json.JSONDecodeError:
                            result = result_text

        if not result or (isinstance(result, dict) and "error" in result):
            logger.warning(f"Failed to fetch prompt blocks from Notion: {result}")
            return None

        # Parse blocks and extract text
        prompt_text = _parse_notion_blocks(result)

        return prompt_text

    except Exception as e:
        logger.warning(f"Error fetching prompt from Notion: {e}")
        return None


def _parse_notion_blocks(blocks_response: dict) -> str:
    """
    Parse Notion blocks response and extract plain text.

    Args:
        blocks_response: Response from API-get-block-children

    Returns:
        Formatted prompt text
    """
    import json

    # Handle response format
    if isinstance(blocks_response, str):
        try:
            blocks_response = json.loads(blocks_response)
        except json.JSONDecodeError:
            return ""

    prompt_text = ""
    results = blocks_response.get("results", [])

    for block in results:
        block_type = block.get("type")

        if block_type == "paragraph":
            rich_text = block.get("paragraph", {}).get("rich_text", [])
            line = ""
            for text_obj in rich_text:
                text_content = text_obj.get("plain_text", "")
                # Preserve bold formatting with **
                if text_obj.get("annotations", {}).get("bold"):
                    text_content = f"**{text_content}**"
                line += text_content
            if line.strip():  # Only add non-empty lines
                prompt_text += line + "\n"
            else:
                prompt_text += "\n"  # Preserve empty lines for spacing

        elif block_type == "bulleted_list_item":
            rich_text = block.get("bulleted_list_item", {}).get("rich_text", [])
            line = "- "
            for text_obj in rich_text:
                line += text_obj.get("plain_text", "")
            prompt_text += line + "\n"

        elif block_type == "numbered_list_item":
            rich_text = block.get("numbered_list_item", {}).get("rich_text", [])
            line = ""
            for text_obj in rich_text:
                line += text_obj.get("plain_text", "")
            prompt_text += line + "\n"

        elif block_type == "heading_1":
            rich_text = block.get("heading_1", {}).get("rich_text", [])
            line = "# "
            for text_obj in rich_text:
                line += text_obj.get("plain_text", "")
            prompt_text += line + "\n\n"

        elif block_type == "heading_2":
            rich_text = block.get("heading_2", {}).get("rich_text", [])
            line = "## "
            for text_obj in rich_text:
                line += text_obj.get("plain_text", "")
            prompt_text += line + "\n\n"

        elif block_type == "heading_3":
            rich_text = block.get("heading_3", {}).get("rich_text", [])
            line = "### "
            for text_obj in rich_text:
                line += text_obj.get("plain_text", "")
            prompt_text += line + "\n\n"

        elif block_type == "code":
            code_text = block.get("code", {}).get("rich_text", [])
            code_content = ""
            for text_obj in code_text:
                code_content += text_obj.get("plain_text", "")
            if code_content:
                prompt_text += f"```\n{code_content}\n```\n"

    return prompt_text.strip()


def sync_main_prompt_to_file(workspace: Optional[str] = None, force: bool = False) -> bool:
    """
    Sync the main prompt from Notion to the local file for fast runtime access.

    Args:
        workspace: Optional workspace name. If not provided, uses default workspace.
        force: If True, sync even if file exists and is recent

    Returns:
        True if sync was successful, False otherwise
    """
    try:
        # Check if we need to sync
        if not force and os.path.exists(LOCAL_PROMPT_PATH):
            # Check file age - only sync if older than 1 hour
            file_mtime = os.path.getmtime(LOCAL_PROMPT_PATH)
            age_hours = (datetime.now().timestamp() - file_mtime) / 3600

            if age_hours < 1:
                logger.debug(f"Prompt file is recent (< 1 hour old), skipping sync")
                return True

        # Fetch prompt from Notion
        prompt_text = get_main_prompt(workspace=workspace)

        if not prompt_text:
            logger.warning("Failed to fetch prompt from Notion for sync")
            return False

        # Ensure prompts directory exists
        prompt_dir = Path(LOCAL_PROMPT_PATH).parent
        prompt_dir.mkdir(parents=True, exist_ok=True)

        # Write to file
        with open(LOCAL_PROMPT_PATH, 'w', encoding='utf-8') as f:
            f.write(prompt_text)

        logger.info(f"Successfully synced Main prompt from Notion to {LOCAL_PROMPT_PATH}")
        return True

    except Exception as e:
        logger.error(f"Error syncing prompt to file: {e}")
        return False


def get_main_prompt_from_file() -> Optional[str]:
    """
    Load the main prompt from the local file.

    Returns:
        Prompt text from file, or None if file doesn't exist
    """
    try:
        if os.path.exists(LOCAL_PROMPT_PATH):
            with open(LOCAL_PROMPT_PATH, 'r', encoding='utf-8') as f:
                return f.read()
        return None
    except Exception as e:
        logger.warning(f"Error loading prompt from file: {e}")
        return None
