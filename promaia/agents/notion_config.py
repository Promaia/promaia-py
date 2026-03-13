"""
Load agent configurations from Notion.

This module handles loading agent System Prompts and structure from Notion
while keeping runtime config in JSON.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from promaia.agents.agent_config import AgentConfig, load_agents as load_agents_from_json

logger = logging.getLogger(__name__)


async def load_agent_by_id(agent_id: str, workspace: str) -> Optional[AgentConfig]:
    """
    Load agent configuration combining Notion content + JSON settings.

    Args:
        agent_id: Agent ID (e.g., "grace")
        workspace: Workspace name

    Returns:
        AgentConfig with Notion System Prompt or None if not found
    """
    from promaia.notion.client import get_client
    from promaia.notion.pages import get_block_content, get_page_title
    from promaia.markdown.converter import page_to_markdown

    # 1. Load JSON config (has notion_page_id and runtime settings)
    json_agents = load_agents_from_json()
    json_agent = None

    for agent in json_agents:
        if hasattr(agent, 'agent_id') and agent.agent_id == agent_id:
            json_agent = agent
            break

    if not json_agent:
        logger.warning(f"Agent '{agent_id}' not found in JSON config")
        return None

    # 2. If no Notion integration, return JSON config as-is
    if not json_agent.notion_page_id:
        logger.debug(f"Agent '{agent_id}' has no Notion integration")
        return json_agent

    # 3. Load System Prompt from Notion
    try:
        client = get_client(workspace)

        # Find System Prompt subpage
        system_prompt_content = await load_system_prompt(
            json_agent.notion_page_id,
            json_agent.system_prompt_page_id,
            client
        )

        if system_prompt_content:
            # Override JSON prompt with Notion content
            json_agent.prompt_file = system_prompt_content
            logger.info(f"Loaded System Prompt from Notion for agent '{agent_id}'")
        else:
            logger.warning(f"Could not load System Prompt from Notion, using JSON fallback")

        return json_agent

    except Exception as e:
        logger.error(f"Error loading agent from Notion: {e}")
        logger.info(f"Falling back to JSON config for agent '{agent_id}'")
        return json_agent


async def load_system_prompt(
    agent_page_id: str,
    system_prompt_page_id: Optional[str],
    client
) -> Optional[str]:
    """
    Load System Prompt content from Notion subpage.

    Args:
        agent_page_id: Agent's page ID
        system_prompt_page_id: System Prompt page ID (if known)
        client: Notion client

    Returns:
        System Prompt as markdown string
    """
    from promaia.notion.pages import get_block_content
    from promaia.markdown.converter import page_to_markdown

    try:
        # If we don't have the system prompt page ID, find it
        if not system_prompt_page_id:
            system_prompt_page_id = await find_subpage_by_title(
                agent_page_id,
                "System Prompt",
                client
            )

        if not system_prompt_page_id:
            logger.warning("System Prompt subpage not found")
            return None

        # Get page content
        blocks = await get_block_content(system_prompt_page_id)

        # Convert to markdown
        # page_to_markdown signature is (blocks, properties=None, include_properties=True, ...)
        # Older call sites used a "title" kwarg which is not supported.
        system_prompt = page_to_markdown(blocks, properties=None, include_properties=False)

        return system_prompt

    except Exception as e:
        logger.error(f"Error loading System Prompt: {e}")
        return None


async def find_subpage_by_title(
    parent_page_id: str,
    title: str,
    client
) -> Optional[str]:
    """
    Find a subpage by title within a parent page.

    Args:
        parent_page_id: Parent page ID
        title: Title to search for
        client: Notion client

    Returns:
        Page ID of matching subpage or None
    """
    try:
        # Get all child blocks
        response = await client.blocks.children.list(block_id=parent_page_id)

        for block in response["results"]:
            if block["type"] == "child_page":
                # Get page details to check title
                child_page = await client.pages.retrieve(page_id=block["id"])

                # Extract title
                from promaia.notion.pages import get_page_title
                page_title = get_page_title(child_page)

                if page_title == title:
                    return block["id"]

        return None

    except Exception as e:
        logger.error(f"Error finding subpage '{title}': {e}")
        return None


async def find_subdatabase_by_title(
    parent_page_id: str,
    title: str,
    client
) -> Optional[str]:
    """
    Find a sub-database by title within a parent page.

    Args:
        parent_page_id: Parent page ID
        title: Database title to search for
        client: Notion client

    Returns:
        Database ID or None
    """
    try:
        # Get all child blocks
        response = await client.blocks.children.list(block_id=parent_page_id)

        for block in response["results"]:
            if block["type"] == "child_database":
                # Check title
                if block.get("child_database", {}).get("title") == title:
                    return block["id"]

        return None

    except Exception as e:
        logger.error(f"Error finding sub-database '{title}': {e}")
        return None


async def update_last_run(agent_id: str, workspace: str, timestamp: str):
    """
    Update Last Run property in Notion.

    Args:
        agent_id: Agent ID
        workspace: Workspace name
        timestamp: ISO timestamp
    """
    from promaia.notion.client import get_client

    # Get agent config
    json_agents = load_agents_from_json()
    agent = None

    for a in json_agents:
        if hasattr(a, 'agent_id') and a.agent_id == agent_id:
            agent = a
            break

    if not agent or not agent.notion_page_id:
        return

    try:
        client = get_client(workspace)

        await client.pages.update(
            page_id=agent.notion_page_id,
            properties={
                "Last Run": {"date": {"start": timestamp}}
            }
        )

        logger.info(f"Updated Last Run for agent '{agent_id}'")

    except Exception as e:
        logger.error(f"Error updating Last Run: {e}")
        # Non-critical, don't raise
