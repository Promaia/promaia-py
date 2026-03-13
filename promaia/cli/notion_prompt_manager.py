"""
Manager for Notion-backed agent prompts.

This module handles:
- Fetching Notion pages as prompts
- Converting them to markdown
- Storing metadata about Notion sources
- Syncing prompts when Notion pages change
"""

import asyncio
import json
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


# Prompt metadata format
# We store metadata at the top of markdown files as HTML comments:
# <!-- notion_prompt_metadata
# {
#   "notion_page_id": "abc123...",
#   "notion_url": "https://www.notion.so/...",
#   "last_synced": "2026-01-22T10:30:00Z",
#   "sync_enabled": true
# }
# -->


def parse_notion_url(url: str) -> Optional[str]:
    """
    Parse a Notion URL to extract the page ID.

    Supported formats:
    - https://www.notion.so/Page-Title-abc123def456
    - https://notion.so/abc123def456
    - https://www.notion.so/workspace/abc123def456
    - https://www.notion.so/workspace/Page-Title-abc123def456?v=...

    Args:
        url: Notion page URL

    Returns:
        Page ID (32 hex characters) or None if invalid
    """
    # Remove query parameters and fragments
    url = url.split('?')[0].split('#')[0]

    # Extract the last part of the URL path
    parts = url.rstrip('/').split('/')
    if not parts:
        return None

    last_part = parts[-1]

    # Notion page IDs are 32 hex characters, often with hyphens
    # They appear at the end of the URL segment, after the last hyphen
    # Example: "Page-Title-abc123def456" -> "abc123def456"

    # Try to find a 32-character hex string (with or without hyphens)
    # Common patterns:
    # 1. abc123def456 (no hyphens)
    # 2. abc123-def456 (with hyphens)
    # 3. Page-Title-abc123def456 (title with ID at end)

    # Remove all hyphens and check if we have a valid ID
    id_candidate = last_part.replace('-', '')

    # Check if it's a valid hex string of length 32
    if len(id_candidate) == 32 and all(c in '0123456789abcdefABCDEF' for c in id_candidate):
        return id_candidate.lower()

    # If the last part wasn't a pure ID, try extracting the last 32 hex chars
    # This handles cases like "Page-Title-abc123def456"
    match = re.search(r'([0-9a-fA-F]{32})$', id_candidate)
    if match:
        return match.group(1).lower()

    # Try another pattern: last segment might be just the ID with hyphens
    # Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (UUID style)
    match = re.search(r'([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})$', last_part)
    if match:
        return match.group(1).replace('-', '').lower()

    logger.warning(f"Could not extract page ID from URL: {url}")
    return None


def format_notion_id(page_id: str) -> str:
    """
    Format a Notion page ID with hyphens in UUID format.

    Args:
        page_id: 32-character hex string

    Returns:
        Formatted ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    """
    if len(page_id) != 32:
        return page_id

    return f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"


async def fetch_notion_page_as_markdown(page_id: str, workspace: Optional[str] = None) -> Optional[str]:
    """
    Fetch a Notion page and convert it to markdown.

    Args:
        page_id: Notion page ID (32 hex characters)
        workspace: Optional workspace name for auth context

    Returns:
        Markdown content or None if failed
    """
    try:
        from promaia.notion.client import get_client, ensure_default_client
        from promaia.notion.pages import get_block_content, get_page_title
        from promaia.markdown.converter import page_to_markdown

        # Get appropriate client
        if workspace:
            try:
                client = get_client(workspace)
            except Exception:
                logger.warning(f"Failed to get workspace client for '{workspace}', using default")
                client = ensure_default_client()
        else:
            client = ensure_default_client()

        # Format the page ID with hyphens for API call
        formatted_id = format_notion_id(page_id)

        # Fetch the page blocks
        logger.info(f"Fetching Notion page {formatted_id}...")
        blocks = await get_block_content(formatted_id)

        if not blocks:
            logger.warning(f"No blocks found for page {formatted_id}")
            return None

        # Get page title
        try:
            title = await get_page_title(formatted_id)
        except Exception as e:
            logger.warning(f"Could not fetch title for page {formatted_id}: {e}")
            title = "Untitled"

        # Convert to markdown
        markdown_content = page_to_markdown(blocks, title)

        logger.info(f"Successfully fetched page {formatted_id} ({len(markdown_content)} chars)")
        return markdown_content

    except Exception as e:
        logger.error(f"Error fetching Notion page {page_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


def extract_metadata_from_prompt(prompt_content: str) -> Optional[Dict[str, Any]]:
    """
    Extract Notion metadata from the top of a prompt file.

    Args:
        prompt_content: Markdown content with potential metadata

    Returns:
        Metadata dict or None if no metadata found
    """
    # Look for metadata comment at the start
    match = re.match(r'^<!--\s*notion_prompt_metadata\s*\n(.*?)\n-->', prompt_content, re.DOTALL)
    if not match:
        return None

    try:
        metadata = json.loads(match.group(1))
        return metadata
    except json.JSONDecodeError:
        logger.warning("Found metadata comment but could not parse JSON")
        return None


def add_metadata_to_prompt(prompt_content: str, metadata: Dict[str, Any]) -> str:
    """
    Add or update Notion metadata in a prompt file.

    Args:
        prompt_content: Markdown content
        metadata: Metadata to add

    Returns:
        Updated content with metadata
    """
    # Remove existing metadata if present
    prompt_content = re.sub(
        r'^<!--\s*notion_prompt_metadata\s*\n.*?\n-->\s*\n',
        '',
        prompt_content,
        flags=re.DOTALL
    )

    # Add new metadata at the top
    metadata_json = json.dumps(metadata, indent=2)
    metadata_comment = f"<!-- notion_prompt_metadata\n{metadata_json}\n-->\n\n"

    return metadata_comment + prompt_content


async def create_notion_prompt(
    notion_url: str,
    filename: Optional[str] = None,
    workspace: Optional[str] = None,
    prompts_dir: Optional[Path] = None
) -> Optional[Path]:
    """
    Create a new prompt file from a Notion page URL.

    Args:
        notion_url: Notion page URL
        filename: Optional filename (without .md extension)
        workspace: Optional workspace for auth context
        prompts_dir: Optional prompts directory (defaults to <data_dir>/agent_prompts)

    Returns:
        Path to created file or None if failed
    """
    # Parse the URL
    page_id = parse_notion_url(notion_url)
    if not page_id:
        logger.error(f"Could not parse Notion URL: {notion_url}")
        return None

    # Fetch the page content
    markdown_content = await fetch_notion_page_as_markdown(page_id, workspace)
    if not markdown_content:
        logger.error(f"Could not fetch Notion page: {notion_url}")
        return None

    # Determine filename
    if not filename:
        # Generate filename from page ID
        filename = f"notion_prompt_{page_id[:8]}"

    # Ensure .md extension
    if not filename.endswith('.md'):
        filename += '.md'

    # Determine output directory
    if prompts_dir is None:
        from promaia.utils.env_writer import get_agent_prompts_dir
        prompts_dir = get_agent_prompts_dir()

    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Check if file already exists
    output_path = prompts_dir / filename
    if output_path.exists():
        logger.warning(f"Prompt file already exists: {output_path}")
        # Could ask user if they want to overwrite
        return None

    # Add metadata
    metadata = {
        "notion_page_id": page_id,
        "notion_url": notion_url,
        "last_synced": datetime.utcnow().isoformat() + "Z",
        "sync_enabled": True
    }

    content_with_metadata = add_metadata_to_prompt(markdown_content, metadata)

    # Write to file
    output_path.write_text(content_with_metadata)
    logger.info(f"Created Notion-backed prompt: {output_path}")

    return output_path


async def sync_notion_prompt(prompt_file: Path, workspace: Optional[str] = None) -> bool:
    """
    Sync a Notion-backed prompt file with its source page.

    Args:
        prompt_file: Path to the prompt file
        workspace: Optional workspace for auth context

    Returns:
        True if synced successfully, False otherwise
    """
    try:
        # Read the file
        content = prompt_file.read_text()

        # Extract metadata
        metadata = extract_metadata_from_prompt(content)
        if not metadata or not metadata.get('sync_enabled'):
            logger.debug(f"Skipping {prompt_file.name}: no sync metadata or sync disabled")
            return False

        page_id = metadata.get('notion_page_id')
        if not page_id:
            logger.warning(f"Skipping {prompt_file.name}: no page ID in metadata")
            return False

        # Fetch updated content
        logger.info(f"Syncing Notion prompt: {prompt_file.name}...")
        markdown_content = await fetch_notion_page_as_markdown(page_id, workspace)

        if not markdown_content:
            logger.error(f"Failed to fetch updated content for {prompt_file.name}")
            return False

        # Update metadata with new sync time
        metadata['last_synced'] = datetime.utcnow().isoformat() + "Z"

        # Add metadata to content
        updated_content = add_metadata_to_prompt(markdown_content, metadata)

        # Write back to file
        prompt_file.write_text(updated_content)
        logger.info(f"✓ Synced {prompt_file.name}")

        return True

    except Exception as e:
        logger.error(f"Error syncing {prompt_file.name}: {e}")
        return False


async def sync_all_notion_prompts(prompts_dir: Optional[Path] = None, workspace: Optional[str] = None) -> Dict[str, Any]:
    """
    Sync all Notion-backed prompts in the prompts directory.

    Args:
        prompts_dir: Optional prompts directory (defaults to <data_dir>/agent_prompts)
        workspace: Optional workspace for auth context

    Returns:
        Dict with sync results: {"synced": [...], "failed": [...], "skipped": [...]}
    """
    if prompts_dir is None:
        from promaia.utils.env_writer import get_agent_prompts_dir
        prompts_dir = get_agent_prompts_dir()

    if not prompts_dir.exists():
        logger.warning(f"Prompts directory does not exist: {prompts_dir}")
        return {"synced": [], "failed": [], "skipped": []}

    results = {
        "synced": [],
        "failed": [],
        "skipped": []
    }

    # Find all markdown files
    md_files = list(prompts_dir.glob("*.md"))

    if not md_files:
        logger.info("No prompt files found to sync")
        return results

    logger.info(f"Found {len(md_files)} prompt files, checking for Notion-backed prompts...")

    for prompt_file in md_files:
        # Check if it has Notion metadata
        content = prompt_file.read_text()
        metadata = extract_metadata_from_prompt(content)

        if not metadata or not metadata.get('notion_page_id'):
            results["skipped"].append(prompt_file.name)
            continue

        if not metadata.get('sync_enabled'):
            logger.debug(f"Sync disabled for {prompt_file.name}")
            results["skipped"].append(prompt_file.name)
            continue

        # Sync the prompt
        success = await sync_notion_prompt(prompt_file, workspace)

        if success:
            results["synced"].append(prompt_file.name)
        else:
            results["failed"].append(prompt_file.name)

    # Log summary
    logger.info(f"Notion prompt sync complete: {len(results['synced'])} synced, {len(results['failed'])} failed, {len(results['skipped'])} skipped")

    return results


def get_prompt_content(prompt_file: Path) -> str:
    """
    Get the prompt content without metadata.

    Args:
        prompt_file: Path to the prompt file

    Returns:
        Clean prompt content (without metadata comments)
    """
    content = prompt_file.read_text()

    # Remove metadata comment if present
    content = re.sub(
        r'^<!--\s*notion_prompt_metadata\s*\n.*?\n-->\s*\n',
        '',
        content,
        flags=re.DOTALL
    )

    return content


def is_notion_backed(prompt_file: Path) -> bool:
    """
    Check if a prompt file is backed by Notion.

    Args:
        prompt_file: Path to the prompt file

    Returns:
        True if the file has Notion metadata
    """
    try:
        content = prompt_file.read_text()
        metadata = extract_metadata_from_prompt(content)
        return metadata is not None and 'notion_page_id' in metadata
    except Exception:
        return False
