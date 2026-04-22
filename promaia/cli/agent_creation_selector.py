"""Interactive selectors for agent creation flow."""

import asyncio
import os
import logging
import subprocess
import tempfile
from typing import List, Optional, Tuple, Dict, Any
from pathlib import Path
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import Application
from prompt_toolkit.layout.containers import HSplit, Window, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.widgets import TextArea, Frame
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)


# ============================================================================
# Discord Channel Enrichment
# ============================================================================

async def fetch_discord_channels(
    workspace: str,
    db_configs: List[Dict],
    preselected_names: Optional[set] = None,
) -> List[Dict]:
    """
    Enrich Discord databases with channel information.

    Args:
        workspace: Workspace name for auth context
        db_configs: List of database configurations
        preselected_names: Optional set of "server#channel_id" names that
            should appear pre-selected (from agent edit flow)

    Returns:
        Enriched database configs with channels array for Discord sources
    """
    import json
    from pathlib import Path

    enriched = []

    # Load Discord credentials for this workspace
    from promaia.utils.env_writer import get_data_dir
    credentials_file = get_data_dir() / "credentials" / workspace / "discord_credentials.json"
    bot_token = None

    if credentials_file.exists():
        try:
            with open(credentials_file, 'r') as f:
                creds_data = json.load(f)
                bot_token = creds_data.get('bot_token')
        except Exception as e:
            logger.warning(f"Could not load Discord credentials: {e}")

    for db in db_configs:
        if db.get('source_type') != 'discord':
            enriched.append(db)
            continue

        # Skip if no bot token available
        if not bot_token:
            logger.warning(f"No Discord credentials found for workspace {workspace}")
            enriched.append(db)
            continue

        # Fetch channels from Discord API
        try:
            from promaia.connectors.discord_connector import DiscordConnector
            from promaia.config.databases import get_database_manager

            # Get full database config to get database_id (server_id)
            db_manager = get_database_manager()
            db_name = db.get('name')
            full_db_config = None

            # Find the database configuration
            for workspace_db in db_manager.get_workspace_databases(workspace):
                if workspace_db.get_qualified_name() == db_name:
                    full_db_config = workspace_db
                    break

            if not full_db_config:
                logger.warning(f"Could not find full config for {db_name}")
                enriched.append(db)
                continue

            # Create connector config
            connector_config = {
                'source_type': 'discord',
                'database_id': full_db_config.database_id,  # This is the server_id
                'workspace': workspace,
                'bot_token': bot_token
            }

            connector = DiscordConnector(connector_config)
            await connector.connect()

            # Get guild data with channels
            guild_data = await connector._get_guild_data()

            if guild_data and guild_data.get('channels'):
                # Add channels to database entry (already filtered to text-only in connector)
                db_name = db.get('name')
                db_with_channels = {
                    **db,
                    'channels': [
                        {
                            'id': ch['id'],
                            'name': ch['name'],
                            'days': db.get('default_days', 7),
                            'selected': bool(
                                preselected_names
                                and f"{db_name}#{ch['id']}" in preselected_names
                            ),
                        }
                        for ch in guild_data.get('channels', [])
                    ]
                }
                enriched.append(db_with_channels)
            else:
                # Add without channels - user can select server-wide
                enriched.append(db)

        except Exception as e:
            logger.warning(f"Could not fetch channels for {db.get('name')}: {e}")
            # Add without channels - user can select server-wide
            enriched.append(db)

    return enriched


# ============================================================================
# Styling Utilities
# ============================================================================

def _styled_header(text: str) -> str:
    """Create a styled header for selectors."""
    return f"🤖 {text}"


def _styled_status_line(selected_count: int, total: int, instructions: str) -> str:
    """Create status line for selectors."""
    return f"Selected: {selected_count}/{total} | {instructions}"


def _format_database_entry(name: str, days: str, enabled: bool) -> str:
    """Format a database entry for display."""
    checkbox = "☑" if enabled else "☐"
    days_display = f"[{days}]" if days != "all" else "[all]"
    return f"{checkbox}  {name} {days_display}"


def _get_entry_prefix(enabled: bool) -> str:
    """Get checkbox prefix for an entry."""
    checkbox = "☑" if enabled else "☐"
    return f"{checkbox}      "


# ============================================================================
# Workspace Selector
# ============================================================================

async def select_workspace(workspaces: List[str], preselected: str = None) -> Optional[str]:
    """
    Interactive workspace selector.

    Args:
        workspaces: List of available workspace names
        preselected: Pre-selected workspace (auto-select if only one)

    Returns:
        Selected workspace name or None if cancelled
    """
    console = Console()

    # If only one workspace, auto-select it
    if len(workspaces) == 1:
        selected = workspaces[0]
        console.print(f"✓ Workspace: [cyan]{selected}[/cyan] (auto-selected)", style="dim")
        return selected

    # State management
    current_focus = 0
    if preselected and preselected in workspaces:
        current_focus = workspaces.index(preselected)

    should_exit = False
    confirmed = False

    def get_status_display():
        return f"🔍 Select Workspace | ↑↓:Navigate ENTER:Select ESC:Cancel"

    def get_entry_display(index: int) -> str:
        """Get display text for a workspace entry."""
        indicator = "→" if index == current_focus else " "
        return f"{indicator}  {workspaces[index]}"

    # Create display windows
    def create_layout():
        # Status line
        status_window = Window(
            FormattedTextControl(text=get_status_display),
            height=1,
        )

        # Title line
        title_window = Window(
            FormattedTextControl(text=_styled_header("Select Workspace")),
            height=1,
            style="class:title"
        )

        # Entry windows
        entry_windows = []
        for i in range(len(workspaces)):
            entry_window = Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
                style=f"class:{'selected' if i == current_focus else 'unselected'}"
            )
            entry_windows.append(entry_window)

        # Main container
        container = HSplit([
            status_window,
            title_window,
            Window(height=1),  # Spacer
            *entry_windows
        ])

        return Layout(container)

    layout = create_layout()

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(workspaces) - 1:
            current_focus += 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal should_exit, confirmed
        should_exit = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        nonlocal should_exit
        should_exit = True
        event.app.exit()

    # Create application
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    # Run the application
    await app.run_async()

    if confirmed:
        return workspaces[current_focus]
    else:
        return None


# ============================================================================
# Database Selector with Inline Day Editing
# ============================================================================

async def select_databases(
    workspace: str,
    available_databases: List[Dict[str, Any]]
) -> Optional[List[str]]:
    """
    Interactive database selector with checkboxes and Discord channel hierarchy.

    Databases are access permissions — the agent can query these sources
    via query tools at runtime. No content is pre-loaded.

    Args:
        workspace: Workspace name
        available_databases: List of database configs with 'name' and optional 'channels'

    Returns:
        List of database names like ["journal", "gmail", "discord_server#channel_id"] or None if cancelled
    """
    console = Console()

    if not available_databases:
        console.print("❌ No databases available", style="red")
        return None

    # Expand Discord databases with channels into flat list
    entries = []  # List of {'name': str, 'display_name': str, 'days': int, 'is_channel': bool, 'parent_idx': int, 'channel_id': str}

    for db in available_databases:
        db_name = db['name']
        default_days = db.get('default_days', 7)
        channels = db.get('channels', [])

        if channels:
            # Discord server with channels - add parent entry
            parent_idx = len(entries)
            entries.append({
                'name': db_name,
                'display_name': db_name,
                'days': default_days,
                'is_channel': False,
                'is_parent': True,
                'parent_idx': None,
                'channel_id': None,
                'default_include': db.get('default_include', True)
            })

            # Add channel entries
            for channel in channels:
                entries.append({
                    'name': f"{db_name}#{channel['id']}",  # Format: server#channel_id
                    'display_name': f"  #{channel['name']}",  # Indented with #
                    'days': channel.get('days', default_days),
                    'is_channel': True,
                    'is_parent': False,
                    'parent_idx': parent_idx,
                    'channel_id': channel['id'],
                    'default_include': channel.get('selected', False),
                })
        else:
            # Regular database (no channels)
            entries.append({
                'name': db_name,
                'display_name': db_name,
                'days': default_days,
                'is_channel': False,
                'is_parent': False,
                'parent_idx': None,
                'channel_id': None,
                'default_include': db.get('default_include', True)
            })

    # Create focusable widgets and state for each entry
    # (TextArea kept as a minimal focusable widget — prompt_toolkit requires
    # a focusable control for key bindings to work with up/down navigation)
    text_areas = []
    enabled_states = []
    source_windows = []

    for idx, entry in enumerate(entries):
        # Minimal focusable widget (hidden, just for focus tracking)
        text_area = TextArea(
            text="",
            height=1,
            multiline=False,
            wrap_lines=False,
            scrollbar=False,
            focusable=True,
        )
        text_areas.append(text_area)

        # Start with default_include
        is_enabled = entry.get('default_include', True)
        enabled_states.append(is_enabled)

        # Create window for this entry — just checkbox + name
        prefix_text = _get_entry_prefix(is_enabled)
        display_name = entry['display_name']

        source_window = VSplit([
            Window(
                FormattedTextControl(text=prefix_text),
                width=8,
                dont_extend_width=True,
            ),
            Window(
                FormattedTextControl(text=display_name),
                width=len(display_name) + 1,
                dont_extend_width=True,
            ),
            text_area,  # hidden focusable for navigation
        ])
        source_windows.append(source_window)

    # State management
    current_focus = 0
    should_exit = False
    confirmed = False

    def get_status_display():
        """Generate status line."""
        enabled_count = sum(enabled_states)
        total_count = len(enabled_states)
        return f"🔍 {workspace} | Selected: {enabled_count}/{total_count} | ↑↓:Navigate SPACE:Toggle ENTER:Confirm ESC:Cancel"

    # Status line
    status_window = Window(
        FormattedTextControl(text=get_status_display),
        height=1,
    )

    # Title line
    title_window = Window(
        FormattedTextControl(text=_styled_header("Select Databases")),
        height=1,
        style="class:title"
    )

    # Main container
    container = HSplit([
        status_window,
        title_window,
        Window(height=1),  # Spacer
        *source_windows
    ])

    layout = Layout(container)

    # Set initial focus
    layout.focus(text_areas[current_focus])

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            layout.focus(text_areas[current_focus])

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(text_areas) - 1:
            current_focus += 1
            layout.focus(text_areas[current_focus])

    @bindings.add(' ')  # Spacebar
    def toggle_database(event):
        nonlocal current_focus
        entry = entries[current_focus]

        # Toggle current entry
        enabled_states[current_focus] = not enabled_states[current_focus]

        # If this is a parent with channels, toggle all children too
        if entry.get('is_parent'):
            new_state = enabled_states[current_focus]
            for idx, child_entry in enumerate(entries):
                if child_entry.get('parent_idx') == current_focus:
                    enabled_states[idx] = new_state
                    # Update child prefix display
                    prefix_text = _get_entry_prefix(enabled_states[idx])
                    source_windows[idx].children[0].content.text = prefix_text

        # If this is a channel, update parent state
        if entry.get('is_channel') and entry.get('parent_idx') is not None:
            parent_idx = entry['parent_idx']
            # Check if all children are selected or none are selected
            children_indices = [i for i, e in enumerate(entries) if e.get('parent_idx') == parent_idx]
            all_selected = all(enabled_states[i] for i in children_indices)
            none_selected = not any(enabled_states[i] for i in children_indices)

            # Update parent checkbox (use ⊟ for partial selection)
            if all_selected:
                enabled_states[parent_idx] = True
                prefix_text = _get_entry_prefix(True)
            elif none_selected:
                enabled_states[parent_idx] = False
                prefix_text = _get_entry_prefix(False)
            else:
                # Partial selection - show intermediate state
                prefix_text = "⊟      "

            source_windows[parent_idx].children[0].content.text = prefix_text

        # Update current prefix display
        prefix_text = _get_entry_prefix(enabled_states[current_focus])
        source_windows[current_focus].children[0].content.text = prefix_text

        # Update status
        status_window.content.text = get_status_display

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal should_exit, confirmed
        should_exit = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        nonlocal should_exit
        should_exit = True
        event.app.exit()

    # Create application
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    # Run the application
    await app.run_async()

    if confirmed:
        # Return enabled database names (access permissions)
        result = []
        for i, (enabled, entry) in enumerate(zip(enabled_states, entries)):
            # Skip parent entries if they have channels (only include actual channels)
            if entry.get('is_parent'):
                continue

            if enabled:
                # Use entry['name'] which includes channel ID for Discord: "server#channel_id"
                result.append(entry['name'])

        if not result:
            console.print("❌ No databases selected", style="red")
            return None

        return result
    else:
        return None


# ============================================================================
# Prompt Input Selector with File Browser
# ============================================================================

async def input_prompt() -> Optional[str]:
    """
    Interactive prompt input with file browser and editor support.

    Returns:
        Prompt content as string or None if cancelled
    """
    console = Console()

    # Ensure agent_prompts directory exists
    from promaia.utils.env_writer import get_agent_prompts_dir
    prompts_dir = get_agent_prompts_dir()
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Get list of .md files
    md_files = sorted(prompts_dir.glob("*.md"))

    if not md_files:
        console.print("📝 No prompt files found. Creating a new one...", style="cyan")
        return await _create_new_prompt_file(prompts_dir)

    # File browser with preview
    file_list = [f.name for f in md_files]

    # State management
    current_focus = 0
    should_exit = False
    confirmed = False
    action = None  # 'select', 'new', 'edit', 'notion'

    def get_preview_content() -> str:
        """Get preview of currently selected file."""
        if current_focus < len(md_files):
            try:
                content = md_files[current_focus].read_text()
                # Limit preview to first 500 characters
                if len(content) > 500:
                    return content[:500] + "\n\n... (truncated)"
                return content
            except Exception as e:
                return f"Error reading file: {e}"
        return ""

    def get_status_display():
        return "↑↓:Navigate N:New E:Edit P:Notion ENTER:Select ESC:Cancel"

    def get_file_display(index: int) -> str:
        """Get display text for a file entry."""
        indicator = "→" if index == current_focus else " "
        return f"{indicator}  {file_list[index]}"

    # Create display windows
    def create_layout():
        # Status line
        status_window = Window(
            FormattedTextControl(text=get_status_display),
            height=1,
        )

        # Title line
        title_window = Window(
            FormattedTextControl(text=_styled_header("Select Prompt File")),
            height=1,
            style="class:title"
        )

        # File list
        file_windows = []
        for i in range(len(file_list)):
            file_window = Window(
                FormattedTextControl(text=lambda i=i: get_file_display(i)),
                height=1,
                style=f"class:{'selected' if i == current_focus else 'unselected'}"
            )
            file_windows.append(file_window)

        file_list_container = HSplit(file_windows)

        # Preview pane
        preview_text = get_preview_content()
        preview_window = Window(
            FormattedTextControl(text=preview_text),
            wrap_lines=True,
        )

        # Split layout: file list (left) + preview (right)
        content_container = VSplit([
            HSplit([
                Window(FormattedTextControl(text="Files:"), height=1, style="class:header"),
                file_list_container,
            ], width=30),
            Window(width=1),  # Separator
            HSplit([
                Window(FormattedTextControl(text="Preview:"), height=1, style="class:header"),
                preview_window,
            ]),
        ])

        # Main container
        container = HSplit([
            status_window,
            title_window,
            Window(height=1),  # Spacer
            content_container,
        ])

        return Layout(container)

    layout = create_layout()

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(file_list) - 1:
            current_focus += 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add('n')
    @bindings.add('N')
    def create_new(event):
        nonlocal should_exit, action
        should_exit = True
        action = 'new'
        event.app.exit()

    @bindings.add('e')
    @bindings.add('E')
    def edit_file(event):
        nonlocal should_exit, action
        should_exit = True
        action = 'edit'
        event.app.exit()

    @bindings.add('p')
    @bindings.add('P')
    def paste_notion_link(event):
        nonlocal should_exit, action
        should_exit = True
        action = 'notion'
        event.app.exit()

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal should_exit, confirmed
        should_exit = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        nonlocal should_exit
        should_exit = True
        event.app.exit()

    # Create application
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    # Run the application
    await app.run_async()

    if confirmed:
        # Read and return selected file content
        try:
            content = md_files[current_focus].read_text()
            return content
        except Exception as e:
            console.print(f"❌ Error reading file: {e}", style="red")
            return None
    elif action == 'new':
        return await _create_new_prompt_file(prompts_dir)
    elif action == 'edit':
        # Edit the selected file and return its content
        file_path = md_files[current_focus]
        if await _edit_file_in_editor(file_path):
            try:
                content = file_path.read_text()
                return content
            except Exception as e:
                console.print(f"❌ Error reading edited file: {e}", style="red")
                return None
        return None
    elif action == 'notion':
        # Create prompt from Notion URL
        return await _create_notion_prompt(prompts_dir)
    else:
        return None


async def _create_new_prompt_file(prompts_dir: Path) -> Optional[str]:
    """Create a new prompt file using the user's editor."""
    console = Console()

    # Prompt for filename
    console.print("\nEnter filename for new prompt (without .md extension):")
    filename = input("Filename: ").strip()

    if not filename:
        console.print("❌ Filename is required", style="red")
        return None

    # Add .md extension if not present
    if not filename.endswith('.md'):
        filename += '.md'

    file_path = prompts_dir / filename

    # Check if file already exists
    if file_path.exists():
        console.print(f"❌ File '{filename}' already exists", style="red")
        return None

    # Create initial content
    initial_content = "# Agent Prompt\n\nDescribe what the agent should do...\n"
    file_path.write_text(initial_content)

    # Open in editor
    if await _edit_file_in_editor(file_path):
        try:
            content = file_path.read_text()
            if content.strip():
                console.print(f"✓ Created prompt file: {filename}", style="green")
                return content
            else:
                console.print("❌ Prompt file is empty", style="red")
                file_path.unlink()  # Remove empty file
                return None
        except Exception as e:
            console.print(f"❌ Error reading new file: {e}", style="red")
            return None
    else:
        console.print("❌ Editor was closed without saving", style="red")
        file_path.unlink()  # Remove file if editing was cancelled
        return None


async def _edit_file_in_editor(file_path: Path) -> bool:
    """Open a file in the user's preferred editor."""
    console = Console()

    # Determine editor
    editor = os.environ.get('EDITOR') or os.environ.get('VISUAL') or 'nano'

    console.print(f"Opening in {editor}...", style="dim")

    try:
        # Open editor and wait for completion
        subprocess.run([editor, str(file_path)], check=True)
        return True
    except subprocess.CalledProcessError:
        console.print(f"❌ Error opening editor: {editor}", style="red")
        return False
    except FileNotFoundError:
        console.print(f"❌ Editor not found: {editor}", style="red")
        console.print("Set EDITOR environment variable to your preferred editor", style="dim")
        return False


async def _create_notion_prompt(prompts_dir: Path) -> Optional[str]:
    """Create a new prompt file from a Notion page URL."""
    from promaia.cli.notion_prompt_manager import create_notion_prompt, get_prompt_content
    console = Console()

    console.print("\n📄 Create Prompt from Notion Page", style="cyan")
    console.print("Paste the Notion page URL:")

    notion_url = input("URL: ").strip()

    if not notion_url:
        console.print("❌ URL is required", style="red")
        return None

    # Prompt for filename
    console.print("\nEnter filename for this prompt (without .md extension):")
    console.print("(Press ENTER to auto-generate from page ID)")
    filename = input("Filename: ").strip()

    # Show progress message
    console.print("\n⏳ Fetching Notion page...", style="cyan")

    # Create the Notion-backed prompt
    try:
        prompt_file = await create_notion_prompt(
            notion_url=notion_url,
            filename=filename if filename else None,
            prompts_dir=prompts_dir
        )

        if not prompt_file:
            console.print("❌ Failed to create prompt from Notion page", style="red")
            return None

        console.print(f"✅ Created Notion-backed prompt: {prompt_file.name}", style="green")
        console.print("   This prompt will stay synced with the Notion page", style="dim")

        # Return the prompt content (without metadata)
        return get_prompt_content(prompt_file)

    except Exception as e:
        console.print(f"❌ Error creating Notion prompt: {e}", style="red")
        import traceback
        traceback.print_exc()
        return None


# ============================================================================
# Interval Selector
# ============================================================================

async def select_interval() -> Optional[int]:
    """
    Interactive interval selector with common presets.

    Returns:
        Interval in minutes or None if cancelled
    """
    console = Console()

    # Common intervals
    intervals = [
        (5, "⚡ Very Frequent (5 minutes)"),
        (15, "🔄 Frequent (15 minutes)"),
        (30, "⏰ Regular (30 minutes)"),
        (60, "📅 Hourly (60 minutes)"),
        (None, "✏️  Custom (enter manually)"),
    ]

    # State management
    current_focus = 2  # Default to 30 minutes
    should_exit = False
    confirmed = False

    def get_status_display():
        return "↑↓:Navigate ENTER:Select ESC:Cancel"

    def get_entry_display(index: int) -> str:
        """Get display text for an interval entry."""
        indicator = "→" if index == current_focus else " "
        return f"{indicator}  {intervals[index][1]}"

    # Create display windows
    def create_layout():
        # Status line
        status_window = Window(
            FormattedTextControl(text=get_status_display),
            height=1,
        )

        # Title line
        title_window = Window(
            FormattedTextControl(text=_styled_header("Select Run Interval")),
            height=1,
            style="class:title"
        )

        # Entry windows
        entry_windows = []
        for i in range(len(intervals)):
            entry_window = Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
                style=f"class:{'selected' if i == current_focus else 'unselected'}"
            )
            entry_windows.append(entry_window)

        # Main container
        container = HSplit([
            status_window,
            title_window,
            Window(height=1),  # Spacer
            *entry_windows
        ])

        return Layout(container)

    layout = create_layout()

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(intervals) - 1:
            current_focus += 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal should_exit, confirmed
        should_exit = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        nonlocal should_exit
        should_exit = True
        event.app.exit()

    # Create application
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    # Run the application
    await app.run_async()

    if confirmed:
        selected_interval = intervals[current_focus][0]

        if selected_interval is None:
            # Custom interval
            console.print("\nEnter custom interval in minutes:")
            try:
                custom_value = input("Minutes: ").strip()
                interval_minutes = int(custom_value)
                if interval_minutes <= 0:
                    console.print("❌ Interval must be positive", style="red")
                    return None
                return interval_minutes
            except ValueError:
                console.print("❌ Invalid interval", style="red")
                return None
        else:
            return selected_interval
    else:
        return None


# ============================================================================
# Notion Page Selector
# ============================================================================

async def select_notion_page(workspace: str) -> Optional[str]:
    """
    Interactive Notion page selector with search.

    Args:
        workspace: Workspace name to query pages from

    Returns:
        Page ID or None if cancelled
    """
    console = Console()

    # For now, we'll use manual input mode as fetching all Notion pages
    # might be expensive. We can add the browser mode later.
    console.print("\n📄 Enter Notion page ID", style="cyan")
    console.print("(This is where agent results will be written)", style="dim")

    page_id = input("Page ID: ").strip()

    if not page_id:
        console.print("❌ Page ID is required", style="red")
        return None

    return page_id


# ============================================================================
# MCP Tools Selector
# ============================================================================

async def select_mcp_tools(available_tools: List[str], preselected: Optional[List[str]] = None) -> List[str]:
    """
    Interactive MCP tools selector.

    Args:
        available_tools: List of available MCP tool names

    Returns:
        List of selected tool names (can be empty)
    """
    console = Console()

    if not available_tools:
        return []

    # State management
    preselected = preselected or []
    enabled_states = [(t in preselected) for t in available_tools]
    current_focus = 0
    should_exit = False
    confirmed = False

    def get_status_display():
        """Generate status line."""
        enabled_count = sum(enabled_states)
        total_count = len(enabled_states)
        return f"MCP Tools | Selected: {enabled_count}/{total_count} | ↑↓:Navigate SPACE:Toggle ENTER:Confirm ESC:Cancel"

    def get_entry_display(index: int) -> str:
        """Get display text for an entry."""
        checkbox = "☑" if enabled_states[index] else "☐"
        return f"{checkbox}  {available_tools[index]}"

    # Create display windows
    def create_layout():
        # Status line
        status_window = Window(
            FormattedTextControl(text=get_status_display),
            height=1,
        )

        # Title line
        title_window = Window(
            FormattedTextControl(text=_styled_header("Select MCP Tools (Optional)")),
            height=1,
            style="class:title"
        )

        # Entry windows
        entry_windows = []
        for i in range(len(available_tools)):
            entry_window = Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
                style=f"class:{'selected' if i == current_focus else 'unselected'}"
            )
            entry_windows.append(entry_window)

        # Main container
        container = HSplit([
            status_window,
            title_window,
            Window(height=1),  # Spacer
            *entry_windows
        ])

        return Layout(container)

    layout = create_layout()

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(available_tools) - 1:
            current_focus += 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(' ')  # Spacebar
    def toggle_selection(event):
        nonlocal current_focus
        enabled_states[current_focus] = not enabled_states[current_focus]
        layout = create_layout()
        event.app.layout = layout

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal should_exit, confirmed
        should_exit = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        nonlocal should_exit
        should_exit = True
        event.app.exit()

    # Create application
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    # Run the application
    await app.run_async()

    if confirmed:
        # Return selected tools
        result_tools = []
        for i, enabled in enumerate(enabled_states):
            if enabled:
                result_tools.append(available_tools[i])
        return result_tools
    else:
        return []


# ============================================================================
# Per-Server Sub-Tool Selector (for granular tool permissions)
# ============================================================================

async def select_server_tools(
    server_name: str,
    tools: List[Tuple[str, str]],
    preselected: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """Interactive checkbox selector for which tools on *server_name* are enabled.

    Args:
        server_name: Display name (e.g. "promaia").
        tools: List of (tool_name, description) tuples.
        preselected: Optional list of tool names to start checked. If None,
            all tools are checked by default.

    Returns:
        - List of selected tool names. Empty list means "block all tools on
          this server".
        - None if the user cancelled (caller should treat as 'no change').
    """
    console = Console()
    if not tools:
        return None

    preselected_set = set(preselected) if preselected is not None else {t for t, _ in tools}
    enabled_states = [(name in preselected_set) for name, _ in tools]
    current_focus = 0
    confirmed = False

    def _status_line() -> str:
        n = sum(enabled_states)
        total = len(enabled_states)
        return (
            f"{server_name} | Enabled: {n}/{total} | "
            f"↑↓:Navigate SPACE:Toggle a:All n:None ENTER:Confirm ESC:Skip"
        )

    def _entry_display(i: int) -> str:
        checkbox = "☑" if enabled_states[i] else "☐"
        name, desc = tools[i]
        return f"{checkbox}  {name}  —  {desc}"

    def _build_layout():
        status_window = Window(
            FormattedTextControl(text=_status_line),
            height=1,
        )
        title_window = Window(
            FormattedTextControl(text=_styled_header(f"Tools for '{server_name}'")),
            height=1,
            style="class:title",
        )
        entry_windows = []
        for i in range(len(tools)):
            entry_windows.append(
                Window(
                    FormattedTextControl(text=lambda i=i: _entry_display(i)),
                    height=1,
                    style=f"class:{'selected' if i == current_focus else 'unselected'}",
                )
            )
        return Layout(HSplit([status_window, title_window, Window(height=1), *entry_windows]))

    layout = _build_layout()
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def _up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            event.app.layout = _build_layout()

    @bindings.add(Keys.Down)
    def _down(event):
        nonlocal current_focus
        if current_focus < len(tools) - 1:
            current_focus += 1
            event.app.layout = _build_layout()

    @bindings.add(' ')
    def _toggle(event):
        enabled_states[current_focus] = not enabled_states[current_focus]
        event.app.layout = _build_layout()

    @bindings.add('a')
    def _all(event):
        for i in range(len(enabled_states)):
            enabled_states[i] = True
        event.app.layout = _build_layout()

    @bindings.add('n')
    def _none(event):
        for i in range(len(enabled_states)):
            enabled_states[i] = False
        event.app.layout = _build_layout()

    @bindings.add(Keys.Enter)
    def _confirm(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def _cancel(event):
        event.app.exit()

    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()

    if not confirmed:
        return None

    return [name for (name, _), on in zip(tools, enabled_states) if on]
