"""
Write execution logs and notes to agent's Notion Journal database.
"""

import logging
import zoneinfo
from datetime import datetime
from typing import Optional
from promaia.agents.agent_config import load_agents as load_agents_from_json

logger = logging.getLogger(__name__)


def _derive_title(content: str) -> str:
    """Extract a short title from journal content."""
    import re
    # Look for a markdown heading
    for line in content.split("\n"):
        line = line.strip()
        m = re.match(r'^#+\s+(.+)', line)
        if m:
            return m.group(1).strip()[:80]
    # Fall back to first non-empty, non-meta line
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip agent meta-commentary
        if line.lower().startswith(("i'll ", "i will ", "let me ", "looking at ")):
            continue
        return line[:80]
    return "Journal Entry"


async def write_journal_entry(
    agent_id: str,
    workspace: str,
    entry_type: str,
    content: str,
    title: Optional[str] = None,
    execution_id: Optional[int] = None
):
    """
    Write entry to agent's Journal database.

    Args:
        agent_id: Agent ID (e.g., "grace")
        workspace: Workspace name
        entry_type: "Execution", "Note", or "Error"
        content: Entry content (text)
        title: Optional title for the entry. Auto-derived from content if not given.
        execution_id: Optional execution ID for linking

    Returns:
        Notion page ID if successful, None otherwise
    """
    from promaia.notion.client import get_client

    # Get agent config
    json_agents = load_agents_from_json()
    agent = None

    for a in json_agents:
        if hasattr(a, 'agent_id') and a.agent_id == agent_id:
            agent = a
            break

    if not agent or not hasattr(agent, 'journal_db_id') or not agent.journal_db_id:
        logger.warning(f"Agent '{agent_id}' has no Journal database, skipping journal entry")
        return

    # Derive a title if none provided
    if not title:
        title = _derive_title(content)

    logger.info(f"📓 Writing to Notion journal: \"{title}\"")

    try:
        client = get_client(workspace)

        # Use local time matching the system prompt timezone
        local_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        now_local = datetime.now(local_tz)
        today = now_local.strftime("%Y-%m-%d")

        # Create journal entry properties
        properties = {
            "Entry": {"title": [{"type": "text", "text": {"content": f"{today} {title}"}}]},
            "Date": {"date": {"start": now_local.isoformat()}},
            "Type": {"select": {"name": entry_type}}
        }

        # Add execution ID if provided
        if execution_id is not None:
            properties["Execution ID"] = {"number": execution_id}

        # Split content into chunks of ~2000 chars per paragraph block (Notion's limit)
        children = []
        chunk_size = 1900

        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                }
            })

        # Create page with content as body blocks
        response = await client.pages.create(
            parent={"database_id": agent.journal_db_id},
            properties=properties,
            children=children
        )

        page_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
        logger.info(f"✅ Journal entry created: \"{today} {title}\"")
        return page_id

    except Exception as e:
        logger.error(f"Error writing journal entry: {e}")
        return None


async def get_recent_journal_entries(
    agent_id: str,
    workspace: str,
    limit: int = 10
) -> list:
    """
    Get recent journal entries for an agent.

    Args:
        agent_id: Agent ID
        workspace: Workspace name
        limit: Maximum number of entries to return

    Returns:
        List of journal entry dictionaries
    """
    from promaia.notion.client import get_client

    # Get agent config
    json_agents = load_agents_from_json()
    agent = None

    for a in json_agents:
        if hasattr(a, 'agent_id') and a.agent_id == agent_id:
            agent = a
            break

    if not agent or not hasattr(agent, 'journal_db_id') or not agent.journal_db_id:
        return []

    try:
        client = get_client(workspace)

        # Query journal database
        response = await client.databases.query(
            database_id=agent.journal_db_id,
            sorts=[{"property": "Date", "direction": "descending"}],
            page_size=limit
        )

        entries = []
        for page in response["results"]:
            props = page["properties"]

            # Read content from page blocks instead of Content property
            page_id = page["id"]
            content = ""
            try:
                # Fetch page blocks to get content
                blocks_response = await client.blocks.children.list(block_id=page_id)
                for block in blocks_response.get("results", []):
                    if block["type"] == "paragraph":
                        paragraph = block.get("paragraph", {})
                        rich_text = paragraph.get("rich_text", [])
                        for text_obj in rich_text:
                            content += text_obj.get("text", {}).get("content", "")
            except Exception as e:
                logger.warning(f"Could not load blocks for journal entry {page_id}: {e}")

            date_prop = (props.get("Date") or {}).get("date") or {}
            type_prop = (props.get("Type") or {}).get("select") or {}
            entry = {
                "date": date_prop.get("start"),
                "type": type_prop.get("name"),
                "content": content,
                "execution_id": (props.get("Execution ID") or {}).get("number")
            }

            entries.append(entry)

        return entries

    except Exception as e:
        logger.error(f"Error loading journal entries: {e}")
        return []
