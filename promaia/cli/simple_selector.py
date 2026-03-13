"""Simple selector for databases and Discord channels for removal operations."""

import asyncio
import os
import logging
from typing import List, Optional, Dict, Any, Tuple
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

logger = logging.getLogger(__name__)

def launch_simple_selector(workspace: Optional[str], show_type: str = "both", title: str = "Select Items") -> List[str]:
    """
    Launch simple selector for databases and Discord channels.
    
    Args:
        workspace: Workspace to show items from
        show_type: What to show - "databases", "channels", or "both" 
        title: Title for the selector
        
    Returns:
        List of selected items (database names or channel specs)
    """
    return asyncio.run(interactive_simple_selector(workspace, show_type, title))

async def interactive_simple_selector(workspace: Optional[str], show_type: str = "both", title: str = "Select Items") -> List[str]:
    """Interactive simple selector with checkbox-style selection."""
    from promaia.config.databases import get_database_manager
    import sys
    
    console = Console()
    
    # Check if we're in a proper terminal
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print("❌ Interactive selection not available (not running in a terminal).", style="red")
        console.print("This command requires a terminal to display the interactive selector.", style="dim")
        return []
    
    try:
        db_manager = get_database_manager()
        
        if workspace is None:
            console.print(f"❌ No workspace specified", style="red")
            return []
                
        workspace_databases = db_manager.get_workspace_databases(workspace)
            
        if not workspace_databases:
            console.print(f"❌ No databases found in workspace '{workspace}'", style="red")
            return []
        
        # Build entries for databases and Discord channels
        all_entries = []
        
        for db in workspace_databases:
            qualified_name = db.get_qualified_name()
            
            if show_type in ["databases", "both"]:
                # Add regular database entry
                if db.source_type != "discord":
                    all_entries.append({
                        'name': qualified_name,
                        'display': f"{qualified_name} ({db.source_type})",
                        'type': 'database',
                        'database': qualified_name
                    })
            
            if show_type in ["channels", "both"] and db.source_type == "discord":
                # For Discord databases, load available channels
                channels = get_synced_channels_from_filesystem(db)
                
                if channels:
                    for channel in channels:
                        channel_name = channel['name']
                        channel_spec = f"{qualified_name}#{channel_name}"
                        message_count = channel.get('message_count', 0)
                        
                        all_entries.append({
                            'name': channel_spec,
                            'display': f"{qualified_name}#{channel_name} ({message_count} messages)",
                            'type': 'discord_channel',
                            'database': qualified_name,
                            'channel': channel_name
                        })
                else:
                    # Show Discord database even if no channels
                    if show_type in ["databases", "both"]:
                        all_entries.append({
                            'name': qualified_name,
                            'display': f"{qualified_name} (discord, no channels)",
                            'type': 'database',
                            'database': qualified_name
                        })
        
        if not all_entries:
            console.print(f"❌ No items found in workspace '{workspace}' for type '{show_type}'", style="red")
            return []
        
        # Sort entries: regular databases first, then Discord channels, alphabetically within each group
        def sort_key(entry):
            type_priority = {'database': 0, 'discord_channel': 1}
            primary = type_priority.get(entry['type'], 2)
            secondary = entry['database']
            tertiary = entry.get('channel', "")
            return (primary, secondary, tertiary)
        
        all_entries.sort(key=sort_key)
        
        # State management
        enabled_states = [False for _ in all_entries]  # Start with nothing selected
        current_focus = 0
        should_exit = False
        confirmed = False
        
        def get_status_display():
            """Generate status line."""
            enabled_count = sum(enabled_states)
            total_count = len(enabled_states)
            db_count = sum(1 for entry in all_entries if entry['type'] == 'database')
            channel_count = total_count - db_count
            
            type_info = ""
            if show_type == "databases":
                type_info = f"Databases: {total_count}"
            elif show_type == "channels":
                type_info = f"Channels: {total_count}"
            else:
                type_info = f"Items: {db_count} databases, {channel_count} channels"
                
            return f"🔍 {workspace} | {type_info} | Selected: {enabled_count}/{total_count} | ↑↓ Navigate SPACE Toggle ENTER Confirm ESC Cancel"
        
        def get_entry_display(index: int) -> str:
            """Get display text for an entry."""
            checkbox = "☑" if enabled_states[index] else "☐"
            return f"{checkbox}       {all_entries[index]['display']}"
        
        # Create display windows
        def create_layout():
            # Status line
            status_window = Window(
                FormattedTextControl(text=get_status_display),
                height=1,
            )
            
            # Title line
            title_window = Window(
                FormattedTextControl(text=title),
                height=1,
                style="class:title"
            )
            
            # Entry windows
            entry_windows = []
            current_group = None
            
            for i, entry in enumerate(all_entries):
                # Add group header if this is a new group
                entry_group = 'databases' if entry['type'] == 'database' else 'channels'
                if current_group != entry_group:
                    if current_group is not None:
                        # Add spacing between groups
                        entry_windows.append(Window(height=1))
                    
                    if entry_group == 'databases':
                        header_text = "📄 Databases:"
                    else:
                        header_text = "💬 Discord Channels:"
                    
                    header_window = Window(
                        FormattedTextControl(text=header_text),
                        height=1,
                        style="class:header"
                    )
                    entry_windows.append(header_window)
                    current_group = entry_group
                
                # Create entry window
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
                layout = create_layout()  # Recreate layout to update selection highlighting
                event.app.layout = layout
        
        @bindings.add(Keys.Down)
        def move_down(event):
            nonlocal current_focus
            if current_focus < len(all_entries) - 1:
                current_focus += 1
                layout = create_layout()  # Recreate layout to update selection highlighting
                event.app.layout = layout
        
        @bindings.add(' ')  # Spacebar
        def toggle_selection(event):
            nonlocal current_focus
            enabled_states[current_focus] = not enabled_states[current_focus]
            layout = create_layout()  # Recreate layout to update checkboxes
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
            # Return selected items
            result_items = []
            for i, enabled in enumerate(enabled_states):
                if enabled:
                    result_items.append(all_entries[i]['name'])
            return result_items
        else:
            return []
            
    except Exception as e:
        console.print(f"❌ Error in simple selector: {e}", style="red")
        import traceback
        traceback.print_exc()
        return []


def get_synced_channels_from_filesystem(db_config) -> List[Dict]:
    """Get list of channels that have already been synced by checking filesystem."""
    channels = []
    
    try:
        from pathlib import Path
        
        # Check the markdown directory for this Discord database
        md_dir = db_config.markdown_directory
        
        if os.path.exists(md_dir):
            # Each subdirectory represents a synced channel
            for channel_dir in Path(md_dir).iterdir():
                if channel_dir.is_dir() and not channel_dir.name.startswith('.'):
                    # Count messages in this channel
                    message_files = list(channel_dir.glob("*.md"))
                    last_sync = "unknown"
                    
                    if message_files:
                        # Get the most recent message file for last activity
                        newest_file = max(message_files, key=lambda f: f.stat().st_mtime)
                        last_sync = newest_file.stat().st_mtime
                        import datetime
                        last_sync = datetime.datetime.fromtimestamp(last_sync).strftime("%m/%d %H:%M")
                    
                    channels.append({
                        "id": "unknown",  # We don't need the ID for removal
                        "name": channel_dir.name,  # Use directory name as-is
                        "message_count": len(message_files),
                        "last_activity": last_sync
                    })
    
    except Exception as e:
        logger.error(f"Error reading synced channels from filesystem: {e}")
    
    return channels


def launch_database_selector(workspace: Optional[str] = None) -> List[str]:
    """Launch selector specifically for databases."""
    return launch_simple_selector(workspace, "databases", "Select Databases to Remove")


def launch_channel_selector(workspace: Optional[str] = None) -> List[str]:
    """Launch selector specifically for Discord channels.""" 
    return launch_simple_selector(workspace, "channels", "Select Discord Channels to Remove")


def launch_combined_selector(workspace: Optional[str] = None) -> List[str]:
    """Launch selector for both databases and channels."""
    return launch_simple_selector(workspace, "both", "Select Items to Remove")