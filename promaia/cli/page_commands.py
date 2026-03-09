"""
CLI commands for managing synced Notion pages.

Commands:
    maia page add      — Register a Notion page for bi-directional sync
    maia page list     — List registered pages with sync status
    maia page sync     — Sync one or all registered pages
    maia page remove   — Unregister a page
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-')


async def handle_page_add(args):
    """
    Interactively register a Notion page for sync.

    Usage:
        maia page add
        maia page add --url <notion_url>
    """
    from rich.console import Console
    from promaia.cli.notion_prompt_manager import (
        parse_notion_url,
        format_notion_id,
        fetch_notion_page_as_markdown,
        add_metadata_to_prompt,
    )
    from promaia.notion.pages import get_page_title
    from promaia.config.databases import get_database_manager

    console = Console()
    db_manager = get_database_manager()

    console.print("\n📄 Register Notion Page for Sync\n", style="bold cyan")

    # 1. Get Notion URL
    url = getattr(args, 'url', None)
    if not url:
        url = input("Notion page URL: ").strip()

    if not url:
        console.print("❌ URL is required", style="red")
        return

    page_id = parse_notion_url(url)
    if not page_id:
        console.print("❌ Could not parse page ID from URL", style="red")
        return

    console.print(f"   Page ID: {page_id}", style="dim")

    # Check if already registered
    for name, pg in db_manager.pages.items():
        if pg.notion_page_id == page_id:
            console.print(f"⚠️  Page already registered as '{name}'", style="yellow")
            return

    # 2. Fetch title
    console.print("⏳ Fetching page title...", style="dim")
    try:
        formatted_id = format_notion_id(page_id)
        title = await get_page_title(formatted_id)
    except Exception as e:
        logger.warning(f"Could not fetch title: {e}")
        title = "Untitled"

    console.print(f"   Title: {title}", style="white")

    # 3. Nickname
    default_nickname = _slugify(title) or f"page-{page_id[:8]}"
    nickname_input = input(f"Nickname (default: '{default_nickname}'): ").strip()
    nickname = nickname_input if nickname_input else default_nickname

    # 4. Workspace
    from promaia.config.workspaces import get_workspace_manager
    workspace_mgr = get_workspace_manager()
    default_ws = workspace_mgr.get_default_workspace() or "default"
    ws_input = input(f"Workspace (default: '{default_ws}'): ").strip()
    workspace = ws_input if ws_input else default_ws

    # 5. Description
    description = input("Description (optional): ").strip()

    # 6. Pull content
    console.print("⏳ Pulling page content...", style="dim")
    content = await fetch_notion_page_as_markdown(page_id, workspace)
    if content is None:
        console.print("❌ Failed to fetch page content", style="red")
        return

    # 7. Save to file
    md_path = f"data/md/notion/{workspace}/pages/{nickname}.md"
    full_path = Path(md_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)

    now_str = datetime.now(timezone.utc).isoformat()

    metadata = {
        "notion_page_id": page_id,
        "notion_url": url,
        "nickname": nickname,
        "workspace": workspace,
        "last_synced": now_str,
        "sync_enabled": True,
    }
    content_with_meta = add_metadata_to_prompt(content, metadata)
    full_path.write_text(content_with_meta)

    console.print(f"   Saved to: {md_path}", style="dim")

    # 8. Register in config
    page_data = {
        "notion_page_id": page_id,
        "notion_url": url,
        "nickname": nickname,
        "workspace": workspace,
        "description": description,
        "markdown_path": md_path,
        "sync_enabled": True,
        "last_synced": now_str,
    }
    db_manager.add_page(nickname, page_data)

    console.print(f"\n✅ Page '{nickname}' registered for sync!", style="green")
    console.print(f"   File: {md_path}", style="dim")
    console.print(f"   Sync with: maia page sync --name {nickname}", style="dim")
    console.print(f"   Or sync all: maia sync", style="dim")


async def handle_page_list(args):
    """
    List all registered pages with sync status.

    Usage:
        maia page list
    """
    from rich.console import Console
    from rich.table import Table
    from promaia.config.databases import get_database_manager

    console = Console()
    db_manager = get_database_manager()

    pages = db_manager.pages
    if not pages:
        console.print("No pages registered. Use 'maia page add' to register one.", style="yellow")
        return

    table = Table(title=f"Registered Pages ({len(pages)})")
    table.add_column("Nickname", style="cyan")
    table.add_column("Workspace", style="white")
    table.add_column("Last Synced", style="dim")
    table.add_column("Sync", style="green")
    table.add_column("File", style="dim")

    for name, pg in pages.items():
        sync_status = "✅" if pg.sync_enabled else "⏸️"
        last_synced = pg.last_synced[:19] if pg.last_synced else "never"
        file_exists = "✓" if Path(pg.markdown_path).exists() else "✗"
        table.add_row(
            pg.nickname,
            pg.workspace,
            last_synced,
            sync_status,
            f"{file_exists} {pg.markdown_path}",
        )

    console.print(table)


async def handle_page_sync(args):
    """
    Sync one or all registered pages.

    Usage:
        maia page sync
        maia page sync --name <nickname>
    """
    from rich.console import Console
    from promaia.config.databases import get_database_manager
    from promaia.notion.page_sync import sync_page

    console = Console()
    db_manager = get_database_manager()

    name_filter = getattr(args, 'name', None)

    if name_filter:
        pg = db_manager.get_page(name_filter)
        if not pg:
            console.print(f"❌ Page '{name_filter}' not found", style="red")
            return
        pages_to_sync = {name_filter: pg}
    else:
        pages_to_sync = db_manager.pages

    if not pages_to_sync:
        console.print("No pages to sync", style="yellow")
        return

    console.print(f"🔄 Syncing {len(pages_to_sync)} page(s)...\n", style="cyan")

    results = {"pulled": [], "pushed": [], "skipped": [], "error": []}

    for name, pg in pages_to_sync.items():
        result = await sync_page(pg)
        results[result].append(name)

        icon = {"pulled": "⬇️", "pushed": "⬆️", "skipped": "⏭️", "error": "❌"}
        console.print(f"  {icon.get(result, '?')} {name}: {result}")

    # Save updated last_synced timestamps
    db_manager.save_config()

    # Summary
    console.print()
    if results["pulled"]:
        console.print(f"  ⬇️  Pulled: {len(results['pulled'])}", style="green")
    if results["pushed"]:
        console.print(f"  ⬆️  Pushed: {len(results['pushed'])}", style="green")
    if results["skipped"]:
        console.print(f"  ⏭️  Skipped: {len(results['skipped'])}", style="dim")
    if results["error"]:
        console.print(f"  ❌ Errors: {len(results['error'])}", style="red")


async def handle_page_remove(args):
    """
    Unregister a page and optionally delete the local file.

    Usage:
        maia page remove <name>
    """
    from rich.console import Console
    from promaia.config.databases import get_database_manager

    console = Console()
    db_manager = get_database_manager()

    name = args.name
    pg = db_manager.get_page(name)
    if not pg:
        console.print(f"❌ Page '{name}' not found", style="red")
        return

    # Ask about file deletion
    delete_file = False
    md_path = Path(pg.markdown_path)
    if md_path.exists():
        delete_input = input(f"Delete local file '{pg.markdown_path}'? (y/N): ").strip().lower()
        delete_file = delete_input == 'y'

    db_manager.remove_page(name)

    if delete_file and md_path.exists():
        md_path.unlink()
        console.print(f"   Deleted: {pg.markdown_path}", style="dim")

    console.print(f"✅ Page '{name}' removed", style="green")


def add_page_commands(subparsers):
    """Register 'maia page' command group."""
    page_parser = subparsers.add_parser('page', help='Manage synced Notion pages')
    page_subparsers = page_parser.add_subparsers(dest='page_command', help='Page commands')

    # page add
    add_parser = page_subparsers.add_parser('add', help='Register a Notion page for sync')
    add_parser.add_argument('--url', '-u', help='Notion page URL (skip interactive prompt)')
    add_parser.set_defaults(func=handle_page_add)

    # page list
    list_parser = page_subparsers.add_parser('list', help='List registered pages')
    list_parser.set_defaults(func=handle_page_list)

    # page sync
    sync_parser = page_subparsers.add_parser('sync', help='Sync registered pages')
    sync_parser.add_argument('--name', '-n', help='Sync specific page by nickname')
    sync_parser.set_defaults(func=handle_page_sync)

    # page remove
    remove_parser = page_subparsers.add_parser('remove', help='Unregister a page')
    remove_parser.add_argument('name', help='Page nickname')
    remove_parser.set_defaults(func=handle_page_remove)
