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
    asyncio.run(_run_setup(args))


async def _run_setup(args):
    """Async setup flow."""
    check_only = getattr(args, "check", False)

    if check_only:
        await _check_config()
        return

    _print_banner()

    in_docker = is_running_in_docker()
    if in_docker and getattr(args, "debug", False):
        console.print("[dim]Running inside Docker container[/dim]\n")

    from promaia.auth.registry import get_ai_integrations, get_integration
    from promaia.auth.flow import configure_credential

    # Step 1: AI provider selection
    integrations = get_ai_integrations()
    selected = await _select_provider(integrations)
    if selected is None:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        return

    # Step 2: Configure the selected AI provider
    success = await configure_credential(selected, console)
    if not success:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        return

    # Step 3: Ensure config file exists (needed before workspace creation)
    if ensure_config_file():
        console.print("[green]OK[/green] promaia.config.json ready")
    else:
        console.print(
            "[yellow]Warning:[/yellow] promaia.config.template.json not found — "
            "skipped config file creation"
        )

    # Step 4: Connect services
    console.print()
    console.print("[bold]Connect your services[/bold]\n")

    notion = get_integration("notion")
    notion_success = await configure_credential(notion, console)

    console.print()
    google = get_integration("google")
    await configure_credential(google, console)

    # Step 5: Set up workspace
    workspace_slug = None
    if notion_success:
        console.print()
        console.print("[bold]Setting up your workspace[/bold]\n")
        workspace_slug = _auto_create_workspace(notion, console)

    # Step 6: Select Notion databases
    if workspace_slug and notion_success:
        console.print()
        console.print("[bold]Select Notion databases to sync[/bold]\n")
        await _browse_notion_databases(workspace_slug, console)

    # Step 7: Next steps
    console.print()
    from_installer = os.environ.get("PROMAIA_FROM_INSTALLER") == "1"
    maia_installed = os.environ.get("PROMAIA_MAIA_INSTALLED") == "1"
    _print_next_steps(from_installer, maia_installed)


def _auto_create_workspace(notion_integration, c=None):
    """Create or confirm a workspace, letting the user name it.

    Returns the workspace slug (str) or None.
    """
    import re
    import shutil as _shutil
    from rich.prompt import Prompt
    from promaia.config.workspaces import get_workspace_manager

    c = c or console

    # Suggest a name from the Notion workspace
    raw_name = getattr(notion_integration, "_last_validated_name", None)
    default_slug = re.sub(r"[^a-z0-9]+", "-", (raw_name or "default").lower()).strip("-") or "default"

    manager = get_workspace_manager()

    # If a workspace already exists, show it and offer to rename
    existing_names = list(manager.workspaces.keys())
    if existing_names:
        current = manager.default_workspace or existing_names[0]
        new_name = Prompt.ask(
            f"  Workspace name", default=current
        ).strip().lower()
        new_name = re.sub(r"[^a-z0-9]+", "-", new_name).strip("-") or current

        if new_name != current:
            # Rename: create new, move credentials, remove old
            manager.add_workspace(new_name)
            old_token = notion_integration._token_path(current)
            new_token = notion_integration._token_path(new_name)
            if old_token.exists() and not new_token.exists():
                new_token.parent.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(old_token, new_token)
            manager.remove_workspace(current)
            manager.set_default_workspace(new_name)
            c.print(f"  [green]OK[/green] Renamed workspace to [bold]{new_name}[/bold]")
            return new_name
        else:
            c.print(f"  [green]OK[/green] Workspace [bold]{current}[/bold] ready")
            return current

    # First time: ask for name with Notion workspace as suggestion
    slug = Prompt.ask(
        "  Workspace name", default=default_slug
    ).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or default_slug

    if manager.add_workspace(slug):
        # Copy global Notion credential to workspace-specific path
        global_token = notion_integration._token_path()
        ws_token = notion_integration._token_path(slug)
        if global_token.exists() and not ws_token.exists():
            ws_token.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(global_token, ws_token)

        c.print(f"  [green]OK[/green] Created workspace [bold]{slug}[/bold] (set as default)")
        return slug
    return None


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

    # Search for all databases the bot has access to
    c.print("  [dim]Searching for databases...[/dim]")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.notion.com/v1/search",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json={"filter": {"value": "database", "property": "object"}},
            )
        if resp.status_code != 200:
            c.print(f"  [yellow]Could not search Notion databases (HTTP {resp.status_code})[/yellow]")
            return

        results = resp.json().get("results", [])
    except Exception as e:
        c.print(f"  [yellow]Could not connect to Notion: {e}[/yellow]")
        return

    if not results:
        c.print("  [dim]No databases found. Share databases with your Notion integration to see them here.[/dim]")
        return

    # Build list of (id, title) from results
    databases = []
    for db in results:
        db_id = db.get("id", "")
        title_parts = db.get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts).strip()
        if not title:
            title = "Untitled"
        databases.append((db_id, title))

    # Check which are already added
    db_manager = get_database_manager()
    existing_ids = set()
    for db_config in db_manager.get_workspace_databases(workspace):
        existing_ids.add(db_config.database_id)

    # Filter out already-added databases
    new_databases = [(db_id, title) for db_id, title in databases if db_id not in existing_ids]
    already_added = len(databases) - len(new_databases)

    if already_added > 0:
        c.print(f"  [dim]{already_added} database(s) already configured[/dim]")

    if not new_databases:
        c.print("  [green]OK[/green] All available databases are already configured")
        return

    # Multi-select UI using prompt_toolkit
    selected = await _multi_select_databases(new_databases, c)

    if not selected:
        c.print("  [dim]No databases selected[/dim]")
        return

    # Add selected databases
    added = 0
    for db_id, title in selected:
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


async def _multi_select_databases(databases, c):
    """Multi-select checkbox UI for database selection.

    Args:
        databases: list of (id, title) tuples
    Returns:
        list of selected (id, title) tuples
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout

    selected = [False] * len(databases)
    current = 0
    confirmed = False

    def get_entry(i):
        check = "[x]" if selected[i] else "[ ]"
        arrow = " >" if i == current else "  "
        return f" {arrow} {check} {databases[i][1]}"

    def get_status():
        count = sum(selected)
        return f" SPACE toggle  ENTER confirm ({count} selected)  ESC skip"

    def make_layout():
        entries = [
            Window(
                FormattedTextControl(text=lambda i=i: get_entry(i)),
                height=1,
            )
            for i in range(len(databases))
        ]
        status = Window(
            FormattedTextControl(text=get_status), height=1, style="fg:gray"
        )
        return Layout(HSplit([*entries, Window(height=1), status]))

    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def up(event):
        nonlocal current
        if current > 0:
            current -= 1
            event.app.layout = make_layout()

    @bindings.add(Keys.Down)
    def down(event):
        nonlocal current
        if current < len(databases) - 1:
            current += 1
            event.app.layout = make_layout()

    @bindings.add(" ")
    def toggle(event):
        selected[current] = not selected[current]
        event.app.layout = make_layout()

    @bindings.add(Keys.Enter)
    def confirm(event):
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
        "--check",
        action="store_true",
        help="Check current configuration status without changing anything",
    )
    setup_parser.set_defaults(func=handle_setup)
