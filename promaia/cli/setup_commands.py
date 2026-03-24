"""
Interactive setup wizard for Promaia.

Guides the user through:
1. AI provider selection (Claude, Gemini, ChatGPT)
2. API key entry and validation
3. .env file configuration
4. promaia.config.json initialization
5. Optional Notion workspace configuration

Usage:
    maia setup              # Full interactive setup
    maia setup --check      # Verify current configuration
"""
import os
import shutil
import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import Application
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

from promaia.utils.env_writer import (
    get_config_path,
    get_config_template_path,
)


console = Console()


# ── Docker detection ─────────────────────────────────────────────────


def is_running_in_docker() -> bool:
    """Detect if running inside a Docker container."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r") as f:
            return "docker" in f.read()
    except (FileNotFoundError, PermissionError):
        pass
    return False


# ── Config file initialization ───────────────────────────────────────


def ensure_config_file() -> bool:
    """
    Copy promaia.config.template.json -> promaia.config.json if missing.
    Config goes into the data directory (maia-data/ or project root).
    Returns True if config file exists after this call.
    """
    config_path = get_config_path()
    template_path = get_config_template_path()

    if config_path.exists():
        return True

    if template_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, config_path)
        return True

    return False


# ── Main setup flow ──────────────────────────────────────────────────


def handle_setup(args):
    """Entry point for `maia setup`. Sync wrapper around async flow."""
    try:
        asyncio.run(_run_setup(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup interrupted.[/yellow]")


async def _run_setup(args):
    """Async setup flow."""
    check_only = getattr(args, "check", False)

    if check_only:
        await _check_config()
        return

    # Handle single-service setup: maia setup slack, maia setup notion, etc.
    service = getattr(args, "service", None)
    if service:
        await _run_single_service_setup(service)
        return

    _print_banner()

    in_docker = is_running_in_docker()
    if in_docker and getattr(args, "debug", False):
        console.print("[dim]Running inside Docker container[/dim]\n")

    from promaia.auth.registry import get_ai_integrations, get_integration
    from promaia.auth.flow import configure_credential

    # Step 1: Ensure config file exists
    if ensure_config_file():
        console.print("[green]OK[/green] promaia.config.json ready")
    else:
        console.print(
            "[yellow]Warning:[/yellow] promaia.config.template.json not found — "
            "skipped config file creation"
        )

    # Step 2: Name your workspace
    console.print()
    console.print("[bold]Name your Promaia workspace[/bold]\n")
    workspace_slug = _setup_workspace(console)

    # Step 3: AI provider
    console.print()
    integrations = get_ai_integrations()
    selected = await _select_provider(integrations)
    if selected:
        await _safe_step(configure_credential(selected, console), "AI provider")

    # Step 4: Connect Notion
    console.print()
    notion = get_integration("notion")
    notion_success = await _safe_step(configure_credential(notion, console), "Notion")

    # Copy Notion credentials to workspace if needed
    if notion_success and workspace_slug:
        _copy_notion_creds_to_workspace(notion, workspace_slug)

    # Step 5: Select Notion databases
    if workspace_slug:
        console.print()
        console.print("[bold]Select Notion databases to sync[/bold]\n")
        await _safe_step(
            _browse_notion_databases(workspace_slug, console),
            "database selection"
        )

    # Step 6: Connect Google
    console.print()
    google = get_integration("google")
    await _safe_step(configure_credential(google, console), "Google")

    # Step 7: Connect Slack
    console.print()
    slack_success = await _safe_step(
        _setup_slack(workspace_slug, console),
        "Slack"
    )

    # Step 8: Initial sync
    if workspace_slug:
        from promaia.config.databases import get_database_manager
        db_manager = get_database_manager()
        workspace_dbs = db_manager.get_workspace_databases(workspace_slug)
        if workspace_dbs:
            console.print()
            console.print("[bold]Syncing your data[/bold]\n")
            console.print(f"  [dim]Syncing {len(workspace_dbs)} source(s)...[/dim]")
            await _safe_step(_run_initial_sync(workspace_slug), "initial sync")

    # Step 9: Next steps
    console.print()
    from_installer = os.environ.get("PROMAIA_FROM_INSTALLER") == "1"
    maia_installed = os.environ.get("PROMAIA_MAIA_INSTALLED") == "1"
    _print_next_steps(from_installer, maia_installed)


async def _run_initial_sync(workspace):
    """Run an initial sync for all databases in the workspace."""
    from promaia.cli.database_commands import handle_database_sync

    class SyncArgs:
        def __init__(self):
            self.sources = []
            self.workspace = workspace
            self.browse = None
            self.limit = None

    await handle_database_sync(SyncArgs())


async def _run_single_service_setup(service):
    """Run setup for a single service by name."""
    from promaia.auth.registry import get_integration
    from promaia.auth.flow import configure_credential
    from promaia.config.workspaces import get_workspace_manager

    ensure_config_file()

    manager = get_workspace_manager()
    workspace = manager.get_default_workspace()
    if not workspace:
        console.print("[yellow]No workspace configured. Run 'maia setup' first.[/yellow]")
        return

    service = service.lower().strip()

    if service == "slack":
        await _setup_slack(workspace, console)
    elif service == "notion":
        notion = get_integration("notion")
        await configure_credential(notion, console)
        _copy_notion_creds_to_workspace(notion, workspace)
        console.print()
        console.print("[bold]Select Notion databases to sync[/bold]\n")
        await _browse_notion_databases(workspace, console)
    elif service == "google":
        google = get_integration("google")
        await configure_credential(google, console)
    elif service in ("llm", "ai", "openrouter", "anthropic"):
        from promaia.auth.registry import get_ai_integrations
        integrations = get_ai_integrations()
        selected = await _select_provider(integrations)
        if selected:
            await configure_credential(selected, console)
    else:
        console.print(f"[yellow]Unknown service: {service}[/yellow]")
        console.print("[dim]Available: slack, notion, google, llm[/dim]")


async def _safe_step(coro, name="step"):
    """Run an async step, catching interrupts and errors gracefully."""
    try:
        return await coro
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print(f"\n  [dim]{name} skipped[/dim]")
        return False
    except Exception as e:
        console.print(f"\n  [yellow]Warning:[/yellow] {name} failed: {e}")
        return False


def _setup_workspace(c=None):
    """Ask the user for a workspace name. Returns the slug or None."""
    import re
    from rich.prompt import Prompt
    from promaia.config.workspaces import get_workspace_manager

    c = c or console
    manager = get_workspace_manager()

    existing_names = list(manager.workspaces.keys())
    if existing_names:
        current = manager.default_workspace or existing_names[0]
        new_name = Prompt.ask("  Workspace name", default=current).strip().lower()
        new_name = re.sub(r"[^a-z0-9]+", "-", new_name).strip("-") or current

        if new_name != current:
            manager.add_workspace(new_name)
            manager.remove_workspace(current)
            manager.set_default_workspace(new_name)
            c.print(f"  [green]OK[/green] Renamed workspace to [bold]{new_name}[/bold]")
            return new_name
        else:
            c.print(f"  [green]OK[/green] Workspace [bold]{current}[/bold] ready")
            return current

    slug = Prompt.ask("  Workspace name", default="my-workspace").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "my-workspace"

    if manager.add_workspace(slug):
        c.print(f"  [green]OK[/green] Created workspace [bold]{slug}[/bold]")
        return slug
    return None


def _copy_notion_creds_to_workspace(notion_integration, workspace):
    """Copy global Notion credential to workspace-specific path if needed."""
    import shutil as _shutil
    global_token = notion_integration._token_path()
    ws_token = notion_integration._token_path(workspace)
    if global_token.exists() and not ws_token.exists():
        ws_token.parent.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(global_token, ws_token)


async def _setup_slack(workspace, c=None):
    """Set up Slack: create bot via manifest redirect, collect tokens, select channels."""
    import httpx
    from rich.prompt import Prompt
    from promaia.auth.registry import get_integration

    c = c or console

    slack = get_integration("slack")

    # Check existing credentials
    existing = slack.get_slack_credentials(workspace)
    if existing and existing.get("bot_token") and existing.get("app_token"):
        c.print("[bold]Slack[/bold]\n")
        c.print("  [dim]Validating existing connection...[/dim]")
        valid, msg = await slack.validate_credential(existing["bot_token"])
        if valid:
            c.print(f"  [green]OK[/green] {msg}")
            reconfigure = Prompt.ask("  Reconfigure?", choices=["y", "n"], default="n").strip().lower()
            if reconfigure != "y":
                # Still offer channel selection
                if workspace:
                    await _browse_slack_channels(workspace, existing["bot_token"], c)
                return True
        else:
            c.print(f"  [yellow]Warning:[/yellow] {msg}")
            c.print("  [dim]Reconfiguring...[/dim]\n")

    c.print("[bold]Connect Slack[/bold]\n")
    c.print("  Create your Slack bot:")
    c.print("  Visit: [link=https://oauth.promaia.workers.dev/slack]https://oauth.promaia.workers.dev/slack[/link]\n")

    # QR code
    try:
        from promaia.auth.flow import _render_qr
        _render_qr("https://oauth.promaia.workers.dev/slack", c)
    except Exception:
        pass

    c.print("  [dim]After creating the bot, copy the two tokens below.[/dim]\n")

    # Collect tokens
    bot_token = Prompt.ask("  Bot Token (xoxb-...)").strip()
    if not bot_token:
        c.print("  [dim]Skipped[/dim]")
        return False

    app_token = Prompt.ask("  App Token (xapp-...)").strip()
    if not app_token:
        c.print("  [dim]Skipped[/dim]")
        return False

    # Validate bot token
    c.print("  [dim]Validating...[/dim]")
    valid, msg = await slack.validate_credential(bot_token)
    if valid:
        c.print(f"  [green]OK[/green] {msg}")
    else:
        c.print(f"  [red]FAIL[/red] {msg}")
        save_anyway = Prompt.ask("  Save anyway?", choices=["y", "n"], default="n").strip().lower()
        if save_anyway != "y":
            return False

    # Validate app token format
    if not app_token.startswith("xapp-"):
        c.print("  [yellow]Warning:[/yellow] App token should start with xapp-")

    # Store credentials
    slack.store_credential(bot_token, app_token=app_token, workspace=workspace)
    c.print(f"  [green]OK[/green] Slack credentials saved")

    # Auto-enable the Slack service
    try:
        from promaia.utils.env_writer import get_data_dir
        import json as _json
        services_path = get_data_dir() / "services.json"
        if services_path.exists():
            svc = _json.loads(services_path.read_text())
        else:
            svc = {}
        svc["slack"] = {"enabled": True}
        services_path.write_text(_json.dumps(svc, indent=2) + "\n")
        c.print(f"  [green]OK[/green] Slack service enabled")
    except Exception:
        c.print(f"  [dim]Note: enable Slack with 'maia services enable slack'[/dim]")

    # Channel selection
    if workspace:
        await _browse_slack_channels(workspace, bot_token, c)

    return True


async def _browse_slack_channels(workspace, bot_token, c=None):
    """Browse Slack channels and let user select which to join/sync."""
    import httpx

    c = c or console
    c.print()
    c.print("[bold]Select Slack channels[/bold]\n")
    c.print("  [dim]Searching for channels...[/dim]")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://slack.com/api/conversations.list",
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"types": "public_channel,private_channel", "limit": 200},
            )
        data = resp.json()
        if not data.get("ok"):
            c.print(f"  [yellow]Could not list channels: {data.get('error')}[/yellow]")
            return
        channels = data.get("channels", [])
    except Exception as e:
        c.print(f"  [yellow]Could not connect to Slack: {e}[/yellow]")
        return

    if not channels:
        c.print("  [dim]No channels found[/dim]")
        return

    # Build list: (id, name, is_member)
    channel_list = []
    for ch in channels:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", "unknown")
        is_member = ch.get("is_member", False)
        channel_list.append((ch_id, ch_name, is_member))

    channel_list.sort(key=lambda x: x[1].lower())

    # Multi-select (reuse flat selector pattern)
    selected = await _multi_select_channels(channel_list, c)

    if not selected:
        c.print("  [dim]No channels selected[/dim]")
        return

    # Join selected channels and collect their IDs
    joined = 0
    selected_channel_ids = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for ch_id, ch_name, _is_member in selected:
            try:
                resp = await client.post(
                    "https://slack.com/api/conversations.join",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    json={"channel": ch_id},
                )
                if resp.json().get("ok"):
                    joined += 1
                    selected_channel_ids.append(ch_id)
                elif _is_member:
                    # Already a member, still count it
                    selected_channel_ids.append(ch_id)
            except Exception:
                pass

    c.print(f"  [green]OK[/green] Joined {joined} channel(s)")

    # Add Slack as a sync source in the database config
    if selected_channel_ids:
        import re
        from promaia.config.databases import get_database_manager

        db_manager = get_database_manager()

        # Get team ID for database_id
        team_id = "slack"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {bot_token}"},
                )
                data = resp.json()
                if data.get("ok"):
                    team_id = data.get("team_id", "slack")
        except Exception:
            pass

        # Check if a Slack database already exists for this workspace
        existing_slack = None
        for db_config in db_manager.get_workspace_databases(workspace):
            if db_config.source_type == "slack":
                existing_slack = db_config
                break

        if not existing_slack:
            config = {
                "source_type": "slack",
                "database_id": team_id,
                "description": "Slack channels",
                "workspace": workspace,
                "sync_enabled": True,
                "include_properties": True,
                "default_days": 7,
                "save_markdown": True,
                "channel_ids": selected_channel_ids,
            }
            db_manager.add_database("slack", config, workspace)
            c.print(f"  [green]OK[/green] Added Slack as sync source with {len(selected_channel_ids)} channel(s)")


async def _multi_select_channels(channels, c):
    """Multi-select for Slack channels. channels: list of (id, name, is_member)."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout

    selected = [ch[2] for ch in channels]  # Pre-select channels bot is already in
    current = [0]
    confirmed = False
    max_visible = 20

    def get_viewport_text():
        total = len(channels)
        cur = current[0]
        half = max_visible // 2
        if total <= max_visible:
            start = 0
        elif cur < half:
            start = 0
        elif cur >= total - half:
            start = max(0, total - max_visible)
        else:
            start = cur - half
        end = min(start + max_visible, total)

        lines = []
        if start > 0:
            lines.append("  ... more above")
        for i in range(start, end):
            check = "[x]" if selected[i] else "[ ]"
            arrow = " >" if i == cur else "  "
            member = " (joined)" if channels[i][2] else ""
            lines.append(f" {arrow} {check} #{channels[i][1]}{member}")
        if end < total:
            lines.append("  ... more below")
        return "\n".join(lines)

    def get_status():
        count = sum(selected)
        return f" SPACE toggle  ENTER confirm ({count} selected)  ESC skip"

    def make_layout():
        visible = min(len(channels), max_visible) + 3
        return Layout(HSplit([
            Window(FormattedTextControl(text=get_viewport_text), height=visible),
            Window(FormattedTextControl(text=get_status), height=1, style="fg:gray"),
        ]))

    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def up(event):
        if current[0] > 0:
            current[0] -= 1
            event.app.layout = make_layout()

    @bindings.add(Keys.Down)
    def down(event):
        if current[0] < len(channels) - 1:
            current[0] += 1
            event.app.layout = make_layout()

    @bindings.add(" ")
    def toggle(event):
        selected[current[0]] = not selected[current[0]]
        event.app.layout = make_layout()

    @bindings.add(Keys.Enter)
    def confirm_sel(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=make_layout(), key_bindings=bindings,
        full_screen=False, mouse_support=False,
    )
    await app.run_async()

    if confirmed:
        return [channels[i] for i in range(len(channels)) if selected[i]]
    return []


async def _browse_notion_databases(workspace, c=None):
    """Browse Notion databases and let user select which to sync."""
    import re
    import httpx
    from promaia.auth.registry import get_integration
    from promaia.config.databases import get_database_manager

    c = c or console

    # Get Notion token for this workspace
    notion = get_integration("notion")
    token = notion.get_notion_credentials(workspace)
    if not token:
        c.print("  [dim]No Notion credentials found — skipping database selection[/dim]")
        return

    # Search for databases — show top-level immediately, load nested on demand
    c.print("  [dim]Searching for databases...[/dim]")
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.notion.com/v1/search",
                headers=headers,
                json={"filter": {"value": "database", "property": "object"}},
            )
            if resp.status_code != 200:
                c.print(f"  [yellow]Could not search Notion databases (HTTP {resp.status_code})[/yellow]")
                return

            results = resp.json().get("results", [])
            if not results:
                c.print("  [dim]No databases found. Share databases with your Notion integration to see them here.[/dim]")
                return

            # Split into top-level and nested
            top_level = []
            nested_raw = []
            for db in results:
                db_id = db.get("id", "")
                title = "".join(t.get("plain_text", "") for t in db.get("title", [])).strip() or "Untitled"
                p = db.get("parent", {})
                if p.get("type") == "workspace":
                    top_level.append((db_id, title, ""))
                else:
                    nested_raw.append((db, db_id, title, p))

            nested_count = len(nested_raw)
    except Exception as e:
        c.print(f"  [yellow]Could not connect to Notion: {e}[/yellow]")
        return

    # Start with top-level databases only
    all_databases = list(top_level)

    # Check which are already added
    db_manager = get_database_manager()
    existing_ids = set()
    for db_config in db_manager.get_workspace_databases(workspace):
        existing_ids.add(db_config.database_id)

    # Filter and select from top-level first
    new_databases = [(db_id, title, group) for db_id, title, group in all_databases if db_id not in existing_ids]
    already_added = len(all_databases) - len(new_databases)

    if already_added > 0:
        c.print(f"  [dim]{already_added} database(s) already configured[/dim]")

    if not new_databases and nested_count == 0:
        c.print("  [green]OK[/green] All available databases are already configured")
        return

    all_selected = []

    if new_databases:
        new_databases.sort(key=lambda x: x[1].lower())
        if nested_count > 0:
            c.print(f"  [dim]Showing {len(new_databases)} top-level databases ({nested_count} more in sub-pages)[/dim]")

        selected = await _multi_select_flat(new_databases, c)
        if selected:
            all_selected.extend(selected)

    # Offer to load nested databases
    if nested_count > 0:
        from rich.prompt import Prompt
        show_more = Prompt.ask(
            f"\n  Show {nested_count} more databases from sub-pages?",
            choices=["y", "n"], default="n"
        ).strip().lower()

        if show_more == "y":
            c.print("  [dim]Loading nested databases...[/dim]")
            try:
                async with httpx.AsyncClient(timeout=60.0) as client2:
                    parent_names = {}
                    parent_page_ids = set()
                    for _db, _db_id, _title, p in nested_raw:
                        if p.get("type") == "page_id":
                            parent_page_ids.add(p["page_id"])

                    for pid in parent_page_ids:
                        try:
                            pr = await client2.get(f"https://api.notion.com/v1/pages/{pid}", headers=headers)
                            if pr.status_code == 200:
                                for _pname, pval in pr.json().get("properties", {}).items():
                                    if pval.get("type") == "title":
                                        parts = pval.get("title", [])
                                        parent_names[pid] = "".join(t.get("plain_text", "") for t in parts).strip() or ""
                                        break
                        except Exception:
                            pass

                nested_databases = []
                for _db, db_id, title, p in nested_raw:
                    if db_id in existing_ids:
                        continue
                    if p.get("type") == "page_id":
                        group = parent_names.get(p["page_id"], "(other)")
                    else:
                        group = "(other)"
                    nested_databases.append((db_id, title, group))

                if nested_databases:
                    nested_databases.sort(key=lambda x: (x[2].lower(), x[1].lower()))
                    selected2 = await _multi_select_flat(nested_databases, c)
                    if selected2:
                        all_selected.extend(selected2)
                else:
                    c.print("  [dim]All nested databases already configured[/dim]")
            except Exception as e:
                c.print(f"  [yellow]Could not load nested databases: {e}[/yellow]")

    if not all_selected:
        c.print("  [dim]No databases selected[/dim]")
        return

    # Add selected databases
    added = 0
    for db_id, title, _group in all_selected:
        name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "untitled"
        config = {
            "source_type": "notion",
            "database_id": db_id,
            "description": title,
            "workspace": workspace,
            "sync_enabled": True,
            "include_properties": True,
            "default_days": 7,
            "save_markdown": True,
        }
        if db_manager.add_database(name, config, workspace):
            added += 1

    c.print(f"  [green]OK[/green] Added {added} database(s) to workspace [bold]{workspace}[/bold]")


async def _multi_select_flat(databases, c):
    """Multi-select with flat top-level items + collapsible groups.

    Items with group="" are shown flat at the top (top-level databases).
    Items with a group name are shown in collapsible sections below.

    Args:
        databases: list of (id, title, group) tuples, sorted flat-first
    Returns:
        list of selected (id, title, group) tuples
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout

    selected = [False] * len(databases)
    confirmed = False
    max_visible = 20

    # Separate flat items from grouped items
    flat_indices = [i for i, (_, _, g) in enumerate(databases) if g == ""]
    groups = []  # (group_name, [db_indices])
    group_map = {}
    for i, (db_id, title, group) in enumerate(databases):
        if group == "":
            continue
        if group not in group_map:
            group_map[group] = len(groups)
            groups.append((group, []))
        groups[group_map[group]][1].append(i)

    # All groups start collapsed
    expanded = {g: False for g, _ in groups}

    def build_nav_items():
        """Returns list of (type, value)."""
        items = []
        # Flat items first
        for idx in flat_indices:
            items.append(("db", idx))
        # Then groups
        if groups and flat_indices:
            items.append(("separator", None))
        for group_name, db_indices in groups:
            items.append(("group", group_name))
            if expanded.get(group_name, False):
                for idx in db_indices:
                    items.append(("db", idx))
        return items

    nav_items = build_nav_items()
    current = [0]

    def get_viewport_text():
        items = nav_items
        total = len(items)
        cur = current[0]

        half = max_visible // 2
        if total <= max_visible:
            start = 0
        elif cur < half:
            start = 0
        elif cur >= total - half:
            start = max(0, total - max_visible)
        else:
            start = cur - half
        end = min(start + max_visible, total)

        lines = []
        if start > 0:
            lines.append("  ... more above")

        for i in range(start, end):
            item_type, value = items[i]
            is_cur = (i == cur)
            arrow = " >" if is_cur else "  "

            if item_type == "separator":
                lines.append("")
            elif item_type == "group":
                icon = "v" if expanded.get(value, False) else ">"
                count = len(groups[group_map[value]][1])
                sel_count = sum(1 for idx in groups[group_map[value]][1] if selected[idx])
                sel_info = f" ({sel_count}/{count})" if sel_count > 0 else f" ({count})"
                lines.append(f" {arrow} {icon} {value}{sel_info}")
            else:
                db_idx = value
                check = "[x]" if selected[db_idx] else "[ ]"
                lines.append(f" {arrow} {check} {databases[db_idx][1]}")

        if end < total:
            lines.append("  ... more below")

        return "\n".join(lines)

    def get_status():
        count = sum(selected)
        return " SPACE select  RIGHT/LEFT expand/collapse  ENTER confirm ({} selected)  ESC skip".format(count)

    def make_layout():
        visible = min(len(nav_items), max_visible) + 3
        viewport = Window(
            FormattedTextControl(text=get_viewport_text),
            height=visible,
        )
        status = Window(
            FormattedTextControl(text=get_status), height=1, style="fg:gray"
        )
        return Layout(HSplit([viewport, status]))

    bindings = KeyBindings()

    def _skip_separators(direction):
        """Move cursor past separator items."""
        while 0 <= current[0] < len(nav_items) and nav_items[current[0]][0] == "separator":
            current[0] += direction

    @bindings.add(Keys.Up)
    def up(event):
        if current[0] > 0:
            current[0] -= 1
            _skip_separators(-1)
            event.app.layout = make_layout()

    @bindings.add(Keys.Down)
    def down(event):
        if current[0] < len(nav_items) - 1:
            current[0] += 1
            _skip_separators(1)
            event.app.layout = make_layout()

    @bindings.add(Keys.Right)
    def expand_group(event):
        nonlocal nav_items
        if current[0] >= len(nav_items):
            return
        item_type, value = nav_items[current[0]]
        if item_type == "group":
            expanded[value] = not expanded[value]
            nav_items = build_nav_items()
            for i, (t, v) in enumerate(nav_items):
                if t == "group" and v == value:
                    current[0] = i
                    break
            event.app.layout = make_layout()

    @bindings.add(Keys.Left)
    def collapse_group(event):
        nonlocal nav_items
        if current[0] >= len(nav_items):
            return
        item_type, value = nav_items[current[0]]
        if item_type == "group" and expanded.get(value, False):
            expanded[value] = False
            nav_items = build_nav_items()
            event.app.layout = make_layout()
        elif item_type == "db":
            db_group = databases[value][2]
            if db_group and db_group in expanded:
                expanded[db_group] = False
                nav_items = build_nav_items()
                for i, (t, v) in enumerate(nav_items):
                    if t == "group" and v == db_group:
                        current[0] = i
                        break
                event.app.layout = make_layout()

    @bindings.add(" ")
    def toggle(event):
        nonlocal nav_items
        if current[0] >= len(nav_items):
            return
        item_type, value = nav_items[current[0]]
        if item_type == "db":
            selected[value] = not selected[value]
        elif item_type == "group":
            db_indices = groups[group_map[value]][1]
            all_selected = all(selected[i] for i in db_indices)
            for i in db_indices:
                selected[i] = not all_selected
            if not all_selected and not expanded.get(value, False):
                expanded[value] = True
                nav_items = build_nav_items()
                for i, (t, v) in enumerate(nav_items):
                    if t == "group" and v == value:
                        current[0] = i
                        break
        event.app.layout = make_layout()

    @bindings.add(Keys.Enter)
    def confirm_sel(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=make_layout(),
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()

    if confirmed:
        return [databases[i] for i in range(len(databases)) if selected[i]]
    return []


def _print_banner():
    banner = Panel(
        "[bold magenta]🐙 Promaia Setup[/bold magenta]\n"
        "[dim]Configure your AI provider and API key[/dim]",
        border_style="magenta",
        padding=(1, 2),
    )
    console.print(banner)
    console.print()


async def _select_provider(integrations):
    """Arrow-key provider selector. Returns chosen Integration or None."""
    current_focus = 0
    confirmed = False

    def get_entry_display(index: int) -> str:
        p = integrations[index]
        indicator = "\u2192" if index == current_focus else " "
        tag = "  (recommended)" if p.recommended else ""
        return f" {indicator}  {p.display_name}{tag}"

    def get_status_display():
        return " \u2191\u2193 Navigate   ENTER Select   ESC Cancel"

    def create_layout():
        status_window = Window(
            FormattedTextControl(text=get_status_display), height=1,
            style="fg:gray",
        )
        title_window = Window(
            FormattedTextControl(text=" Select your AI provider:"), height=1
        )
        entry_windows = [
            Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
            )
            for i in range(len(integrations))
        ]
        return Layout(HSplit([
            title_window,
            Window(height=1),
            *entry_windows,
            Window(height=1),
            status_window,
        ]))

    layout = create_layout()
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(integrations) - 1:
            current_focus += 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()

    if confirmed:
        selected = integrations[current_focus]
        console.print(f"  [magenta]{selected.display_name}[/magenta]\n")
        return selected
    return None


async def _confirm(prompt: str, default_yes: bool = False) -> bool:
    """Arrow-key Yes/No selector. Returns True for Yes, False for No."""
    options = ["Yes", "No"]
    current_focus = 0 if default_yes else 1
    confirmed = False

    def get_entry_display(index: int) -> str:
        indicator = "\u2192" if index == current_focus else " "
        return f" {indicator}  {options[index]}"

    def get_status_display():
        return " \u2191\u2193 Navigate   ENTER Select"

    def create_layout():
        prompt_window = Window(
            FormattedTextControl(text=f" {prompt}"), height=1
        )
        entry_windows = [
            Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
            )
            for i in range(len(options))
        ]
        status_window = Window(
            FormattedTextControl(text=get_status_display), height=1,
            style="fg:gray",
        )
        return Layout(HSplit([
            prompt_window,
            Window(height=1),
            *entry_windows,
            Window(height=1),
            status_window,
        ]))

    layout = create_layout()
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(options) - 1:
            current_focus += 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()

    result = confirmed and current_focus == 0
    console.print(f"  [dim]{'Yes' if result else 'No'}[/dim]\n")
    return result


async def _check_config():
    """Non-destructive config check: maia setup --check"""
    console.print("[bold]Configuration Status[/bold]\n")

    from promaia.auth.registry import list_integrations

    table = Table(show_header=True)
    table.add_column("Integration", style="magenta")
    table.add_column("Status")

    for integration in list_integrations():
        cred = integration.get_default_credential()
        if cred:
            masked = (
                cred[:8] + "..." + cred[-4:]
                if len(cred) > 12
                else "***"
            )
            table.add_row(
                integration.display_name,
                f"[green]Set[/green] ({masked})",
            )
        else:
            table.add_row(integration.display_name, "[dim]Not set[/dim]")

    console.print(table)

    config_path = get_config_path()
    if config_path.exists():
        console.print(f"\n[green]OK[/green] {config_path}")
    else:
        console.print(f"\n[yellow]Missing[/yellow] {config_path}")
        console.print("[dim]Run 'maia setup' to create it[/dim]")


def _print_next_steps(from_installer: bool = False, maia_installed: bool = False):
    """Print success message and quick-start commands."""
    if from_installer and not maia_installed:
        cmds = (
            "  docker compose run --rm maia chat\n"
            "  docker compose up -d       [dim]# start web API + scheduler[/dim]\n"
            "  docker compose run --rm maia --help"
        )
        reconfigure = (
            "  docker compose run --rm maia setup\n"
            "  docker compose run --rm maia setup --check"
        )
    else:
        cmds = "  maia chat\n  maia --help"
        reconfigure = "  maia setup\n  maia setup --check"

    panel = Panel(
        "[bold green]Setup complete![/bold green]\n\n"
        f"Quick start:\n{cmds}\n\n"
        f"Reconfigure anytime:\n{reconfigure}",
        title="[bold]Ready[/bold]",
        border_style="magenta",
        padding=(1, 2),
    )
    console.print(panel)


# ── Argparse registration ────────────────────────────────────────────


def add_setup_commands(subparsers):
    """Register 'maia setup' command with argparse."""
    setup_parser = subparsers.add_parser(
        "setup",
        help="Interactive setup wizard — configure AI provider and API key",
    )
    setup_parser.add_argument(
        "service",
        nargs="?",
        default=None,
        help="Run setup for a specific service (slack, notion, google, llm)",
    )
    setup_parser.add_argument(
        "--check",
        action="store_true",
        help="Check current configuration status without changing anything",
    )
    setup_parser.set_defaults(func=handle_setup)
