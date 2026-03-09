"""
Bi-directional Notion page sync.

Handles pulling page content from Notion to local markdown,
pushing local markdown changes back to Notion, and syncing
based on last-edited timestamps.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from promaia.cli.notion_prompt_manager import (
    fetch_notion_page_as_markdown,
    format_notion_id,
    add_metadata_to_prompt,
    extract_metadata_from_prompt,
)
from promaia.agents.notion_setup import markdown_to_notion_blocks

logger = logging.getLogger(__name__)


async def pull_page(page_id: str, workspace: Optional[str] = None) -> Optional[str]:
    """
    Pull a Notion page and return its markdown content.

    Args:
        page_id: 32-char hex Notion page ID
        workspace: Optional workspace name for auth context

    Returns:
        Markdown string or None on failure
    """
    return await fetch_notion_page_as_markdown(page_id, workspace)


async def push_page(page_id: str, markdown: str, workspace: Optional[str] = None) -> bool:
    """
    Replace entire Notion page content with local markdown.

    Steps:
    1. List existing blocks and delete them
    2. Convert markdown to Notion blocks
    3. Append new blocks (batched in 100s)

    Args:
        page_id: Notion page ID
        markdown: Markdown content to push
        workspace: Optional workspace for auth context

    Returns:
        True if successful
    """
    try:
        from promaia.notion.client import get_client, ensure_default_client

        if workspace:
            try:
                client = get_client(workspace)
            except Exception:
                client = ensure_default_client()
        else:
            client = ensure_default_client()

        formatted_id = format_notion_id(page_id)

        # 1. Delete all existing blocks
        existing = await client.blocks.children.list(block_id=formatted_id)
        for block in existing.get("results", []):
            try:
                await client.blocks.delete(block_id=block["id"])
            except Exception as e:
                logger.warning(f"Could not delete block {block['id']}: {e}")

        # 2. Convert markdown to Notion blocks
        # Strip metadata comment before converting
        clean_md = markdown
        import re
        clean_md = re.sub(
            r'^<!--\s*notion_prompt_metadata\s*\n.*?\n-->\s*\n',
            '',
            clean_md,
            flags=re.DOTALL
        )

        blocks = markdown_to_notion_blocks(clean_md)

        if not blocks:
            logger.info(f"No blocks to push for page {formatted_id}")
            return True

        # 3. Append in batches of 100 (Notion API limit)
        for i in range(0, len(blocks), 100):
            batch = blocks[i:i + 100]
            await client.blocks.children.append(
                block_id=formatted_id,
                children=batch,
            )

        logger.info(f"Pushed {len(blocks)} blocks to page {formatted_id}")
        return True

    except Exception as e:
        logger.error(f"Error pushing to Notion page {page_id}: {e}")
        return False


async def get_page_last_edited(page_id: str, workspace: Optional[str] = None) -> Optional[datetime]:
    """
    Get the last_edited_time of a Notion page.

    Args:
        page_id: Notion page ID
        workspace: Optional workspace for auth context

    Returns:
        datetime (UTC) or None on failure
    """
    try:
        from promaia.notion.client import get_client, ensure_default_client

        if workspace:
            try:
                client = get_client(workspace)
            except Exception:
                client = ensure_default_client()
        else:
            client = ensure_default_client()

        formatted_id = format_notion_id(page_id)
        page = await client.pages.retrieve(page_id=formatted_id)

        edited_str = page.get("last_edited_time", "")
        if edited_str:
            # Notion returns ISO 8601 with timezone
            dt = datetime.fromisoformat(edited_str.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)

        return None

    except Exception as e:
        logger.error(f"Error getting last_edited_time for page {page_id}: {e}")
        return None


async def sync_page(page_config) -> str:
    """
    Bi-directional sync for a registered page.

    Compares local file mtime and Notion last_edited_time against
    the stored last_synced timestamp to determine sync direction.

    Args:
        page_config: PageConfig object with notion_page_id, markdown_path, etc.

    Returns:
        "pulled", "pushed", "skipped", or "error"
    """
    try:
        if not page_config.sync_enabled:
            return "skipped"

        page_id = page_config.notion_page_id
        md_path = Path(page_config.markdown_path)
        workspace = page_config.workspace

        # Get Notion last_edited_time
        notion_edited = await get_page_last_edited(page_id, workspace)
        if notion_edited is None:
            logger.warning(f"Could not get Notion edit time for {page_config.nickname}")
            return "error"

        # Get local file mtime (or None if file doesn't exist)
        local_edited = None
        if md_path.exists():
            mtime = md_path.stat().st_mtime
            local_edited = datetime.fromtimestamp(mtime, tz=timezone.utc)

        # Parse last_synced from config
        last_synced = None
        if page_config.last_synced:
            try:
                last_synced = datetime.fromisoformat(
                    page_config.last_synced.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        # Determine direction
        if last_synced is None and local_edited is not None:
            # First sync with existing local file — compare directly
            notion_changed = True
            local_changed = True
        else:
            notion_changed = last_synced is None or notion_edited > last_synced
            local_changed = (
                local_edited is not None
                and last_synced is not None
                and local_edited > last_synced
            )

        def _stamp_after_write(path: Path) -> str:
            """Return an ISO timestamp from the file's actual mtime after writing."""
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

        if notion_changed and not local_changed:
            # Pull from Notion
            content = await pull_page(page_id, workspace)
            if content is None:
                return "error"

            metadata = {
                "notion_page_id": page_id,
                "nickname": page_config.nickname,
                "workspace": workspace,
                "last_synced": "",  # placeholder, updated below
                "sync_enabled": True,
            }
            content_with_meta = add_metadata_to_prompt(content, metadata)

            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(content_with_meta)

            # Use file's mtime so next sync won't see it as "local changed"
            page_config.last_synced = _stamp_after_write(md_path)
            logger.info(f"Pulled page '{page_config.nickname}' from Notion")
            return "pulled"

        elif local_changed and not notion_changed:
            # DISABLED: Push to Notion is disabled due to duplicate page bug.
            # Local changes will be preserved but not pushed back to Notion.
            logger.info(f"Page '{page_config.nickname}' has local changes but push is disabled (duplicate page bug)")
            return "skipped"

        elif notion_changed and local_changed:
            # Both changed — latest-edit-wins
            if notion_edited >= local_edited:
                content = await pull_page(page_id, workspace)
                if content is None:
                    return "error"
                metadata = {
                    "notion_page_id": page_id,
                    "nickname": page_config.nickname,
                    "workspace": workspace,
                    "last_synced": "",
                    "sync_enabled": True,
                }
                content_with_meta = add_metadata_to_prompt(content, metadata)
                md_path.write_text(content_with_meta)
                page_config.last_synced = _stamp_after_write(md_path)
                logger.info(f"Conflict: pulled page '{page_config.nickname}' (Notion newer)")
                return "pulled"
            else:
                # DISABLED: Push to Notion is disabled due to duplicate page bug.
                logger.info(f"Conflict: page '{page_config.nickname}' has local changes but push is disabled (duplicate page bug)")
                return "skipped"

        else:
            # Neither changed
            return "skipped"

    except Exception as e:
        logger.error(f"Error syncing page '{page_config.nickname}': {e}")
        return "error"
