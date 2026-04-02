"""
Database management commands for the enhanced Maia CLI.
"""
import asyncio
import argparse
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from promaia.utils.timezone_utils import days_ago_utc, now_local, now_utc, log_timezone_info
import json
import os
import re
from pathlib import Path

from promaia.config.databases import get_database_manager, get_database_config
from promaia.config.paths import get_project_root
from promaia.connectors import ConnectorRegistry
from promaia.connectors.base import QueryFilter, DateRangeFilter

logger = logging.getLogger(__name__)


def _get_discord_bot_token(workspace: str) -> Optional[str]:
    """Get Discord bot token for a workspace via the auth module."""
    from promaia.auth import get_integration
    return get_integration("discord").get_discord_token(workspace)


# Helper for better input handling with keyboard shortcuts
async def prompt_input(text: str, default: str = "") -> str:
    """Enhanced input with proper keyboard shortcut support (Option+Delete, etc.)."""
    try:
        from prompt_toolkit import PromptSession
        session = PromptSession()
        result = await session.prompt_async(text, default=default)
        return result
    except ImportError:
        # Fallback to regular input if prompt_toolkit not available
        return input(text)


async def checkbox_selector(title: str, items: list, item_formatter=None) -> list:
    """
    Interactive checkbox selector using prompt_toolkit.
    
    Args:
        title: Title to display at top
        items: List of items (dicts or any objects)
        item_formatter: Optional function to format each item for display
                       Takes (index, item) and returns a string
    
    Returns:
        List of selected items
    
    Controls:
        ↑/↓: Navigate
        SPACE: Toggle selection
        ENTER: Confirm
        ESC: Cancel
        a: Select all
        n: Select none
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window, VSplit
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    
    if not items:
        return []
    
    # Default formatter
    if item_formatter is None:
        item_formatter = lambda idx, item: f"{idx + 1}. {str(item)}"
    
    # State
    selected_states = [False] * len(items)
    current_focus = 0
    confirmed = False
    
    def get_item_display(idx):
        checkbox = "[✓]" if selected_states[idx] else "[ ]"
        item_text = item_formatter(idx, items[idx])
        style = "reverse" if idx == current_focus else ""
        return [(style, f"  {checkbox} {item_text}")]
    
    def get_status():
        selected_count = sum(selected_states)
        return f"  {title} | Selected: {selected_count}/{len(items)} | ↑↓ Navigate  SPACE Toggle  ENTER Confirm  ESC Cancel  A All  N None"
    
    # Build windows for each item
    item_windows = [
        Window(
            FormattedTextControl(lambda idx=i: get_item_display(idx)),
            height=1
        )
        for i in range(len(items))
    ]
    
    status_window = Window(
        FormattedTextControl(get_status),
        height=1
    )
    
    container = HSplit([
        status_window,
        Window(height=1),  # Spacer
        *item_windows
    ])
    
    layout = Layout(container)
    bindings = KeyBindings()
    
    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
    
    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(items) - 1:
            current_focus += 1
    
    @bindings.add(' ')
    def toggle(event):
        selected_states[current_focus] = not selected_states[current_focus]
    
    @bindings.add('a')
    def select_all(event):
        for i in range(len(selected_states)):
            selected_states[i] = True
    
    @bindings.add('n')
    def select_none(event):
        for i in range(len(selected_states)):
            selected_states[i] = False
    
    @bindings.add(Keys.Enter)
    def confirm(event):
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
        return [items[i] for i, selected in enumerate(selected_states) if selected]
    return []

async def _select_database_interactive(source_types: Optional[List[str]] = None) -> Optional[str]:
    """Show an interactive single-select list of databases and return the chosen qualified name.

    Navigate with arrow keys, press ENTER to confirm the highlighted item, ESC to cancel.

    Args:
        source_types: If provided, only show databases whose source_type is in this list.

    Returns:
        The qualified database name (e.g. "koii.slack-general"), or None if cancelled.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    db_manager = get_database_manager()
    all_databases = db_manager.list_databases()

    # Build list of (qualified_name, db_config) pairs, optionally filtered
    candidates = []
    for qualified_name in all_databases:
        db_config = db_manager.databases.get(qualified_name)
        if db_config is None:
            continue
        if source_types and db_config.source_type not in source_types:
            continue
        candidates.append((qualified_name, db_config))

    if not candidates:
        filter_msg = f" (types: {', '.join(source_types)})" if source_types else ""
        print(f"No databases found{filter_msg}")
        return None

    current_focus = 0
    confirmed = False

    def format_line(idx):
        qname, cfg = candidates[idx]
        status = "✓" if cfg.sync_enabled else "✗"
        text = f"  {status} {qname} ({cfg.source_type}) - {cfg.description}"
        style = "reverse" if idx == current_focus else ""
        return [(style, text)]

    def get_status():
        return f"  Select a database ({len(candidates)}) | ↑↓ Navigate  ENTER Select  ESC Cancel"

    item_windows = [
        Window(FormattedTextControl(lambda idx=i: format_line(idx)), height=1)
        for i in range(len(candidates))
    ]

    container = HSplit([
        Window(FormattedTextControl(get_status), height=1),
        Window(height=1),
        *item_windows,
    ])

    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(candidates) - 1:
            current_focus += 1

    @bindings.add(Keys.Enter)
    def confirm(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=Layout(container),
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    await app.run_async()

    if confirmed:
        return candidates[current_focus][0]
    return None


async def remove_channel_from_config(db_config, channel_name: str, db_manager) -> bool:
    """
    Remove a Discord channel from database configuration by mapping channel name to ID.
    
    Args:
        db_config: Database configuration object
        channel_name: Name of the channel to remove
        db_manager: Database manager instance
        
    Returns:
        True if channel was found and removed, False otherwise
    """
    try:
        # Check if this database has channel_id filters
        if not hasattr(db_config, 'property_filters') or 'channel_id' not in db_config.property_filters:
            return True  # No channel filters to update
            
        channel_ids = db_config.property_filters.get('channel_id', [])
        if not channel_ids:
            return True  # No channel IDs configured
            
        # Try to map channel name to channel ID using existing data
        channel_id_to_remove = None
        
        # Look through registry entries to find a mapping
        from promaia.storage.hybrid_storage import get_hybrid_registry
        registry = get_hybrid_registry()
        
        try:
            # Get content for this database
            content_items = registry.list_content(
                workspace=db_config.workspace,
                database_name=db_config.get_qualified_name()
            )
            
            # Look for items from this channel to extract channel ID
            for item in content_items:
                metadata = item.get('metadata', {})
                item_channel_name = metadata.get('channel_name') or metadata.get('discord_channel_name')
                
                if item_channel_name == channel_name:
                    # Try to extract channel ID from metadata or file path
                    channel_id = metadata.get('channel_id') or metadata.get('discord_channel_id')
                    if channel_id and channel_id in channel_ids:
                        channel_id_to_remove = channel_id
                        break
                        
        except Exception as e:
            logger.debug(f"Could not find channel ID mapping through registry: {e}")
        
        # If we found a channel ID to remove, update the config
        if channel_id_to_remove:
            updated_channel_ids = [cid for cid in channel_ids if cid != channel_id_to_remove]
            db_config.property_filters['channel_id'] = updated_channel_ids
            db_manager.save_database_field(db_config, "property_filters")
            logger.info(f"Removed channel ID {channel_id_to_remove} from database {db_config.get_qualified_name()}")
            return True
        else:
            logger.warning(f"Could not find channel ID for channel name '{channel_name}' in database {db_config.get_qualified_name()}")
            return False
            
    except Exception as e:
        logger.error(f"Error removing channel from config: {e}")
        return False

async def handle_database_list(args):
    """Handle 'maia database list' command."""
    db_manager = get_database_manager()
    
    # Check if workspace filter is specified
    workspace_filter = getattr(args, 'workspace', None)
    
    if workspace_filter:
        databases = db_manager.list_databases(workspace_filter)
        print(f"Databases in workspace '{workspace_filter}':")
    else:
        databases = db_manager.list_databases()
        # Group by workspace for better display
        workspace_databases = db_manager.list_databases_by_workspace()
        
        if not workspace_databases:
            print("No databases configured.")
            print("Add a database with: maia database add <name> --source-type notion --database-id <id>")
            return
        
        print("Configured databases by workspace:")
        for workspace, db_names in workspace_databases.items():
            print(f"\n  Workspace: {workspace}")
            for db_name in db_names:
                db_config = db_manager.get_database(db_name)
                status = "✓" if db_config.sync_enabled else "✗"
                print(f"    {status} {db_config.get_qualified_name()} ({db_config.source_type}) - {db_config.description}")
        return
    
    if not databases:
        print(f"No databases configured in workspace '{workspace_filter}'.")
        return
    
    for db_name in databases:
        db_config = db_manager.get_database(db_name)
        status = "✓" if db_config.sync_enabled else "✗"
        print(f"  {status} {db_config.get_qualified_name()} ({db_config.source_type}) - {db_config.description}")

async def _add_discord_channels_interactive(db_config, workspace, db_name):
    """Helper function to interactively add Discord channels to a database."""
    from promaia.cli.discord_commands import interactive_channel_browser, get_accessible_channels_cached
    from rich.console import Console

    console = Console()
    db_manager = get_database_manager()

    print(f"🔍 Loading available channels for Discord server...")

    bot_token = _get_discord_bot_token(workspace)
    if not bot_token:
        print(f"✗ No Discord bot token configured for workspace '{workspace}'")
        print(f"💡 To configure Discord:")
        print(f"   1. Run: maia workspace discord-setup {workspace}")
        print(f"   2. Then run: maia database channel add {db_name}")
        return

    # Get all available channels for this server
    servers = []
    try:
        channels, server_name = await get_accessible_channels_cached(db_config, bot_token)
        if channels:
            servers = [{
                "server_id": db_config.database_id,
                "server_name": server_name,
                "db_name": db_config.nickname,
                "channels": channels
            }]
    except Exception as e:
        print(f"⚠️  Could not fetch channels: {e}")
        print(f"💡 You may need to run 'maia discord refresh' first")
        print(f"💡 Or add channels later with: maia database channel add {db_name}")
        return

    if not servers or not servers[0]["channels"]:
        print(f"✗ No accessible channels found")
        print(f"💡 Try running 'maia discord refresh' to update the channel cache")
        return

    print(f"📋 Select channels to sync:")
    print("   Use SPACE to select channels, ENTER to confirm, ESC to cancel")

    # Use the interactive browser
    selected_channels, _ = await interactive_channel_browser(console, servers, workspace)

    if not selected_channels:
        print("No channels selected.")
        return

    # Extract channel IDs from selection
    channels_to_add = [channel[1] for channel in selected_channels]  # channel[1] is channel_id

    print(f"\n➕ Adding {len(channels_to_add)} channels to database '{db_name}':")
    for channel in selected_channels:
        print(f"   - {channel[2]} ({channel[1]})")  # channel[2] is channel_name

    # Merge with existing channels (avoid duplicates)
    current_channel_ids = []
    channel_filter = db_config.property_filters.get('channel_id', [])
    if isinstance(channel_filter, str):
        current_channel_ids = [channel_filter]
    elif isinstance(channel_filter, list):
        current_channel_ids = list(channel_filter)

    all_channel_ids = list(dict.fromkeys(current_channel_ids + channels_to_add))
    db_config.property_filters['channel_id'] = all_channel_ids

    # Persist channel ID → name mapping
    channel_names_map = dict(db_config.property_filters.get('channel_names', {}))
    for channel in selected_channels:
        channel_names_map[channel[1]] = channel[2]  # channel[1]=id, channel[2]=name
    db_config.property_filters['channel_names'] = channel_names_map

    # Ask which channels should use OCR processing for images
    if len(selected_channels) > 0:
        print(f"\n📷 Which channels should use OCR for images (e.g. handwritten notes)?")
        print("   These channels will download images and run them through the OCR pipeline.")
        print("   Other channels will sync text as normal.\n")
        ocr_selection = []
        for channel in selected_channels:
            ch_name = channel[2]
            ch_id = channel[1]
            try:
                answer = (await prompt_input(f"   Enable OCR for #{ch_name}? (y/N): ")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = 'n'
            if answer in ('y', 'yes'):
                ocr_selection.append(ch_id)
        if ocr_selection:
            db_config.property_filters['ocr_channels'] = ocr_selection
            print(f"✓ OCR enabled for {len(ocr_selection)} channel(s)")

    db_manager.save_database_field(db_config, "property_filters")

    print(f"✅ Successfully added {len(channels_to_add)} channels")

    # Offer to sync now
    try:
        answer = (await prompt_input(f"Sync now? [Y/n] ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = 'n'

    if answer in ('', 'y', 'yes'):
        class SyncArgs:
            def __init__(self):
                self.sources = [db_name]
                self.workspace = None
                self.browse = None
                self.limit = None
        await handle_database_sync(SyncArgs())
    else:
        print(f"💡 Run 'maia database sync {db_name}' to sync the channels")

async def _add_slack_channels_interactive(db_config, workspace, db_name):
    """Helper function to interactively add Slack channels to a database."""
    import os
    from rich.console import Console
    from rich.table import Table

    console = Console()
    db_manager = get_database_manager()

    print(f"🔍 Loading available channels for Slack workspace...")

    # Get Slack bot token from environment
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    
    if not bot_token:
        print(f"✗ No Slack bot token found in environment")
        print(f"💡 Add SLACK_BOT_TOKEN to your .env file and restart terminal")
        return

    try:
        from promaia.connectors.slack_connector import SlackConnector
        
        connector_config = db_config.to_dict()
        connector_config['bot_token'] = bot_token
        
        connector = SlackConnector(connector_config)
        await connector.connect()
        
        channels_data = await connector.discover_accessible_channels()
        channels = channels_data.get("channels", [])

        # Cache the channel list so the workspace browser can resolve IDs to names
        try:
            from pathlib import Path
            from promaia.utils.env_writer import get_cache_dir
            cache_dir = get_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"slack_channels_{db_config.workspace}_{db_config.database_id}.json"
            cache_file.write_text(json.dumps(channels_data, indent=2))
        except Exception as e:
            logger.debug(f"Could not cache Slack channels: {e}")

        if not channels:
            print(f"✗ No accessible channels found")
            return
        
        # Use interactive checkbox selector
        print(f"\n📋 Found {len(channels)} Slack channels")
        print("   Use ↑↓ to navigate, SPACE to select, ENTER to confirm, ESC to cancel")
        print("   Press 'a' to select all, 'n' to select none\n")
        
        def format_channel(idx, channel):
            name = channel.get('name', 'unknown')
            is_private = channel.get('is_private', False)
            prefix = "🔒" if is_private else "#"
            return f"{prefix} {name}"
        
        selected_channels = await checkbox_selector(
            title=f"Select Slack channels to sync",
            items=channels,
            item_formatter=format_channel
        )
        
        if not selected_channels:
            print("✓ No channels selected")
            return
        
        # Extract channel IDs
        channel_ids = [ch['id'] for ch in selected_channels]
        
        print(f"\n➕ Adding {len(channel_ids)} channel(s) to database '{db_name}':")
        for ch in selected_channels:
            privacy = "🔒" if ch.get('is_private') else "#"
            print(f"   {privacy} {ch.get('name')} ({ch.get('id')})")
        
        # Merge with existing channels (avoid duplicates)
        current_channel_ids = []
        channel_filter = db_config.property_filters.get('channel_id', [])
        if isinstance(channel_filter, str):
            current_channel_ids = [channel_filter]
        elif isinstance(channel_filter, list):
            current_channel_ids = list(channel_filter)

        all_channel_ids = list(dict.fromkeys(current_channel_ids + channel_ids))
        new_count = len(all_channel_ids) - len(current_channel_ids)

        # Update config — always store as list for type consistency
        db_config.property_filters['channel_id'] = all_channel_ids

        # Persist channel ID → name mapping
        channel_names_map = dict(db_config.property_filters.get('channel_names', {}))
        for ch in selected_channels:
            channel_names_map[ch['id']] = ch.get('name', ch['id'])
        db_config.property_filters['channel_names'] = channel_names_map

        db_manager.save_database_field(db_config, "property_filters")
        if current_channel_ids:
            print(f"✓ Added {new_count} new channel(s) to '{db_name}' ({len(all_channel_ids)} total)")
        else:
            print(f"✓ Added {len(channel_ids)} channel(s) to '{db_name}'")

    except ImportError as e:
        print(f"✗ Slack integration not available: {e}")
        print(f"   Install with: uv pip install slack-sdk")
    except Exception as e:
        print(f"✗ Error loading channels: {e}")

async def _resolve_google_account_for_sheets(database_id: str) -> str | None:
    """Find which authenticated Google account can access a sheet/folder.

    - If no accounts are authenticated, triggers the OAuth flow inline.
    - Tries each account until one can access the resource (or uses the
      first account for 'root').
    - Returns the working account email, or None on failure.
    """
    from promaia.auth.registry import get_integration

    google_int = get_integration("google")
    accounts = google_int.list_authenticated_accounts()

    if not accounts:
        print("\n   No Google account authenticated. Starting setup...")
        from promaia.auth.flow import configure_credential
        from rich.console import Console
        success = await configure_credential(google_int, Console())
        if not success:
            print("   Google authentication failed.")
            return None
        accounts = google_int.list_authenticated_accounts()
        if not accounts:
            return None

    # For 'root' just use the first account
    if database_id == "root":
        return accounts[0]

    # Try each account to see which one can access the resource
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    last_error = None
    for acct in accounts:
        try:
            creds = google_int.get_google_credentials(account=acct)
            if not creds:
                continue
            drive = build('drive', 'v3', credentials=creds)
            drive.files().get(fileId=database_id, fields="id").execute()
            return acct
        except HttpError as e:
            last_error = e
            if e.resp.status == 403 and 'accessNotConfigured' in str(e):
                print(f"\n   Google Drive API is not enabled on the OAuth project.")
                print(f"   Enable it at: https://console.developers.google.com/apis/api/drive.googleapis.com/overview")
                print(f"   Then also enable: https://console.developers.google.com/apis/api/sheets.googleapis.com/overview")
                print(f"   Wait a minute after enabling, then retry.")
                # The account is correct, the API just isn't enabled — return it anyway
                return acct
            continue
        except Exception:
            continue

    # None of the accounts could access it — maybe they need to auth a different account
    print(f"\n   None of your authenticated accounts can access this resource.")
    print(f"   Authenticated: {', '.join(accounts)}")
    choice = (await prompt_input("   Authenticate a different account? (y/N): ")).strip().lower()
    if choice in ('y', 'yes'):
        from promaia.auth.flow import configure_credential
        from rich.console import Console
        success = await configure_credential(google_int, Console())
        if success:
            new_accounts = google_int.list_authenticated_accounts()
            for acct in new_accounts:
                if acct not in accounts:
                    return acct
    return None


async def _discover_source_name(source_type: str, database_id: str, workspace: str) -> str | None:
    """Try to fetch the human-readable name from the source system.

    Returns a suggested nickname string, or None if discovery fails.
    """
    try:
        if source_type == "notion":
            from promaia.auth.registry import get_integration
            notion_int = get_integration("notion")
            token = notion_int.get_notion_credentials(workspace)
            if not token:
                return None
            import requests
            resp = requests.get(
                f"https://api.notion.com/v1/databases/{database_id}",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
                timeout=10,
            )
            if resp.status_code == 200:
                title_parts = resp.json().get("title", [])
                title = "".join(t.get("plain_text", "") for t in title_parts).strip()
                if title:
                    return title.lower().replace(" ", "_").replace("-", "_")
        elif source_type == "discord":
            from promaia.auth.registry import get_integration
            discord_int = get_integration("discord")
            bot_token = discord_int.get_discord_token(workspace)
            if not bot_token:
                return None
            import requests
            resp = requests.get(
                f"https://discord.com/api/v10/guilds/{database_id}",
                headers={"Authorization": f"Bot {bot_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                name = resp.json().get("name", "").strip()
                if name:
                    return name.lower().replace(" ", "-")
        elif source_type == "gmail":
            # Use the email local part as the suggested name
            if "@" in database_id:
                return database_id.split("@")[0].lower().replace(".", "-")
        elif source_type == "slack":
            return "slack"
        elif source_type == "shopify":
            # Use the subdomain from the shop domain
            if ".myshopify.com" in database_id:
                return database_id.split(".myshopify.com")[0].lower()
            return database_id.split(".")[0].lower() if "." in database_id else None
        elif source_type == "google_sheets":
            if database_id == "root":
                return "sheets"
            from promaia.auth.registry import get_integration as _get_int
            _g = _get_int("google")
            for _acct in _g.list_authenticated_accounts():
                try:
                    _creds = _g.get_google_credentials(account=_acct)
                    if not _creds:
                        continue
                    from googleapiclient.discovery import build as _build
                    _drive = _build('drive', 'v3', credentials=_creds)
                    meta = _drive.files().get(fileId=database_id, fields="name").execute()
                    name = meta.get("name", "").strip()
                    if name:
                        return name.lower().replace(" ", "_").replace("-", "_")
                except Exception:
                    continue
            return None
    except Exception:
        pass
    return None


async def handle_database_add(args):
    """Handle 'maia database add' command."""
    try:
        return await _handle_database_add_inner(args)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return


async def _handle_database_add_inner(args):
    db_manager = get_database_manager()

    # Get workspace information
    workspace = getattr(args, 'workspace', None)

    # Source type selection with menu
    if args.source_type:
        source_type = args.source_type
    else:
        print("\nAvailable source types:")
        print("  1. notion (default)")
        print("  2. discord")
        print("  3. gmail")
        print("  4. slack")
        print("  5. shopify")
        print("  6. google_sheets")
        choice = (await prompt_input("Select source type (1-6) or press Enter for notion: ")).strip()

        source_type_map = {
            "1": "notion",
            "2": "discord",
            "3": "gmail",
            "4": "slack",
            "5": "shopify",
            "6": "google_sheets",
            "notion": "notion",
            "discord": "discord",
            "gmail": "gmail",
            "slack": "slack",
            "shopify": "shopify",
            "google_sheets": "google_sheets",
            "": "notion"  # default
        }
        source_type = source_type_map.get(choice.lower(), "notion")

    # Use appropriate label and prompt for ID field based on source type
    if source_type == "shopify":
        print("\n💡 Shop Domain: Your Shopify store's .myshopify.com domain")
        print("   Example: 'my-store.myshopify.com'")
        print("   Credentials are read from env vars: SHOPIFY_SHOP_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET")
        database_id = args.database_id or await prompt_input("Shop Domain: ")
    elif source_type == "slack":
        # For Slack, we don't need the actual workspace ID - bot token is enough
        # Auto-generate a universally unique internal identifier (user never sees this)
        import uuid
        database_id = args.database_id or str(uuid.uuid4())
    elif source_type == "discord":
        print("\n💡 Server ID: Your Discord server's unique ID")
        print("   Example: '1291943271509135412'")
        print("   How to find: Right-click server → Copy Server ID (enable Developer Mode in settings first)")
        database_id = args.database_id or await prompt_input("Server ID: ")
    elif source_type == "google_sheets":
        print("\n💡 Google Sheets: Provide a spreadsheet ID, Drive folder ID, or 'root'")
        print("   Spreadsheet ID: from the URL https://docs.google.com/spreadsheets/d/<ID>/edit")
        print("   Folder ID: from the URL https://drive.google.com/drive/folders/<ID>")
        print("   'root' syncs all accessible spreadsheets")
        database_id = args.database_id or await prompt_input("Sheet/Folder ID (or 'root'): ") or "root"
        # Resolve which Google account can access this sheet
        google_account = await _resolve_google_account_for_sheets(database_id)
        if not google_account:
            print("   Could not resolve a Google account for this resource.")
            return
    elif source_type == "gmail":
        print("\n💡 Gmail Account: The Gmail address to sync")
        print("   Examples: 'you@gmail.com', 'team@company.com'")
        database_id = args.database_id or await prompt_input("Gmail Account: ")
    else:  # notion
        print("\n💡 Database ID: Found in your Notion database URL")
        print("   Example: '259700448ad145849e67fa1040a0e120'")
        print("   Where to find: Open database in Notion → Copy link → Extract ID from URL")
        database_id = args.database_id or await prompt_input("Database ID: ")
    
    if not workspace:
        # Get available workspaces and prompt
        from promaia.config.workspaces import get_workspace_manager
        workspace_manager = get_workspace_manager()
        workspaces = workspace_manager.list_workspaces()
        default_workspace = workspace_manager.get_default_workspace()

        if workspaces:
            if len(workspaces) == 1:
                workspace = workspaces[0]
            else:
                print("\nAvailable workspaces:")
                for idx, ws in enumerate(workspaces, 1):
                    default_marker = " (default)" if ws == default_workspace else ""
                    print(f"  {idx}. {ws}{default_marker}")

                if default_workspace:
                    choice = (await prompt_input(f"Select workspace (1-{len(workspaces)}) or press Enter for {default_workspace}: ")).strip()
                else:
                    choice = (await prompt_input(f"Select workspace (1-{len(workspaces)}): ")).strip()

                if not choice and default_workspace:
                    workspace = default_workspace
                elif choice.isdigit() and 1 <= int(choice) <= len(workspaces):
                    workspace = workspaces[int(choice) - 1]
                elif choice in workspaces:
                    workspace = choice
                else:
                    workspace = default_workspace or workspaces[0] if workspaces else "koii"
        else:
            workspace = (await prompt_input("Workspace name (default: koii): ")).strip() or "koii"

    # Discover source name for the nickname suggestion
    suggested_name = None
    if not args.name:
        print("\nLooking up source name...", end="", flush=True)
        suggested_name = await _discover_source_name(source_type, database_id, workspace)
        print(f" {suggested_name or '(could not detect)'}")

    # Name prompt — use discovered name as editable default
    if args.name:
        name = args.name
    elif suggested_name:
        name = (await prompt_input(f"Database name [{suggested_name}]: ")).strip() or suggested_name
    else:
        name = await prompt_input("Database name: ")

    description = args.description or (await prompt_input("Description (optional): ")).strip()

    config = {
        "source_type": source_type,
        "database_id": database_id,
        "description": description,
        "workspace": workspace,
        "sync_enabled": True,
        "include_properties": True,
        "default_days": 30 if source_type == "shopify" else 7,
        "save_markdown": source_type != "shopify"
    }

    # Store Google account for Sheets connector
    if source_type == "google_sheets" and google_account:
        config["google_account"] = google_account

    try:
        if db_manager.add_database(name, config, workspace):
            # For lookup, we need to use the actual stored key format
            # If name contains workspace prefix, use it directly; otherwise build qualified name
            if '.' in name and name.startswith(f"{workspace}."):
                lookup_name = name
            else:
                lookup_name = name
            
            db_config = db_manager.get_database(lookup_name, workspace)
            if not db_config:
                # Fallback: try by qualified name lookup
                if '.' in name:
                    db_config = db_manager.get_database_by_qualified_name(name)
                else:
                    qualified_name = f"{workspace}.{name}" if workspace != "koii" else name
                    db_config = db_manager.get_database_by_qualified_name(qualified_name)
            
            if db_config:
                print(f"✓ Added database '{db_config.get_qualified_name()}' successfully")
            else:
                print(f"✓ Added database '{name}' successfully")
                return  # Skip connection test if we can't retrieve the config
            
            # Test connection with full database config (includes workspace info)
            try:
                connector_config = db_config.to_dict()
                # For Discord, inject bot token so the connection test works
                if source_type == "discord":
                    bot_token = _get_discord_bot_token(workspace)
                    if bot_token:
                        connector_config['bot_token'] = bot_token
                connector = ConnectorRegistry.get_connector(source_type, connector_config)
                if connector and await connector.test_connection():
                    print("✓ Connection test successful")
                else:
                    print("⚠ Warning: Connection test failed")
            except ImportError as ie:
                print(f"⚠ Warning: Connection test skipped - {ie}")
                print(f"  Note: The database was added successfully, but connection couldn't be tested.")
            except Exception as conn_e:
                print(f"⚠ Warning: Connection test failed - {conn_e}")

            # For Gmail databases, check/trigger Google OAuth
            if source_type == "gmail":
                try:
                    from promaia.auth.registry import get_integration

                    google_int = get_integration("google")
                    existing_creds = google_int.get_google_credentials(account=database_id)

                    if not existing_creds:
                        print(f"\nGoogle credentials needed for {database_id}")
                        print(f"Launching OAuth flow...")
                        from promaia.auth.flow import configure_credential
                        from rich.console import Console

                        success = await configure_credential(
                            google_int, Console(), account=database_id
                        )
                        if success:
                            print(f"Google authenticated for {database_id}")
                        else:
                            print(f"OAuth not completed. Run later: maia auth configure google --account {database_id}")
                    else:
                        print(f"Google credentials found for {database_id}")
                except Exception as auth_e:
                    print(f"Could not check Google auth: {auth_e}")
                    print(f"Run later: maia auth configure google --account {database_id}")

            # For Google Sheets databases, check/trigger Google OAuth
            if source_type == "google_sheets":
                try:
                    from promaia.auth.registry import get_integration

                    google_int = get_integration("google")
                    existing_creds = google_int.get_google_credentials()

                    if not existing_creds:
                        print(f"\nGoogle credentials needed for Sheets access")
                        print(f"Run: maia auth configure google")
                    else:
                        print(f"Google credentials found")
                except Exception as auth_e:
                    print(f"Could not check Google auth: {auth_e}")
                    print(f"Run: maia auth configure google")

            # For Discord databases, check bot token before offering channel selection
            if source_type == "discord":
                bot_token = _get_discord_bot_token(workspace)
                if not bot_token:
                    print(f"\n⚠ Discord bot token not found for workspace '{workspace}'")
                    print(f"💡 To set up Discord and select channels:")
                    print(f"   1. Run: maia workspace discord-setup {workspace}")
                    print(f"   2. Then: maia database channel add {name}")
                else:
                    print("\n📋 Would you like to select Discord channels to sync?")
                    channel_choice = (await prompt_input("Select channels now? (y/N): ")).strip().lower()

                    if channel_choice in ['y', 'yes']:
                        try:
                            await _add_discord_channels_interactive(db_config, workspace, name)
                        except Exception as e:
                            print(f"⚠ Warning: Could not load Discord channels: {e}")
                            print(f"💡 You can add channels later with: maia database channel add {name}")
                    else:
                        print(f"💡 You can add channels later with: maia database channel add {name}")

            elif source_type == "slack":
                print("\n📋 Would you like to select Slack channels to sync?")
                channel_choice = (await prompt_input("Select channels now? (y/N): ")).strip().lower()

                if channel_choice in ['y', 'yes']:
                    try:
                        await _add_slack_channels_interactive(db_config, workspace, name)
                    except Exception as ch_e:
                        print(f"⚠ Warning: Could not add channels: {ch_e}")
                        print(f"💡 You can add channels later with: maia database channel add {name}")
                else:
                    print(f"💡 You can add channels later with: maia database channel add {name}")

            # Initial sync
            qualified = db_config.get_qualified_name() if db_config else name
            print(f"\n📥 Running initial sync for {qualified}...")
            try:
                class _SyncArgs:
                    def __init__(self):
                        self.sources = [qualified]
                        self.workspace = None
                        self.browse = None
                        self.limit = None
                await handle_database_sync(_SyncArgs())
            except Exception as sync_e:
                print(f"⚠ Initial sync failed: {sync_e}")
                print(f"💡 You can sync later with: maia database sync {qualified}")
        else:
            print(f"✗ Failed to add database '{name}' (may already exist)")

    except Exception as e:
        print(f"✗ Failed to add database: {e}")

async def handle_database_remove(args):
    """Handle 'maia database remove' command."""
    db_manager = get_database_manager()
    
    if not args.name:
        print("Database name is required")
        return
    
    # Parse workspace.database format if provided
    workspace = getattr(args, 'workspace', None)
    name = args.name
    original_name = name
    
    if '.' in name and not workspace:
        workspace, name = name.split('.', 1)
    
    # Try multiple resolution strategies
    db_config = None
    
    # First try: use get_database with parsed workspace and name
    db_config = db_manager.get_database(name, workspace)
    
    # Second try: if that fails and we have a qualified name, try direct lookup
    if not db_config and '.' in original_name:
        db_config = db_manager.get_database_by_qualified_name(original_name)
    
    # Third try: try direct key lookup in databases dict
    if not db_config and original_name in db_manager.databases:
        db_config = db_manager.databases[original_name]
    
    if db_config:
        # Find the actual key to remove
        key_to_remove = None
        for key, config in db_manager.databases.items():
            if config == db_config:
                key_to_remove = key
                break
        
        if key_to_remove:
            db_manager.remove_database(key_to_remove)
            print(f"✓ Removed database '{key_to_remove}'")
        else:
            print(f"✗ Could not find database key for '{original_name}'")
    else:
        print(f"✗ Database '{original_name}' not found")

async def handle_database_remove_channels(args):
    """Handle 'maia database channel remove' command to remove channels via browser."""
    from promaia.storage.hybrid_storage import get_hybrid_registry
    from promaia.cli.discord_commands import interactive_channel_browser, get_accessible_channels_cached
    from promaia.connectors.discord_connector import DiscordConnector
    from rich.console import Console

    console = Console()
    db_manager = get_database_manager()
    database_name = getattr(args, 'database_name', None)

    if not database_name:
        database_name = await _select_database_interactive(source_types=['discord', 'slack'])
        if not database_name:
            return

    # Parse database name (handle workspace.database format)
    workspace = None
    if '.' in database_name:
        workspace, db_name = database_name.split('.', 1)
    else:
        db_name = database_name

    # Get database config
    db_config = db_manager.get_database(db_name, workspace)
    if not db_config:
        print(f"✗ Database '{database_name}' not found")
        return

    # Check if it's a Discord database
    if db_config.source_type != 'discord':
        print(f"✗ Database '{database_name}' is not a Discord database (type: {db_config.source_type})")
        return
    
    try:
        print(f"🔍 Loading channels for Discord database '{database_name}'...")

        # Get current channel filters from config
        current_channel_ids = []
        channel_filter = db_config.property_filters.get('channel_id', [])
        if isinstance(channel_filter, str):
            current_channel_ids = [channel_filter]
        elif isinstance(channel_filter, list):
            current_channel_ids = channel_filter

        if not current_channel_ids:
            print(f"✓ No channels configured for database '{database_name}'")
            return

        # Create a fake server structure for the browser to show current channels
        servers = [{
            "server_id": db_config.database_id,
            "server_name": f"Discord Server ({db_config.nickname})",
            "db_name": db_config.nickname,
            "channels": [{"channel_id": cid, "name": f"Channel {cid}", "channel_name": f"Channel {cid}", "accessible": True} for cid in current_channel_ids]
        }]

        print(f"📋 Select channels to REMOVE from database '{database_name}':")
        print("   Use SPACE to select channels, ENTER to confirm, ESC to cancel")

        # Use the existing interactive browser to let user select channels to remove
        selected_channels, _ = await interactive_channel_browser(console, servers, db_config.workspace)

        if not selected_channels:
            print("No channels selected for removal.")
            return

        # Extract channel IDs from selection
        channels_to_remove = [channel[1] for channel in selected_channels]  # channel[1] is channel_id

        print(f"\n🗑️  Will remove {len(channels_to_remove)} channels from database '{database_name}':")
        for channel_id in channels_to_remove:
            print(f"   - {channel_id}")
        
        # Confirm removal
        if not args.force:
            response = await prompt_input(f"\nThis will:\n1. Remove channels from config\n2. Delete all stored data for these channels\n\nContinue? (y/N): ")
            if response.lower() not in ['y', 'yes']:
                print("Operation cancelled")
                return
        
        # Update config - remove channels from filter
        remaining_channels = [cid for cid in current_channel_ids if cid not in channels_to_remove]
        
        if remaining_channels:
            db_config.property_filters['channel_id'] = remaining_channels
        else:
            # Remove the filter entirely if no channels left
            if 'channel_id' in db_config.property_filters:
                del db_config.property_filters['channel_id']
        
        # Clean up channel_names for removed channels
        channel_names_map = dict(db_config.property_filters.get('channel_names', {}))
        for cid in channels_to_remove:
            channel_names_map.pop(cid, None)
        if channel_names_map:
            db_config.property_filters['channel_names'] = channel_names_map
        else:
            db_config.property_filters.pop('channel_names', None)

        db_manager.save_database_field(db_config, "property_filters")
        print(f"✓ Updated config for database '{database_name}'")

        # Remove stored data for these channels
        registry = get_hybrid_registry()
        total_removed = 0
        
        for channel_id in channels_to_remove:
            # Get content for this specific channel
            content_list = registry.list_content(
                content_type=db_config.nickname,
                workspace=db_config.workspace
            )
            
            channel_content = [
                item for item in content_list 
                if item.get('discord_channel_id') == channel_id
            ]
            
            print(f"🗑️  Removing {len(channel_content)} items from channel {channel_id}...")
            
            for item in channel_content:
                page_id = item.get('page_id')
                if page_id and registry.remove_content(page_id):
                    total_removed += 1
                else:
                    print(f"✗ Failed to remove item with page_id: {page_id}")
        
        print(f"✅ Successfully removed {len(channels_to_remove)} channels and {total_removed} stored items from database '{database_name}'")
        
    except Exception as e:
        print(f"✗ Error removing channels: {e}")
        import traceback
        traceback.print_exc()

async def handle_database_purge_data(database_config):
    """Purge all locally stored data for a database."""
    from promaia.storage.hybrid_storage import get_hybrid_registry
    import shutil
    
    try:
        # Get the database's markdown directory
        md_dir = getattr(database_config, 'markdown_directory', None)
        
        # Purge from registry database
        registry = get_hybrid_registry()
        db_name = database_config.nickname
        workspace = database_config.workspace
        qualified_name = f"{workspace}.{db_name}" if workspace else db_name
        
        # Count items to be removed
        removed_count = 0
        
        # Query registry for all content from this database
        try:
            # Get all content for this database
            content_items = registry.get_content_by_database(qualified_name)
            
            # Remove each item from registry
            for item in content_items:
                page_id = item.get('page_id')
                if page_id and registry.remove_content(page_id):
                    removed_count += 1
        except Exception as e:
            print(f"⚠️  Warning: Could not clean registry for database '{qualified_name}': {e}")
        
        # Remove markdown directory if it exists
        if md_dir and os.path.exists(md_dir):
            try:
                shutil.rmtree(md_dir)
                print(f"🗂️  Removed markdown directory: {md_dir}")
            except Exception as e:
                print(f"⚠️  Warning: Could not remove directory '{md_dir}': {e}")
        
        return removed_count
        
    except Exception as e:
        print(f"✗ Error purging data for database: {e}")
        return 0

async def handle_database_remove_with_data_purge(args):
    """Handle 'maia database remove' command with data purging."""
    db_manager = get_database_manager()
    
    if not args.name:
        print("Database name is required")
        return
    
    # Parse workspace.database format if provided
    workspace = getattr(args, 'workspace', None)
    name = args.name
    original_name = name
    
    if '.' in name and not workspace:
        workspace, name = name.split('.', 1)
    
    # Try multiple resolution strategies
    db_config = None
    
    # First try: use get_database with parsed workspace and name
    db_config = db_manager.get_database(name, workspace)
    
    # Second try: if that fails and we have a qualified name, try direct lookup
    if not db_config and '.' in original_name:
        db_config = db_manager.get_database_by_qualified_name(original_name)
    
    # Third try: try direct key lookup in databases dict
    if not db_config and original_name in db_manager.databases:
        db_config = db_manager.databases[original_name]
    
    if not db_config:
        print(f"✗ Database '{original_name}' not found")
        return
    
    # Confirm removal
    if not getattr(args, 'force', False):
        response = await prompt_input(f"This will:\n1. Remove database '{original_name}' from config\n2. Delete all locally stored data for this database\n\nContinue? (y/N): ")
        if response.lower() not in ['y', 'yes']:
            print("Operation cancelled")
            return
    
    # Find the actual key to remove
    key_to_remove = None
    for key, config in db_manager.databases.items():
        if config == db_config:
            key_to_remove = key
            break
    
    if not key_to_remove:
        print(f"✗ Could not find database key for '{original_name}'")
        return
    
    # Purge data first
    print(f"🗑️  Purging locally stored data for database '{original_name}'...")
    removed_count = await handle_database_purge_data(db_config)
    
    # Remove from config
    db_manager.remove_database(key_to_remove)

    print(f"✅ Successfully removed database '{key_to_remove}' and purged {removed_count} stored items")

async def handle_database_remove_interactive(args):
    """Handle interactive database removal using simple selector."""
    from promaia.cli.simple_selector import interactive_simple_selector
    from promaia.config.workspaces import get_workspace_manager
    
    # Get workspace
    workspace_manager = get_workspace_manager()
    workspace = getattr(args, 'workspace', None)
    
    if not workspace:
        workspace = workspace_manager.get_default_workspace()
        
    if not workspace:
        print("✗ No workspace specified and no default workspace set")
        return
    
    # Launch selector
    print(f"🔍 Launching database selector for workspace '{workspace}'...")
    selected_databases = await interactive_simple_selector(workspace, "databases", "Select Databases to Remove")
    
    if not selected_databases:
        print("ℹ️  No databases selected for removal")
        return
    
    # Check for dry-run mode
    dry_run = getattr(args, 'dry_run', False)
    
    if dry_run:
        print(f"\n🔍 DRY RUN - Would remove {len(selected_databases)} databases:")
        for db_name in selected_databases:
            print(f"   - {db_name}")
        print("\nActions that would be performed:")
        print("1. Remove databases from config")
        print("2. Delete all locally stored data for these databases")
        print("3. Clean registry entries")
        print("\nRun without --dry-run to actually perform these actions.")
        return
    
    # Confirm removal
    if not getattr(args, 'force', False):
        print(f"\n🗑️  Will remove {len(selected_databases)} databases:")
        for db_name in selected_databases:
            print(f"   - {db_name}")
        
        response = await prompt_input(f"\nThis will:\n1. Remove databases from config\n2. Delete all locally stored data for these databases\n\nContinue? (y/N): ")
        if response.lower() not in ['y', 'yes']:
            print("Operation cancelled")
            return
    
    # Remove each database
    db_manager = get_database_manager()
    total_removed = 0
    successfully_removed = 0
    
    for db_name in selected_databases:
        try:
            # Get database config
            if '.' in db_name:
                workspace_part, name_part = db_name.split('.', 1)
                db_config = db_manager.get_database(name_part, workspace_part)
            else:
                db_config = db_manager.get_database(db_name, workspace)
            
            if not db_config:
                print(f"✗ Database '{db_name}' not found")
                continue
            
            # Purge data
            removed_count = await handle_database_purge_data(db_config)
            
            # Find the actual key to remove
            key_to_remove = None
            for key, config in db_manager.databases.items():
                if config == db_config:
                    key_to_remove = key
                    break
            
            if key_to_remove:
                db_manager.remove_database(key_to_remove)
                print(f"✅ Removed database '{key_to_remove}' and purged {removed_count} items")
                total_removed += removed_count
                successfully_removed += 1
            else:
                print(f"✗ Could not find database key for '{db_name}'")

        except Exception as e:
            print(f"✗ Error removing database '{db_name}': {e}")

    if successfully_removed > 0:
        print(f"🎉 Successfully removed {successfully_removed} databases and purged {total_removed} total items")

async def handle_channel_remove_interactive(args):
    """Handle interactive Discord channel removal using simple selector.""" 
    from promaia.cli.simple_selector import interactive_simple_selector
    from promaia.config.workspaces import get_workspace_manager
    from promaia.storage.hybrid_storage import get_hybrid_registry
    import shutil
    from pathlib import Path
    
    # Get workspace
    workspace_manager = get_workspace_manager()
    workspace = getattr(args, 'workspace', None)
    
    if not workspace:
        workspace = workspace_manager.get_default_workspace()
        
    if not workspace:
        print("✗ No workspace specified and no default workspace set")
        return
    
    # Launch selector
    print(f"🔍 Launching Discord channel selector for workspace '{workspace}'...")
    selected_channels = await interactive_simple_selector(workspace, "channels", "Select Discord Channels to Remove")
    
    if not selected_channels:
        print("ℹ️  No channels selected for removal")
        return
    
    # Check for dry-run mode
    dry_run = getattr(args, 'dry_run', False)
    
    if dry_run:
        print(f"\n🔍 DRY RUN - Would remove {len(selected_channels)} Discord channels:")
        for channel_spec in selected_channels:
            print(f"   - {channel_spec}")
        print("\nActions that would be performed:")
        print("1. Remove channels from database config")
        print("2. Delete all locally stored data for these channels")
        print("3. Clean registry entries")
        print("\nRun without --dry-run to actually perform these actions.")
        return
    
    # Confirm removal
    if not getattr(args, 'force', False):
        print(f"\n🗑️  Will remove {len(selected_channels)} Discord channels:")
        for channel_spec in selected_channels:
            print(f"   - {channel_spec}")
        
        response = await prompt_input(f"\nThis will:\n1. Remove channels from database config\n2. Delete all locally stored data for these channels\n\nContinue? (y/N): ")
        if response.lower() not in ['y', 'yes']:
            print("Operation cancelled")
            return
    
    # Process each channel for removal
    db_manager = get_database_manager()
    registry = get_hybrid_registry()
    total_removed_items = 0
    total_removed_channels = 0
    
    # Group channels by database
    channels_by_database = {}
    for channel_spec in selected_channels:
        if '#' not in channel_spec:
            continue
            
        db_name, channel_name = channel_spec.split('#', 1)
        if db_name not in channels_by_database:
            channels_by_database[db_name] = []
        channels_by_database[db_name].append(channel_name)
    
    # Process each database
    for db_name, channel_names in channels_by_database.items():
        try:
            # Get database config
            if '.' in db_name:
                workspace_part, name_part = db_name.split('.', 1)
                db_config = db_manager.get_database(name_part, workspace_part)
            else:
                db_config = db_manager.get_database(db_name, workspace)
            
            if not db_config:
                print(f"✗ Database '{db_name}' not found")
                continue
                
            if db_config.source_type != 'discord':
                print(f"✗ Database '{db_name}' is not a Discord database")
                continue
            
            # Remove each channel
            for channel_name in channel_names:
                try:
                    # Remove from registry by channel
                    removed_count = 0
                    try:
                        # Get all content for this database
                        content_items = registry.list_content(
                            workspace=db_config.workspace,
                            database_name=db_config.get_qualified_name()
                        )
                        
                        # Filter for this specific channel and remove
                        for item in content_items:
                            # Check if this item is from the channel we want to remove
                            metadata = item.get('metadata', {})
                            if (metadata.get('channel_name') == channel_name or 
                                metadata.get('discord_channel_name') == channel_name):
                                page_id = item.get('page_id')
                                if page_id and registry.remove_content(page_id):
                                    removed_count += 1
                    except Exception as e:
                        print(f"⚠️  Warning: Could not clean registry for channel '{channel_name}': {e}")
                    
                    # Remove channel directory
                    md_dir = Path(db_config.markdown_directory) / channel_name
                    if md_dir.exists():
                        try:
                            shutil.rmtree(md_dir)
                            print(f"📁 Removed channel directory: {md_dir}")
                        except Exception as e:
                            print(f"⚠️  Warning: Could not remove directory '{md_dir}': {e}")
                    
                    # Remove from database config (channel_id filter)
                    channel_id_removed = await remove_channel_from_config(db_config, channel_name, db_manager)
                    if not channel_id_removed:
                        print(f"⚠️  Note: Channel '{channel_name}' data removed, but could not remove from database config")
                    
                    print(f"✅ Removed channel '{db_name}#{channel_name}' and purged {removed_count} items")
                    total_removed_items += removed_count
                    total_removed_channels += 1
                    
                except Exception as e:
                    print(f"✗ Error removing channel '{channel_name}': {e}")
                    
        except Exception as e:
            print(f"✗ Error processing database '{db_name}': {e}")
    
    if total_removed_channels > 0:
        print(f"🎉 Successfully removed {total_removed_channels} channels and purged {total_removed_items} total items")

async def handle_database_add_channels(args):
    """Handle 'maia database channel add' command to add channels via interactive browser.

    Supports Discord and Slack databases.
    """
    db_manager = get_database_manager()
    database_name = getattr(args, 'database_name', None)

    if not database_name:
        database_name = await _select_database_interactive(source_types=['discord', 'slack'])
        if not database_name:
            return

    # Parse database name (handle workspace.database format)
    workspace = None
    if '.' in database_name:
        workspace, db_name = database_name.split('.', 1)
    else:
        db_name = database_name

    # Get database config
    db_config = db_manager.get_database(db_name, workspace)
    if not db_config:
        print(f"✗ Database '{database_name}' not found")
        return

    effective_workspace = db_config.workspace or workspace

    if db_config.source_type == 'discord':
        await _handle_add_discord_channels(db_config, effective_workspace, database_name)
    elif db_config.source_type == 'slack':
        await _add_slack_channels_interactive(db_config, effective_workspace, database_name)
    else:
        print(f"✗ Channel management is not supported for source type '{db_config.source_type}'")
        print(f"   Supported types: discord, slack")


async def _handle_add_discord_channels(db_config, workspace, display_name):
    """Add Discord channels via interactive browser (used by channel add command)."""
    from promaia.cli.discord_commands import interactive_channel_browser, get_accessible_channels_cached
    from rich.console import Console

    console = Console()
    db_manager = get_database_manager()

    bot_token = _get_discord_bot_token(workspace)
    if not bot_token:
        print(f"✗ No Discord bot token configured for workspace '{workspace}'")
        print(f"💡 To set up Discord credentials:")
        print(f"   Run: maia workspace discord-setup {workspace}")
        return

    try:
        print(f"🔍 Loading available channels for Discord server...")

        # Get all available channels for this server
        default_days = db_config.default_days or 7
        servers = []
        try:
            channels, server_name = await get_accessible_channels_cached(db_config, bot_token)
            if channels:
                servers = [{
                    "server_id": db_config.database_id,
                    "server_name": server_name,
                    "db_name": db_config.nickname,
                    "channels": channels,
                    "days": default_days,
                }]
        except Exception as e:
            print(f"⚠️  Could not fetch live channels: {e}")
            print("You may need to run 'maia discord refresh' first to update channel cache.")
            return

        if not servers or not servers[0]["channels"]:
            print(f"✗ No accessible channels found for database '{display_name}'")
            print("Try running 'maia discord refresh' to update the channel cache.")
            return

        # Build previous_selections from existing config so already-added channels
        # show as checked with their saved per-channel days
        previous_selections = []
        existing_channel_ids = set()
        channel_filter = db_config.property_filters.get('channel_id', [])
        if isinstance(channel_filter, str):
            existing_channel_ids = {channel_filter}
        elif isinstance(channel_filter, list):
            existing_channel_ids = set(channel_filter)

        saved_channel_days = db_config.property_filters.get('channel_days', {})

        if existing_channel_ids and channels:
            for ch in channels:
                if ch['id'] in existing_channel_ids:
                    ch_days = saved_channel_days.get(ch['id'], default_days)
                    previous_selections.append((
                        db_config.nickname,  # db_name
                        ch['id'],            # channel_id
                        ch['name'],          # channel_name
                        ch_days,             # days
                    ))

        print(f"📋 Select channels to ADD to database '{display_name}':")
        print("   Use SPACE to select channels, ENTER to confirm, ESC to cancel")

        # Use the existing interactive browser with pre-checked existing channels
        selected_channels, _ = await interactive_channel_browser(
            console, servers, db_config.workspace,
            previous_selections=previous_selections,
        )

        if not selected_channels:
            print("No channels selected.")
            return

        # Extract channel IDs and per-channel days from selection
        channels_to_add = [channel[1] for channel in selected_channels]
        channel_days = {channel[1]: channel[3] for channel in selected_channels}

        print(f"\n➕ Will add {len(channels_to_add)} channels to database '{display_name}':")
        for channel in selected_channels:
            print(f"   - {channel[2]}:{channel[3]} ({channel[1]})")  # name:days (id)

        # Merge with any existing channels not in the current selection
        # (selected_channels includes pre-checked existing ones, but there may be
        # channels that were previously configured via a different server listing)
        for old_id in existing_channel_ids:
            if old_id not in channels_to_add:
                channels_to_add.append(old_id)
        db_config.property_filters['channel_id'] = channels_to_add
        # Merge channel_days: keep old values for channels not in new selection
        merged_days = dict(db_config.property_filters.get('channel_days', {}))
        merged_days.update(channel_days)
        db_config.property_filters['channel_days'] = merged_days

        # Persist channel ID → name mapping
        channel_names_map = dict(db_config.property_filters.get('channel_names', {}))
        for channel in selected_channels:
            channel_names_map[channel[1]] = channel[2]  # channel[1]=id, channel[2]=name
        db_config.property_filters['channel_names'] = channel_names_map

        # Ask which channels should use OCR processing for images
        if len(selected_channels) > 0:
            print(f"\n📷 Which channels should use OCR for images (e.g. handwritten notes)?")
            print("   These channels will download images and run them through the OCR pipeline.")
            print("   Other channels will sync text as normal.\n")
            # Preserve existing OCR channels that aren't part of this selection
            existing_ocr = set(db_config.property_filters.get('ocr_channels', []))
            ocr_selection = [ch_id for ch_id in existing_ocr if ch_id not in set(channels_to_add)]
            for channel in selected_channels:
                ch_name = channel[2]
                ch_id = channel[1]
                default = "Y/n" if ch_id in existing_ocr else "y/N"
                try:
                    answer = (await prompt_input(f"   Enable OCR for #{ch_name}? ({default}): ")).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ''
                if default == "Y/n":
                    # Previously enabled — keep unless explicitly declined
                    if answer not in ('n', 'no'):
                        ocr_selection.append(ch_id)
                else:
                    if answer in ('y', 'yes'):
                        ocr_selection.append(ch_id)
            if ocr_selection:
                db_config.property_filters['ocr_channels'] = ocr_selection
                print(f"✓ OCR enabled for {len(ocr_selection)} channel(s)")
            elif 'ocr_channels' in db_config.property_filters:
                del db_config.property_filters['ocr_channels']

        db_manager.save_database_field(db_config, "property_filters")

        print(f"✅ Successfully updated channels for database '{display_name}'")

        # Prompt to sync now
        try:
            answer = input(f"Sync now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = 'n'

        if answer in ('', 'y', 'yes'):
            # Build source specs with per-channel days
            source_specs = []
            for channel in selected_channels:
                # channel = (db_name, channel_id, channel_name, days)
                source_specs.append(f"{display_name}:{channel[3]}")
            # Deduplicate — use the max days across channels for the database-level sync
            max_days = max(ch[3] for ch in selected_channels)

            class SyncArgs:
                def __init__(self):
                    self.sources = [f"{display_name}:{max_days}"]
                    self.workspace = None
                    self.browse = None
                    self.limit = None

            await handle_database_sync(SyncArgs())

    except Exception as e:
        print(f"✗ Error adding channels: {e}")
        import traceback
        traceback.print_exc()

async def handle_database_channel_list(args):
    """Handle 'maia database channel list' command — show configured channels."""
    db_manager = get_database_manager()
    database_name = getattr(args, 'database_name', None)

    if not database_name:
        database_name = await _select_database_interactive(source_types=['discord', 'slack'])
        if not database_name:
            return

    # Parse database name (handle workspace.database format)
    workspace = None
    if '.' in database_name:
        workspace, db_name = database_name.split('.', 1)
    else:
        db_name = database_name

    db_config = db_manager.get_database(db_name, workspace)
    if not db_config:
        print(f"✗ Database '{database_name}' not found")
        return

    channel_filter = db_config.property_filters.get('channel_id', [])
    if isinstance(channel_filter, str):
        channel_ids = [channel_filter]
    elif isinstance(channel_filter, list):
        channel_ids = channel_filter
    else:
        channel_ids = []

    qualified = db_config.get_qualified_name()

    if not channel_ids:
        print(f"No channels configured for '{qualified}'")
        print(f"💡 Add channels with: maia database channel add {qualified}")
        return

    print(f"Channels configured for '{qualified}' ({db_config.source_type}):")
    for i, cid in enumerate(channel_ids, 1):
        print(f"  {i}. {cid}")
    print(f"\n{len(channel_ids)} channel(s) total")


async def _select_action_interactive(actions: List[Dict[str, str]], title: str = "") -> Optional[str]:
    """Show a single-select action menu. Returns the chosen action key, or None on cancel.

    Each action dict has 'key', 'label'.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    current_focus = 0
    confirmed = False

    def format_line(idx):
        text = f"  {actions[idx]['label']}"
        style = "reverse" if idx == current_focus else ""
        return [(style, text)]

    def get_status():
        prefix = f"  {title} | " if title else "  "
        return f"{prefix}↑↓ Navigate  ENTER Select  ESC Cancel"

    item_windows = [
        Window(FormattedTextControl(lambda idx=i: format_line(idx)), height=1)
        for i in range(len(actions))
    ]

    container = HSplit([
        Window(FormattedTextControl(get_status), height=1),
        Window(height=1),
        *item_windows,
    ])

    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(actions) - 1:
            current_focus += 1

    @bindings.add(Keys.Enter)
    def confirm(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=Layout(container),
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    await app.run_async()

    if confirmed:
        return actions[current_focus]['key']
    return None


async def handle_database_edit(args):
    """Handle 'maia database edit' — interactive action picker for a database."""
    db_manager = get_database_manager()
    name = getattr(args, 'name', None)

    if not name:
        name = await _select_database_interactive()
        if not name:
            return

    # Parse database name (handle workspace.database format)
    workspace = None
    if '.' in name:
        workspace, db_name = name.split('.', 1)
    else:
        db_name = name

    db_config = db_manager.get_database(db_name, workspace)
    if not db_config:
        print(f"✗ Database '{name}' not found")
        return

    qualified = db_config.get_qualified_name()
    source_type = db_config.source_type

    # Build action list
    actions = []
    if source_type in ('discord', 'slack'):
        actions.append({'key': 'edit_channels', 'label': 'Edit channels'})
    actions.append({'key': 'info', 'label': 'View info'})
    actions.append({'key': 'remove', 'label': 'Remove database'})

    action = await _select_action_interactive(actions, title=f"{qualified} ({source_type})")
    if not action:
        return

    # Build a fake args namespace to pass to the handler
    class FakeArgs:
        pass

    fake = FakeArgs()

    if action == 'edit_channels':
        fake.database_name = qualified
        await handle_database_add_channels(fake)
    elif action == 'info':
        fake.name = qualified
        fake.schema = False
        await handle_database_info(fake)
    elif action == 'remove':
        fake.name = qualified
        await handle_database_remove(fake)


async def handle_database_test(args):
    """Handle 'maia database test' command."""
    db_manager = get_database_manager()

    databases_to_test = []

    if args.names:
        # Test specific databases
        for name in args.names:
            workspace = None
            if '.' in name:
                workspace, db_name = name.split('.', 1)
            else:
                db_name = name
            
            db_config = db_manager.get_database(db_name, workspace)
            if db_config:
                databases_to_test.append((name, db_config))
            else:
                print(f"✗ {name}: Database not found")
    else:
        # Test all databases
        all_databases = db_manager.list_databases()
        for db_name in all_databases:
            db_config = db_manager.get_database(db_name)
            if db_config:
                databases_to_test.append((db_config.get_qualified_name(), db_config))
    
    for display_name, db_config in databases_to_test:
        try:
            connector_config = db_config.to_dict()

            # Shopify reads credentials from env vars — no workspace API key needed
            if db_config.source_type not in ('shopify', 'conversation'):
                from promaia.config.workspaces import get_workspace_api_key
                api_key = get_workspace_api_key(db_config.workspace)

                if not api_key:
                    print(f"✗ {display_name}: No API key configured for workspace '{db_config.workspace}'")
                    continue

                connector_config['api_key'] = api_key

            connector = ConnectorRegistry.get_connector(db_config.source_type, connector_config)
            if connector and await connector.test_connection():
                print(f"✓ {display_name}: Connection successful")
            else:
                print(f"✗ {display_name}: Connection failed")
        except Exception as e:
            print(f"✗ {display_name}: {e}")

async def handle_database_sync(args):
    """Handle 'maia database sync' command."""
    # Check if browse mode is requested
    if hasattr(args, 'browse') and args.browse is not None:
        await handle_database_sync_with_browse(args)
        return
    
    # MONITORING: Track overall sync performance
    overall_start_time = datetime.now()
    
    db_manager = get_database_manager()
    
    # Handle workspace expansion if provided
    if hasattr(args, 'workspace') and args.workspace and not args.sources:
        # Expand workspace to individual database source specifications
        workspace_databases = db_manager.get_workspace_databases(args.workspace)
        
        # Build source specifications with qualified names and default days
        # Only include enabled databases to avoid syncing disabled/problematic ones
        expanded_sources = []
        for db in workspace_databases:
            if not db.sync_enabled:
                print(f"⚠️  Skipping disabled database: {db.get_qualified_name()}")
                continue
                
            qualified_name = db.get_qualified_name()
            default_days = db.default_days
            source_spec = f"{qualified_name}:{default_days}"
            expanded_sources.append(source_spec)
            print(f"📦 Adding to sync: {source_spec}")
        
        if not expanded_sources:
            print(f"❌ No enabled databases configured for workspace '{args.workspace}'")
            return
        else:
            print(f"📦 Workspace '{args.workspace}' expanded to {len(expanded_sources)} databases")
            # Set the expanded sources as if they were provided via -s arguments
            args.sources = expanded_sources
    
    # Parse source specifications (now supports workspace.database format)
    sources = parse_source_specs(args.sources) if args.sources else []
    
    if not sources:
        # Sync all enabled databases
        for db_name in db_manager.list_databases():
            db_config = db_manager.get_database(db_name)
            if db_config and db_config.sync_enabled:
                sources.append({"name": db_name, "qualified_name": db_config.get_qualified_name()})
    
    if not sources:
        print("No databases to sync")
        return
    
    print(f"🚀 Syncing {len(sources)} database(s) with optimized performance...")
    
    # OPTIMIZATION: Sync databases in parallel with maximum safe concurrency
    # Chunk databases to prevent overwhelming the system while maximizing throughput
    MAX_CONCURRENT_DATABASES = 15  # Optimal for most systems
    all_results = []
    
    for i in range(0, len(sources), MAX_CONCURRENT_DATABASES):
        chunk = sources[i:i + MAX_CONCURRENT_DATABASES]
        chunk_tasks = [sync_database(source_spec, args) for source_spec in chunk]
        
        # Execute chunk concurrently
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
        all_results.extend(chunk_results)
        
        # Minimal delay between chunks to prevent system overload
        if i + MAX_CONCURRENT_DATABASES < len(sources):
            await asyncio.sleep(0.05)
    
    # MONITORING: Report overall performance
    overall_duration = (datetime.now() - overall_start_time).total_seconds()
    
    # Display comprehensive summary
    display_sync_summary(all_results, overall_duration)

async def sync_database(source_spec: Dict[str, Any], args):
    """Sync a single database based on source specification."""
    db_name = source_spec["name"]
    qualified_name = source_spec.get("qualified_name", db_name)
    
    db_manager = get_database_manager()
    
    # Parse workspace.database if needed
    workspace = None
    if '.' in db_name:
        workspace, db_name = db_name.split('.', 1)
    
    db_config = db_manager.get_database(db_name, workspace)
    
    if not db_config:
        print(f"✗ Database '{qualified_name}' not found")
        # Return a synthetic result for missing databases
        from promaia.connectors.base import SyncResult
        result = SyncResult()
        result.database_name = qualified_name
        result.errors = [f"Database '{qualified_name}' not found"]
        result.start_time = datetime.now()
        result.end_time = datetime.now()
        return result
    
    # Enhanced logging for sync parameters
    force_status = getattr(args, 'force', False)
    days_arg = getattr(args, 'days', None)
    source_days = source_spec.get("days")
    
    # Detailed log for debugging, but keep user output clean
    log_message = f"Initiating sync for database: '{qualified_name}'. Workspace: '{db_config.workspace}'. Force: {force_status}."
    
    if source_days is not None:
        log_message += f" Days from source: {source_days}."
    elif days_arg is not None:
        log_message += f" Days from --days: {days_arg}."
    else:
        log_message += " Days: not specified (will use incremental or default)."
        
    if source_spec.get("filters"):
        log_message += f" Filters from spec: {source_spec.get('filters')}."
    if db_config.property_filters:
        log_message += f" Filters from config: {db_config.property_filters}."
    logger.debug(log_message)  # Changed from info to debug level
    
    # Clean user output - start sync
    print(f"🔄 Syncing {qualified_name}...")
    
    try:
        # Create connector with appropriate credentials
        connector_config = db_config.to_dict()
        
        if db_config.source_type == 'discord':
            # Load Discord bot token from credentials file
            from promaia.utils.env_writer import get_data_dir
            config_dir = str(get_data_dir() / "credentials" / db_config.workspace)
            credentials_file = os.path.join(config_dir, "discord_credentials.json")

            if not os.path.exists(credentials_file):
                print(f"✗ {qualified_name}: Discord credentials not found for workspace '{db_config.workspace}'")
                print(f"Please run: maia workspace discord-setup {db_config.workspace}")
                # Return a synthetic result for credential errors
                from promaia.connectors.base import SyncResult
                result = SyncResult()
                result.database_name = qualified_name
                result.errors = [f"Discord credentials not found for workspace '{db_config.workspace}'"]
                result.start_time = datetime.now()
                result.end_time = datetime.now()
                return result

            try:
                with open(credentials_file, 'r') as f:
                    creds_data = json.load(f)
                connector_config['bot_token'] = creds_data.get("bot_token")
            except Exception as e:
                print(f"✗ {qualified_name}: Failed to load Discord credentials: {e}")
                from promaia.connectors.base import SyncResult
                result = SyncResult()
                result.database_name = qualified_name
                result.errors = [f"Failed to load Discord credentials: {e}"]
                result.start_time = datetime.now()
                result.end_time = datetime.now()
                return result
        elif db_config.source_type == 'conversation':
            # Conversation connector doesn't need API key - it uses local chat history file
            # Add history_file from config if available
            from promaia.utils.env_writer import get_data_dir
            default_history = str(get_data_dir() / ".maia_chat_history.json")
            if hasattr(db_config, 'auth') and isinstance(db_config.auth, dict):
                connector_config['history_file'] = db_config.auth.get('history_file', default_history)
            elif 'history_file' in connector_config:
                # Already in connector_config from db_config.to_dict()
                pass
            else:
                connector_config['history_file'] = default_history
        elif db_config.source_type == 'slack':
            # Slack bot token from environment
            bot_token = os.environ.get("SLACK_BOT_TOKEN")
            if bot_token:
                connector_config['bot_token'] = bot_token
            # SlackConnector also checks SLACK_BOT_TOKEN env fallback in __init__
        elif db_config.source_type == 'shopify':
            # Shopify credentials from maia-data/.env
            connector_config['client_id'] = os.environ.get("SHOPIFY_CLIENT_ID", "")
            connector_config['client_secret'] = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
        elif db_config.source_type in ('google_calendar', 'google_sheets', 'gmail'):
            # Google-based connectors handle their own auth via get_integration("google")
            pass
        else:
            # Get credentials for Notion and other connectors.
            from promaia.auth import get_integration
            api_key = get_integration("notion").get_notion_credentials(db_config.workspace)

            if not api_key:
                print(f"✗ {qualified_name}: No credentials configured for workspace '{db_config.workspace}'")
                # Return a synthetic result for API key errors
                from promaia.connectors.base import SyncResult
                result = SyncResult()
                result.database_name = qualified_name
                result.errors = [f"No API key configured for workspace '{db_config.workspace}'"]
                result.start_time = datetime.now()
                result.end_time = datetime.now()
                return result

            connector_config['api_key'] = api_key
            
        
        connector = ConnectorRegistry.get_connector(db_config.source_type, connector_config)
        if not connector:
            print(f"✗ No connector available for {db_config.source_type}")
            # Return a synthetic result for connector errors
            from promaia.connectors.base import SyncResult
            result = SyncResult()
            result.database_name = qualified_name
            result.errors = [f"No connector available for {db_config.source_type}"]
            result.start_time = datetime.now()
            result.end_time = datetime.now()
            return result
        
        # Build filters from source spec and config
        filters = build_filters(source_spec, db_config)
        date_filter = build_date_filter(source_spec, db_config, args)
        
        # Extract complex filter from source spec
        complex_filter = source_spec.get('complex_filter')
        
        # Use the new unified storage system instead of old output_directory
        from promaia.storage.unified_storage import get_unified_storage
        storage = get_unified_storage()

        # Build sync arguments - properties_only only supported by Notion connector
        sync_args = {
            'storage': storage,
            'db_config': db_config,
            'filters': filters,
            'date_filter': date_filter if date_filter else None,
            'include_properties': db_config.include_properties,
            'force_update': getattr(args, 'force', False),
            'excluded_properties': db_config.excluded_properties,
            'complex_filter': complex_filter
        }

        # Only pass properties_only to Notion connectors
        if db_config.source_type == 'notion':
            sync_args['properties_only'] = getattr(args, 'properties_only', False)

        # Perform sync using unified storage
        result = await connector.sync_to_local_unified(**sync_args)
        
        # Ensure database name is set in result
        if not result.database_name:
            result.database_name = qualified_name
        
        # Update last sync time on successful sync
        # Update sync time if sync completed successfully (even if no new/updated pages found)
        if not result.errors or result.pages_saved > 0 or result.pages_skipped > 0:
            # Update the last sync time in the database config
            db_config.last_sync_time = now_utc().isoformat()
            # Use targeted save to avoid overwriting concurrent config changes
            # (e.g., channel edits from another process)
            db_manager.save_database_field(db_config, "last_sync_time")

        # MONITORING: Report results with performance metrics
        duration = result.duration_seconds
        duration_str = f" in {duration:.1f}s" if duration else ""

        # Build status string with deletion count if applicable
        status_parts = [f"{result.pages_saved} saved", f"{result.pages_skipped} skipped"]
        if hasattr(result, 'pages_deleted') and result.pages_deleted > 0:
            status_parts.append(f"{result.pages_deleted} deleted")
        status_str = ", ".join(status_parts)

        print(f"✅ {qualified_name}: {status_str}{duration_str}")
        # MONITORING: Display performance metrics
        if hasattr(result, 'api_calls_count') and result.api_calls_count > 0:
            print(f"  API calls: {result.api_calls_count}")
            if result.api_rate_limit_hits > 0:
                print(f"  Rate limit hits: {result.api_rate_limit_hits}")
            if result.api_errors_count > 0:
                print(f"  API errors: {result.api_errors_count}")
        
        if result.errors:
            print(f"  Errors: {len(result.errors)}")
            for error in result.errors[:3]:  # Show first 3 errors
                print(f"    - {error}")
        
        return result
        
    except Exception as e:
        # MONITORING: Include timing even for failed syncs
        duration = (datetime.now() - (result.start_time if 'result' in locals() else datetime.now())).total_seconds()
        duration_str = f" (failed after {duration:.1f}s)" if duration > 0 else ""
        print(f"✗ {qualified_name}: Sync failed{duration_str} - {e}")
        
        # Return a synthetic result for general errors
        from promaia.connectors.base import SyncResult
        error_result = SyncResult()
        error_result.database_name = qualified_name
        error_result.errors = [f"Sync failed: {e}"]
        error_result.start_time = datetime.now()
        error_result.end_time = datetime.now()
        return error_result

def display_sync_summary(sync_results: List, overall_duration: float):
    """Display a comprehensive summary of all database sync results."""
    from promaia.connectors.base import SyncResult
    from promaia.utils.display import print_text, print_markdown
    from promaia.utils.notifications import send_sync_complete_notification
    
    # Separate successful results from exceptions
    successful_results = []
    failed_results = []
    
    for result in sync_results:
        if isinstance(result, Exception):
            # Handle exceptions that occurred during sync
            failed_results.append({
                'database_name': 'Unknown',
                'error': str(result),
                'duration': 0
            })
        elif isinstance(result, SyncResult):
            if result.errors:
                failed_results.append({
                    'database_name': result.database_name or 'Unknown',
                    'error': '; '.join(result.errors),
                    'duration': result.duration_seconds or 0
                })
            else:
                successful_results.append(result)
        else:
            # Unexpected result type
            failed_results.append({
                'database_name': 'Unknown',
                'error': f'Unexpected result type: {type(result)}',
                'duration': 0
            })
    
    # Calculate summary stats
    total_databases = len(successful_results) + len(failed_results)
    success_rate = (len(successful_results) / total_databases * 100) if total_databases > 0 else 0
    
    # Send system notification for sync completion
    if total_databases > 0:
        send_sync_complete_notification(
            success_count=len(successful_results),
            failed_count=len(failed_results),
            duration=overall_duration
        )
    
    # Header
    print("🔄 DATABASE SYNC SUMMARY")
    print("─" * 30)
    # Display successful syncs
    if successful_results:
        print(f"✅ SUCCESSFUL SYNCS ({len(successful_results)} databases)")
        total_saved = 0
        total_skipped = 0
        total_deleted = 0
        total_api_calls = 0

        for result in successful_results:
            # Format timing
            duration_str = f"{result.duration_seconds:.1f}s" if result.duration_seconds else "0.0s"

            # Format counters with colors
            saved_color = "green" if result.pages_saved > 0 else "dim"
            skipped_color = "yellow" if result.pages_skipped > 0 else "dim"

            saved_str = f"{result.pages_saved} saved"
            skipped_str = f"{result.pages_skipped} skipped"

            # Add deleted count if present
            deleted_str = ""
            if hasattr(result, 'pages_deleted') and result.pages_deleted > 0:
                deleted_str = f" • 🗑️ {result.pages_deleted} deleted"
                total_deleted += result.pages_deleted

            # Database name with formatting
            db_name = result.database_name
            if '.' in db_name:
                workspace, name = db_name.split('.', 1)
                db_display = f"{workspace}.{name}"
            else:
                db_display = f"{db_name}"

            api_calls_str = ""
            if hasattr(result, 'api_calls_count') and result.api_calls_count > 0:
                api_calls_str = f" • 🌐 {result.api_calls_count} API calls"
                total_api_calls += result.api_calls_count

            print(f"  📊 {db_display} • 💾 {saved_str} • {skipped_str}{deleted_str} • ⏱️ {duration_str}{api_calls_str}")

            total_saved += result.pages_saved
            total_skipped += result.pages_skipped

        # Totals section
        print("📈 TOTALS")
        totals_parts = [f"💾 {total_saved} saved", f"⏭️ {total_skipped} skipped"]
        if total_deleted > 0:
            totals_parts.append(f"🗑️ {total_deleted} deleted")
        if total_api_calls > 0:
            totals_parts.append(f"🌐 {total_api_calls} API calls")
        print(f"   {' • '.join(totals_parts)}")
    
    # Display failed syncs
    if failed_results:
        print(f"❌ FAILED SYNCS ({len(failed_results)} databases)")
        for failure in failed_results:
            duration_str = f" ({failure['duration']:.1f}s)" if failure['duration'] > 0 else ""
            
            db_name = failure['database_name']
            if '.' in db_name:
                workspace, name = db_name.split('.', 1) 
                db_display = f"{workspace}.{name}"
            else:
                db_display = db_name
                
            error_msg = failure['error']
            print(f"  ⚠️  {db_display}{duration_str} • {error_msg}")
            
            # Add helpful hints for common errors
            if "Google not configured" in error_msg or "Token has expired" in error_msg:
                print("      💡 Run: maia auth configure google")
            elif "Discord credentials not found" in error_msg:
                print(f"      💡 Run: maia workspace discord-setup {workspace if '.' in db_name else 'koii'}")
    # Overall summary
    print("🎯 OVERALL RESULTS")
    
    # Success rate with color coding
    if success_rate == 100:
        rate_emoji = "🎉"
    elif success_rate >= 80:
        rate_emoji = "✅"
    else:
        rate_emoji = "⚠️"
    
    print(f"   {rate_emoji} {len(successful_results)}/{total_databases} databases synced ({success_rate:.1f}%) • ⏱️ {overall_duration:.1f}s")
    print("─" * 30)


async def handle_database_sync_with_browse(args):
    """Handle database sync with Discord channel browser (combining regular sources with Discord selection)."""
    import asyncio
    import sys
    from promaia.cli.discord_commands import handle_discord_browse_filtered
    from promaia.config.workspaces import get_workspace_manager
    
    try:
        # Get workspace
        workspace_manager = get_workspace_manager()
        original_workspace = getattr(args, 'workspace', None)
        
        # Resolve workspace (same logic as chat)
        resolved_workspace = original_workspace
        sources = getattr(args, 'sources', None) or []
        
        # Get browse databases
        browse_databases = getattr(args, 'browse', [])
        
        # Workspace inference logic (same as chat)
        if not resolved_workspace and sources:
            for source in sources:
                if '.' in source:
                    inferred_workspace = source.split('.')[0]
                    if workspace_manager.validate_workspace(inferred_workspace):
                        resolved_workspace = inferred_workspace
                        print(f"INFO: Inferred workspace '{resolved_workspace}' from source '{source}'.")
                        break
        
        # If still no workspace from sources, try to infer from browse databases
        if not resolved_workspace and browse_databases:
            for browse_db in browse_databases:
                # Extract database name from potential database:days format
                db_name = browse_db.split(':')[0] if ':' in browse_db else browse_db
                if '.' in db_name:
                    inferred_workspace = db_name.split('.')[0]
                    if workspace_manager.validate_workspace(inferred_workspace):
                        resolved_workspace = inferred_workspace
                        print(f"INFO: Inferred workspace '{resolved_workspace}' from browse database '{browse_db}'.")
                        break
        
        # If still no workspace, use the default
        if not resolved_workspace:
            resolved_workspace = workspace_manager.get_default_workspace()
            if not resolved_workspace:
                print("No workspace specified, none could be inferred, and no default workspace is configured.", file=sys.stderr)
                return
        
        # Validate workspace
        if not workspace_manager.validate_workspace(resolved_workspace):
            print(f"✗ Workspace '{resolved_workspace}' is not properly configured.", file=sys.stderr)
            return
        
        # Parse browse databases to extract database names and day specifications
        parsed_browse_databases = []
        database_days = {}  # Map database -> days
        
        if browse_databases:
            for browse_spec in browse_databases:
                if ':' in browse_spec:
                    # Format: database:days
                    db_name, days_str = browse_spec.rsplit(':', 1)
                    try:
                        days = int(days_str)
                        parsed_browse_databases.append(db_name)
                        database_days[db_name] = days
                    except ValueError:
                        # If days_str is not a number, treat whole thing as database name
                        parsed_browse_databases.append(browse_spec)
                else:
                    # Just database name, no days specified
                    parsed_browse_databases.append(browse_spec)
        
        # Create args for Discord browse
        class BrowseArgs:
            def __init__(self, workspace, databases=None, database_days=None):
                self.workspace = workspace
                self.databases = databases  # Specific databases to browse, or None for all
                self.database_days = database_days or {}  # Map of database -> days
        
        browse_args = BrowseArgs(resolved_workspace, parsed_browse_databases if parsed_browse_databases else None, database_days)
        
        # Show what we're browsing
        if parsed_browse_databases:
            db_display = []
            for db in parsed_browse_databases:
                if db in database_days:
                    db_display.append(f"{db}:{database_days[db]}")
                else:
                    db_display.append(db)
            print(f"🎮 Launching Discord channel browser for sync - databases: {', '.join(db_display)}...")
        else:
            print(f"🎮 Launching Discord channel browser for sync - workspace '{resolved_workspace}'...")
        
        # Run the Discord browser and get selected channels
        selected_channels = await handle_discord_browse_filtered(browse_args)
        
        if not selected_channels:
            print("ℹ️  No channels selected. Proceeding with regular sources only.")
            # Continue with just the regular sources
            sync_sources = sources
        else:
            print(f"✅ Selected {len(selected_channels)} Discord channels for sync:")
            
            # Convert selected channels to sync source format
            # Group channels by database to avoid redundant syncs
            discord_sources = []
            channels_by_db = {}
            
            # Group channels by database
            for db_name, channel_id, channel_name, days in selected_channels:
                if db_name not in channels_by_db:
                    channels_by_db[db_name] = {'days': days, 'channels': []}
                channels_by_db[db_name]['channels'].append(channel_name)
                
                # Show what was selected
                print(f"   • {db_name}:{days} → #{channel_name}")
            
            # Create consolidated source specs for each database
            for db_name, info in channels_by_db.items():
                days = info['days']
                channels = info['channels']
                
                if len(channels) == 1:
                    # Single channel - simple filter
                    source_spec = f'{db_name}:{days}:"channel_name={channels[0]}"'
                    discord_sources.append(source_spec)
                else:
                    # Multiple channels - use complex filter format
                    # Create OR expression for multiple channels
                    channel_conditions = []
                    for channel in channels:
                        channel_conditions.append(f'discord_channel_name={channel}')
                    complex_expr = ' or '.join(channel_conditions)
                    source_spec = f'{db_name}:{days}:({complex_expr})'
                    discord_sources.append(source_spec)
            
            # Combine regular sources with Discord sources
            sync_sources = sources + discord_sources
        
        if sync_sources:
            print(f"\n🚀 Starting sync with combined sources:")
            print(f"   Regular sources: {sources if sources else 'None'}")
            if 'discord_sources' in locals() and discord_sources:
                print(f"   Discord sources: {discord_sources}")
            else:
                print(f"   Discord sources: None")
        
        # Create modified args for sync (without browse to avoid recursion)
        class SyncArgs:
            def __init__(self, sources, original_args):
                self.sources = sources
                self.workspace = original_workspace
                self.days = getattr(original_args, 'days', None)
                self.force = getattr(original_args, 'force', False)
                self.start_date = getattr(original_args, 'start_date', None)
                self.end_date = getattr(original_args, 'end_date', None)
                self.date_range = getattr(original_args, 'date_range', None)
                self.browse = None  # Clear browse to avoid recursion
        
        sync_args = SyncArgs(sync_sources, args)
        
        # Start sync with combined sources
        await handle_database_sync(sync_args)
        
    except Exception as e:
        print(f"❌ Error in sync with browse: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()


def parse_source_specs(source_specs: List[str]) -> List[Dict[str, Any]]:
    """
    Parse source specifications with support for property filtering.

    Formats supported:
    - database_name
    - database_name:days (e.g., 'journal:7')
    - database_name:all (e.g., 'cms:all')
    - database_name.property=value (e.g., 'cms.Reference=true')
    - database_name:days.property=value (e.g., 'cms:30.KOii_chat=true')
    - database_name#channel:days (e.g., 'trass.tg#koii-work:7')

    Args:
        source_specs: List of source specification strings

    Returns:
        List of parsed source configurations
    """
    parsed_sources = []

    # Get database manager to check for existing databases
    db_manager = get_database_manager()

    # Log timezone information for debugging
    log_timezone_info()

    for spec in source_specs:
        try:
            # Initialize with defaults
            database = None
            days = None
            days_was_specified = False
            property_filters = {}
            comparison_filters = {}
            complex_filter = None  # New: store complex filter expressions

            # Handle Discord channel format: database#channel:days
            # This must be processed before splitting on ':' to extract the database name correctly
            discord_channel_name = None
            if '#' in spec:
                # Split on '#' to separate database from channel
                before_hash = spec.split('#', 1)[0]
                after_hash = spec.split('#', 1)[1]

                # Extract channel name (everything between # and : or end of string)
                if ':' in after_hash:
                    discord_channel_name = after_hash.split(':', 1)[0]
                    # Reconstruct spec without the #channel part for normal parsing
                    # e.g., 'trass.tg#koii-work:7' becomes 'trass.tg:7'
                    spec = before_hash + ':' + after_hash.split(':', 1)[1]
                else:
                    discord_channel_name = after_hash
                    # e.g., 'trass.tg#koii-work' becomes 'trass.tg'
                    spec = before_hash

            # Logic to separate database name from days/filters
            spec_parts = spec.split(':', 1)
            db_spec_part = spec_parts[0]

            db_config = db_manager.get_database_by_qualified_name(db_spec_part)
            if not db_config:
                logger.warning(f"Database '{db_spec_part}' not found in configuration. Skipping.")
                continue

            # If we extracted a Discord channel name, add it as a property filter
            if discord_channel_name:
                property_filters['discord_channel_name'] = discord_channel_name
            
            database = db_config.get_qualified_name()
            
            if len(spec_parts) > 1:
                days_and_filters_part = spec_parts[1]
                days_was_specified = True
                
                # Handle complex filter format: days:(expression)
                if days_and_filters_part.count(':') == 1 and days_and_filters_part.endswith(')') and '(' in days_and_filters_part:
                    # Complex filter format: days:(expression)
                    days_str, filter_part = days_and_filters_part.split(':', 1)
                    
                    # Parse days
                    if days_str.lower() == 'all':
                        days = None
                    else:
                        try:
                            days = int(days_str)
                        except ValueError:
                            logger.warning(f"Invalid days format '{days_str}' in spec '{spec}', using default from config.")
                            days = db_config.default_days
                            days_was_specified = False
                    
                    # Parse complex expression: (discord_channel_name=a or discord_channel_name=b)
                    if filter_part.startswith('(') and filter_part.endswith(')'):
                        complex_expr = filter_part[1:-1]  # Remove parentheses
                        from promaia.cli.database_commands import parse_complex_filter_expression
                        try:
                            complex_filter = parse_complex_filter_expression(complex_expr)
                            logger.info(f"Parsed complex filter for '{database}': {complex_filter}")
                        except Exception as e:
                            logger.warning(f"Failed to parse complex filter '{complex_expr}' in spec '{spec}': {e}")
                    
                    # Set parts to empty for complex filters (no additional processing needed)
                    parts = [days_str]  # Single element list so len(parts) == 1
                
                # Handle Discord channel filter format: days:"channel_name=value"
                elif days_and_filters_part.count(':') == 1 and '"' in days_and_filters_part:
                    # Discord channel filter format: days:"filter"
                    days_str, filter_part = days_and_filters_part.split(':', 1)
                    
                    # Parse days
                    if days_str.lower() == 'all':
                        days = None
                    else:
                        try:
                            days = int(days_str)
                        except ValueError:
                            logger.warning(f"Invalid days format '{days_str}' in spec '{spec}', using default from config.")
                            days = db_config.default_days
                            days_was_specified = False
                    
                    # Parse Discord channel filter: "channel_name=value"
                    if filter_part.startswith('"') and filter_part.endswith('"') and '=' in filter_part:
                        # Extract content within quotes: "channel_name=value" -> channel_name=value
                        inner_content = filter_part[1:-1]  # Remove surrounding quotes
                        if '=' in inner_content:
                            prop_name, prop_value = inner_content.split('=', 1)
                            property_filters[prop_name] = prop_value
                        else:
                            logger.warning(f"Invalid Discord filter format '{filter_part}' in spec '{spec}' - no = found")
                    else:
                        logger.warning(f"Invalid Discord filter format '{filter_part}' in spec '{spec}' - not properly quoted")
                    
                    # Set parts to empty for Discord filters (no additional processing needed)
                    parts = [days_str]  # Single element list so len(parts) == 1
                        
                else:
                    # Regular format: days.property=value
                    parts = days_and_filters_part.split('.', 1)
                    days_str = parts[0]
                    
                    if days_str.lower() == 'all':
                        days = None  # None means all, respecting the existing convention
                    else:
                        try:
                            days = int(days_str)
                        except ValueError:
                            logger.warning(f"Invalid days format '{days_str}' in spec '{spec}', using default from config.")
                            days = db_config.default_days
                            days_was_specified = False # Treat as unspecified if format is bad

                if len(parts) > 1:
                    filter_parts_str = parts[1]
                    for filter_part in filter_parts_str.split('.'):
                        # Check for complex expression
                        if filter_part.startswith('__COMPLEX_EXPR__'):
                            # Extract the actual expression (remove the prefix)
                            complex_expr = filter_part[16:]  # Remove '__COMPLEX_EXPR__' prefix (16 chars)
                            complex_filter = parse_complex_filter_expression(complex_expr)
                        elif '=' in filter_part:
                            prop_name, prop_value = filter_part.split('=', 1)
                            prop_name = prop_name.strip()
                            prop_value = prop_value.strip()
                            
                            # Handle quoted property names and values
                            if prop_name.startswith('"') and prop_value.endswith('"'):
                                prop_name = prop_name[1:]  # Remove leading quote
                                prop_value = prop_value[:-1]  # Remove trailing quote
                            
                            # Handle comparison filters (_after, _before)
                            if prop_name.endswith('_after') or prop_name.endswith('_before'):
                                # Store multiple values for the same filter key in a list
                                if prop_name not in comparison_filters:
                                    comparison_filters[prop_name] = []
                                comparison_filters[prop_name].append(prop_value)
                            else:
                                # Handle regular property filters
                                # Convert string values to appropriate types
                                if prop_value.lower() == 'true':
                                    prop_value = True
                                elif prop_value.lower() == 'false':
                                    prop_value = False
                                elif prop_value.isdigit():
                                    prop_value = int(prop_value)
                                else:
                                    # Special case: don't convert underscores for page_id and Discord properties
                                    discord_properties = ['channel_name', 'data_source', 'page_id']
                                    if prop_name not in discord_properties:
                                        prop_name = prop_name.replace('_', ' ')
                                property_filters[prop_name] = prop_value
                        else:
                             logger.warning(f"Invalid property filter format '{filter_part}' in spec '{spec}'")

            # If days were not specified in the spec string, use the default from the config
            # Property filters should work TOGETHER with date constraints
            if not days_was_specified:
                days = db_config.default_days

            parsed_source = {
                'name': database,
                'database': database,
                'qualified_name': database,
                'days': days,
                'property_filters': property_filters,
                'comparison_filters': comparison_filters,
                'complex_filter': complex_filter  # New: include complex filter
            }
            
            parsed_sources.append(parsed_source)
            
            if complex_filter:
                days_desc = "all" if days is None else days
                logger.info(f"Parsed source: {database}, days: {days_desc}, complex_filter: {complex_filter}")
            else:
                days_desc = "all" if days is None else days
                logger.info(f"Parsed source: {database}, days: {days_desc}, filters: {property_filters}, comparison_filters: {comparison_filters}")
            
        except Exception as e:
            logger.error(f"Error parsing source spec '{spec}': {e}")
            continue
    
    return parsed_sources

def parse_filter_expression(filter_expr: str) -> Dict[str, Any]:
    """
    Parse a single filter expression and convert it to a format that includes source information.
    
    Now supports source-specific filtering:
        'cms:"Reference"=true' -> {'source': 'cms', 'filter': '"Reference"=true'}
        'journal:created_time>2025-01-01' -> {'source': 'journal', 'filter': 'created_time_after=2025-01-01'}
    
    And complex expressions within a single source:
        'cms:"Reference"=true and "Blog status"=live' -> {'source': 'cms', 'filter': '__COMPLEX_EXPR__"Reference"=true and "Blog status"=live'}
    
    Simple expressions without source prefix (backward compatibility):
        'status=published' -> {'source': None, 'filter': 'status=published'}
    
    Args:
        filter_expr: A filter expression string
        
    Returns:
        Dictionary with 'source' and 'filter' keys, or converted filter expression for backward compatibility
    """
    filter_expr = filter_expr.strip()
    
    # Check for source prefix (source:filter_expression or source.filter_expression)
    # But exclude global contains:"..." syntax which doesn't have a source prefix
    # Updated regex to handle day specifications in source names (e.g., trass.yeeps_discord:30, trass.yp:all)
    # Also handle period separator for simple filters (e.g., trass.yp:14.discord_channel_name=value)
    source_match = re.match(r'^([a-zA-Z0-9_.-]+(?::[0-9]+|:all)?)[:.](.+)$', filter_expr)
    if source_match and not (filter_expr.startswith('contains:"') and ':' not in filter_expr[9:]):
        source = source_match.group(1)
        filter_part = source_match.group(2)
        
        # Check if this is a complex expression with 'or', 'and', or parentheses
        if (' or ' in filter_part.lower() or ' and ' in filter_part.lower() or 
            (filter_part.startswith('(') and filter_part.endswith(')'))):
            # Return source-specific complex filter
            return {'source': source, 'filter': f"__COMPLEX_EXPR__{filter_part}"}
        
        # Check for contains:"search term" syntax in source-specific filters
        if filter_part.startswith('contains:"') and filter_part.endswith('"'):
            # This is a complex filter since it uses the 'contains' operator
            return {'source': source, 'filter': f"__COMPLEX_EXPR__{filter_part}"}
        
        # Check for simplified quoted search syntax: source:"search term"
        if filter_part.startswith('"') and filter_part.endswith('"') and '=' not in filter_part and '>' not in filter_part and '<' not in filter_part:
            # Convert to contains syntax: "search term" -> contains:"search term"
            search_term = filter_part[1:-1]  # Remove quotes
            return {'source': source, 'filter': f"__COMPLEX_EXPR__contains:\"{search_term}\""}
        
        # Handle simple comparison operators
        if '>' in filter_part:
            prop, value = filter_part.split('>', 1)
            converted_filter = f"{prop.strip()}_after={value.strip()}"
        elif '<' in filter_part:
            prop, value = filter_part.split('<', 1)
            converted_filter = f"{prop.strip()}_before={value.strip()}"
        elif '=' in filter_part:
            # Direct equality - no conversion needed
            converted_filter = filter_part
        else:
            # Invalid format
            raise ValueError(f"Invalid filter format: '{filter_part}'. Use 'property=value', 'property>value', 'property<value', '\"search term\"', or contains:\"search term\"")
        
        return {'source': source, 'filter': converted_filter}
    
    # No source prefix - handle as before for backward compatibility
    # Check for contains:"search term" syntax first
    if filter_expr.startswith('contains:"') and filter_expr.endswith('"'):
        # This is a complex filter since it uses the 'contains' operator
        return f"__COMPLEX_EXPR__{filter_expr}"
    
    # Check for simplified quoted search syntax: "search term"
    if filter_expr.startswith('"') and filter_expr.endswith('"') and '=' not in filter_expr and '>' not in filter_expr and '<' not in filter_expr:
        # Convert to contains syntax: "search term" -> contains:"search term"
        search_term = filter_expr[1:-1]  # Remove quotes
        return f"__COMPLEX_EXPR__contains:\"{search_term}\""
    
    # Check if this is a complex expression with 'or' or 'and'
    if ' or ' in filter_expr.lower() or ' and ' in filter_expr.lower():
        # Return a special marker to indicate this needs complex parsing
        return f"__COMPLEX_EXPR__{filter_expr}"
    
    # Handle simple comparison operators (existing logic)
    if '>' in filter_expr:
        prop, value = filter_expr.split('>', 1)
        return f"{prop.strip()}_after={value.strip()}"
    elif '<' in filter_expr:
        prop, value = filter_expr.split('<', 1)
        return f"{prop.strip()}_before={value.strip()}"
    elif '=' in filter_expr:
        # Direct equality - no conversion needed
        return filter_expr
    else:
        # Invalid format
        raise ValueError(f"Invalid filter format: '{filter_expr}'. Use 'property=value', 'property>value', 'property<value', '\"search term\"', or complex expressions with 'and'/'or', or contains:\"search term\"")


def parse_complex_filter_expression(expr: str) -> Dict[str, Any]:
    """
    Parse a complex filter expression with 'or' and 'and' operators.
    
    Examples:
        "created_time<2024-12-30 or created_time>2025-06-30"
        "status=published and created_time>2025-01-01" 
        "created_time<2024-12-30 or created_time>2025-06-30 and status=planned"
        "(channel_name=announcements or channel_name=release-notes)"
    
    Returns:
        Dictionary with parsed conditions and operators for SQL generation
    """
    from typing import List, Union
    
    # Handle parentheses - remove outer parentheses if present
    expr = expr.strip()
    if expr.startswith('(') and expr.endswith(')'):
        expr = expr[1:-1].strip()
    
    # Split on 'or' first (lowest precedence)
    or_clauses = []
    for or_part in expr.split(' or '):
        or_part = or_part.strip()
        
        # Split each OR clause on 'and' (higher precedence)
        and_conditions = []
        for and_part in or_part.split(' and '):
            and_part = and_part.strip()
            
            # Parse individual condition
            condition = parse_single_condition(and_part)
            and_conditions.append(condition)
        
        or_clauses.append(and_conditions)
    
    return {
        'type': 'complex',
        'or_clauses': or_clauses  # List of lists: [[and_conds], [and_conds], ...]
    }


def parse_single_condition(condition: str) -> Dict[str, str]:
    """
    Parse a single condition like 'created_time<2024-12-30' or 'status=published'.
    Now supports quoted property names like '"Reference"=true' and '"Blog status"=live'.
    Also supports contains:"search term" for full content search.
    
    Returns:
        Dictionary with property, operator, and value
    """
    condition = condition.strip()
    
    # Handle contains:"search term" syntax for full content search
    contains_match = re.match(r'^contains:"([^"]*)"$', condition)
    if contains_match:
        search_term = contains_match.group(1)
        return {'property': '_content', 'operator': 'contains', 'value': search_term}
    
    # Handle simplified quoted search syntax: "search term"
    if condition.startswith('"') and condition.endswith('"') and '=' not in condition and '>' not in condition and '<' not in condition:
        search_term = condition[1:-1]  # Remove quotes
        return {'property': '_content', 'operator': 'contains', 'value': search_term}
    
    # Handle quoted property names
    # Look for patterns like "Property Name"=value or "Property Name">value
    quoted_prop_match = re.match(r'^"([^"]+)"([><=]+)(.*)$', condition)
    if quoted_prop_match:
        prop = quoted_prop_match.group(1)
        operator = quoted_prop_match.group(2)
        value = quoted_prop_match.group(3).strip()
        return {'property': prop, 'operator': operator, 'value': value}
    
    # Handle unquoted property names (existing logic)
    if '>=' in condition:
        prop, value = condition.split('>=', 1)
        return {'property': prop.strip(), 'operator': '>=', 'value': value.strip()}
    elif '<=' in condition:
        prop, value = condition.split('<=', 1)
        return {'property': prop.strip(), 'operator': '<=', 'value': value.strip()}
    elif '>' in condition:
        prop, value = condition.split('>', 1)
        return {'property': prop.strip(), 'operator': '>', 'value': value.strip()}
    elif '<' in condition:
        prop, value = condition.split('<', 1)
        return {'property': prop.strip(), 'operator': '<', 'value': value.strip()}
    elif '=' in condition:
        prop, value = condition.split('=', 1)
        return {'property': prop.strip(), 'operator': '=', 'value': value.strip()}
    else:
        raise ValueError(f"Invalid condition format: '{condition}'. Use 'property=value', 'property>value', 'property<value', '\"search term\"', or contains:\"search term\"")


def build_sql_from_complex_filter(complex_filter: Dict[str, Any], date_filter_prop: str) -> tuple[str, List[str]]:
    """
    Build SQL WHERE clause and parameters from a complex filter expression.
    
    Args:
        complex_filter: Parsed complex filter from parse_complex_filter_expression
        date_filter_prop: The date property name to use for date comparisons (fallback)
        
    Returns:
        Tuple of (sql_where_clause, parameters_list)
    """
    if complex_filter['type'] != 'complex':
        raise ValueError("Expected complex filter type")
    
    or_clauses_sql = []
    params = []
    
    for and_conditions in complex_filter['or_clauses']:
        and_clauses_sql = []
        
        for condition in and_conditions:
            prop = condition['property']
            op = condition['operator']
            value = condition['value']
            
            # Handle date properties - use the actual property specified in the filter
            if prop in ['created_time', 'last_edited_time']:
                # Use the property specified in the filter, not the default
                actual_date_prop = prop
                if op == '>':
                    and_clauses_sql.append(f"datetime({actual_date_prop}) > datetime(?)")
                elif op == '>=':
                    and_clauses_sql.append(f"datetime({actual_date_prop}) >= datetime(?)")
                elif op == '<':
                    and_clauses_sql.append(f"datetime({actual_date_prop}) < datetime(?)")
                elif op == '<=':
                    and_clauses_sql.append(f"datetime({actual_date_prop}) <= datetime(?)")
                elif op == '=':
                    and_clauses_sql.append(f"date({actual_date_prop}) = date(?)")
                params.append(value)
            elif prop == date_filter_prop:
                # Handle case where filter uses the configured date property
                if op == '>':
                    and_clauses_sql.append(f"datetime({date_filter_prop}) > datetime(?)")
                elif op == '>=':
                    and_clauses_sql.append(f"datetime({date_filter_prop}) >= datetime(?)")
                elif op == '<':
                    and_clauses_sql.append(f"datetime({date_filter_prop}) < datetime(?)")
                elif op == '<=':
                    and_clauses_sql.append(f"datetime({date_filter_prop}) <= datetime(?)")
                elif op == '=':
                    and_clauses_sql.append(f"date({date_filter_prop}) = date(?)")
                params.append(value)
            else:
                # Handle regular properties (these would need to be in JSON metadata)
                # For now, we'll note that these can't be filtered at SQL level
                # and need to be handled post-query
                and_clauses_sql.append("1=1")  # Always true, will filter later
                # We'll store these for post-SQL filtering
        
        if and_clauses_sql:
            or_clauses_sql.append(f"({' AND '.join(and_clauses_sql)})")
    
    if or_clauses_sql:
        where_clause = f"({' OR '.join(or_clauses_sql)})"
        return where_clause, params
    else:
        return "", []

def parse_filter_string(filter_str: str) -> Dict[str, Any]:
    """Parse filter string like 'date>-30d,status=published'."""
    filters = {}
    
    for filter_expr in filter_str.split(','):
        filter_expr = filter_expr.strip()
        
        # Handle date filters
        if '>' in filter_expr or '<' in filter_expr:
            if '>' in filter_expr:
                prop, value = filter_expr.split('>', 1)
                filters[f"{prop.strip()}_after"] = value.strip()
            elif '<' in filter_expr:
                prop, value = filter_expr.split('<', 1)
                filters[f"{prop.strip()}_before"] = value.strip()
        
        # Handle equality filters
        elif '=' in filter_expr:
            prop, value = filter_expr.split('=', 1)
            filters[prop.strip()] = value.strip()
    
    return filters

def build_filters(source_spec: Dict[str, Any], db_config) -> List[QueryFilter]:
    """Build QueryFilter objects from source specification and database config."""
    from promaia.storage.property_resolver import PropertyResolver

    filters = []
    resolver = PropertyResolver()
    database_id = db_config.database_id

    # Add filters from source specification
    logger.debug(f"Source spec property_filters: {source_spec.get('property_filters', {})}")
    for key, value in source_spec.get("property_filters", {}).items():
        if not key.endswith(('_after', '_before')):  # Skip date filters
            logger.debug(f"Adding source filter: {key} = {value}")
            filters.append(QueryFilter(key, "eq", value))

    # Add filters from database configuration with ID resolution
    # Keys that are config-level settings, not actual property filters
    CONFIG_KEYS = {'ocr_channels', 'channel_days', 'annotation_window_seconds', 'channel_names'}
    logger.debug(f"Database config property_filters: {db_config.property_filters}")
    for prop_key, prop_values in db_config.property_filters.items():
        if prop_key in CONFIG_KEYS:
            continue
        # Determine if this is an ID or a name (IDs typically don't contain spaces and have specific patterns)
        # Simple heuristic: if it looks like a Notion property ID, resolve it
        is_property_id = not ' ' in prop_key and len(prop_key) > 10

        if is_property_id:
            # Resolve property ID to current name
            resolved_name, resolved_values = resolver.resolve_filter_value(database_id, prop_key, prop_values)
            if resolved_name and resolved_values:
                if isinstance(resolved_values, list):
                    logger.debug(f"Resolved filter: {prop_key} -> {resolved_name} in {resolved_values}")
                    filters.append(QueryFilter(resolved_name, "in", resolved_values))
                else:
                    logger.debug(f"Resolved filter: {prop_key} -> {resolved_name} = {resolved_values}")
                    filters.append(QueryFilter(resolved_name, "eq", resolved_values))
            else:
                logger.warning(f"Could not resolve property ID {prop_key}, skipping filter")
        else:
            # Use name directly (backward compatibility)
            if isinstance(prop_values, list):
                logger.debug(f"Adding config filter: {prop_key} in {prop_values}")
                filters.append(QueryFilter(prop_key, "in", prop_values))
            else:
                logger.debug(f"Adding config filter: {prop_key} = {prop_values}")
                filters.append(QueryFilter(prop_key, "eq", prop_values))

    logger.debug(f"Final filters: {[(f.property_name, f.operator, f.value) for f in filters]}")
    return filters

def build_date_filter(source_spec: Dict[str, Any], db_config, args) -> Optional[DateRangeFilter]:
    """Build DateRangeFilter from source specification and arguments."""
    force_sync = getattr(args, 'force', False)
    
    # Check if days or date ranges were specified
    has_days_spec = source_spec.get("days") is not None
    has_days_arg = hasattr(args, 'days') and args.days is not None
    has_date_range = hasattr(args, 'date_range') and args.date_range
    has_start_date = hasattr(args, 'start_date') and args.start_date
    has_end_date = hasattr(args, 'end_date') and args.end_date
    
    # Check for date filters in source spec (e.g., database[date_after>2023-01-01])
    spec_filters = source_spec.get("filters", {})
    comparison_filters = source_spec.get("comparison_filters", {})
    all_filters = {**spec_filters, **comparison_filters}
    has_spec_date_filters = any(key.endswith('_after') or key.endswith('_before') for key in all_filters.keys())
    
    # Case 1: --force without any date/days specification (sync all, respecting other filters if any)
    if force_sync and not has_days_spec and not has_days_arg and not has_date_range and not has_start_date and not has_end_date and not has_spec_date_filters:
        # No specific date range, sync all (or based on other non-date filters)
        # If a date_prop was specified in db_config (e.g. for "Date" field), respect it for Notion query
        # otherwise, no date filter is applied here by default for --force. Connector might have its own.
        logger.debug(f"Using --force without days specification, so no date filter will be applied.")
        return None
    
    date_prop = db_config.date_filters.get("property") # Default to None, will be handled
    
    spec_start_date_str = None
    spec_end_date_str = None
    
    for key, value in all_filters.items():
        if key.endswith('_after'):
            spec_start_date_str = value
            if not date_prop: # If a date filter is in spec, use its property
                date_prop = key.replace('_after', '')
        elif key.endswith('_before'):
            spec_end_date_str = value
            if not date_prop: # If a date filter is in spec, use its property
                date_prop = key.replace('_before', '')

    start_date = parse_date_value(spec_start_date_str) if spec_start_date_str else None
    end_date = parse_date_value(spec_end_date_str) if spec_end_date_str else None

    # Handle new command-line date arguments
    if hasattr(args, 'date_range') and args.date_range:
        # Parse date range like "2025-02-01,2025-03-31"
        try:
            range_parts = args.date_range.split(',')
            if len(range_parts) == 2:
                start_date = parse_date_value(range_parts[0].strip())
                end_date = parse_date_value(range_parts[1].strip())
                if not date_prop:
                    date_prop = "created_time"  # Default for date ranges
        except Exception as e:
            logger.warning(f"Invalid date range format '{args.date_range}': {e}")
    
    # Handle individual start/end date arguments (override date_range if both specified)
    explicit_dates_provided = False
    if hasattr(args, 'start_date') and args.start_date:
        start_date = parse_date_value(args.start_date)
        explicit_dates_provided = True
        if not date_prop:
            date_prop = "created_time"
    
    if hasattr(args, 'end_date') and args.end_date:
        end_date = parse_date_value(args.end_date)
        explicit_dates_provided = True
        if not date_prop:
            date_prop = "created_time"

    # Case 2: Source-specific days (e.g., journal:30) - but only if no explicit dates provided
    if source_spec.get("days") is not None and not explicit_dates_provided:
        source_days = source_spec.get("days")
        if source_days == 'all':
            # Sync all for this source
            effective_date_prop = date_prop or db_config.date_filters.get("property", "created_time")
            logger.debug(f"Using source days 'all'. Date prop: {effective_date_prop}. No date range limit.")
            return DateRangeFilter(property_name=effective_date_prop, start_date=None, end_date=None)
        else:
            days_to_sync = int(source_days)
            effective_date_prop = "created_time" if force_sync else db_config.date_filters.get("property", "last_edited_time")
            if not date_prop: date_prop = effective_date_prop

            start_date_from_days = days_ago_utc(days_to_sync)
            if start_date and start_date > start_date_from_days:
                pass # start_date from spec is already set and more restrictive
            else:
                start_date = start_date_from_days

            logger.debug(f"Using source days ({days_to_sync}). Date prop: {date_prop}. Start: {start_date}, End: {end_date}")
            return DateRangeFilter(property_name=date_prop or "last_edited_time", start_date=start_date, end_date=end_date)

    # Case 3: --days argument is provided - but only if no explicit dates provided
    if hasattr(args, 'days') and args.days is not None and not explicit_dates_provided:
        days_to_sync = args.days if isinstance(args.days, int) else db_config.default_days
        # If --force, use created_time to get all items within the --days range
        # If not --force, it will effectively get items *created* within --days 
        # AND *modified* since last sync (Notion connector handles this with force=False)
        # However, a simpler approach for --days without --force is to get items *modified* in the last N days.
        effective_date_prop = "created_time" if force_sync else db_config.date_filters.get("property", "last_edited_time")
        if not date_prop: date_prop = effective_date_prop # Ensure date_prop is set if not from spec

        start_date_from_days = days_ago_utc(days_to_sync)
        # If start_date from spec is more recent, use it
        if start_date and start_date > start_date_from_days:
            pass # start_date from spec is already set and more restrictive
        else:
            start_date = start_date_from_days
        # end_date from spec can remain if set

        logger.debug(f"Using --days ({days_to_sync}). Date prop: {date_prop}. Start: {start_date}, End: {end_date}")
        return DateRangeFilter(property_name=date_prop or "last_edited_time", start_date=start_date, end_date=end_date)

    # Case 4: Date filters are provided in the source specification (e.g., journal[date_prop_after>2023-01-01])
    if start_date or end_date:
        if not date_prop: # Should have been set if _after or _before was found
            logger.warning("Date filter found in spec, but date property could not be determined. Defaulting to 'last_edited_time'.")
            date_prop = "last_edited_time"
        logger.debug(f"Using date filter from source spec. Date prop: {date_prop}. Start: {start_date}, End: {end_date}")
        return DateRangeFilter(property_name=date_prop, start_date=start_date, end_date=end_date)

    # Case 5: Default incremental sync (no --days, no --force, no date spec)
    # Use last_sync_time and 'last_edited_time'
    if db_config.last_sync_time:
        try:
            last_sync = datetime.fromisoformat(db_config.last_sync_time)
            # Add a small buffer to avoid precision issues with Notion's last_edited_time
            # Sync items edited strictly *after* the last sync time.
            start_date = last_sync + timedelta(seconds=1) 
            date_prop_for_incremental = "last_edited_time" # Notion's standard property
            logger.debug(f"Incremental sync. Date prop: {date_prop_for_incremental}. Start: {start_date} (from last_sync_time: {db_config.last_sync_time})")
            return DateRangeFilter(property_name=date_prop_for_incremental, start_date=start_date, end_date=None)
        except ValueError:
            logger.warning(f"Could not parse last_sync_time '{db_config.last_sync_time}'. Defaulting to full sync for relevant period or default_days.")
            # Fallback to default_days if last_sync_time is invalid
            days_to_sync = db_config.default_days
            effective_date_prop = db_config.date_filters.get("property", "last_edited_time")
            start_date = days_ago_utc(days_to_sync)
            logger.debug(f"Fallback to default_days ({days_to_sync}) due to invalid last_sync_time. Date prop: {effective_date_prop}. Start: {start_date}")
            return DateRangeFilter(property_name=effective_date_prop, start_date=start_date, end_date=None)
            
    # Case 6: Initial sync or no last_sync_time (and no --force, no --days, no date spec)
    # Sync based on default_days using 'created_time' or configured date_prop
    logger.debug(f"Initial sync or no valid last_sync_time. Using default_days: {db_config.default_days}")
    days_to_sync = db_config.default_days
    # For initial sync, it's common to get recently *created* items.
    # Or, if db_config specifies a date_filter property like "Date", use that.
    initial_sync_date_prop = db_config.date_filters.get("property", "created_time") 
    start_date = days_ago_utc(days_to_sync)
    return DateRangeFilter(property_name=initial_sync_date_prop, start_date=start_date, end_date=None)

def parse_date_value(value: str) -> Optional[datetime]:
    """Parse date value like '-30d', '2024-01-01', etc."""
    if value.startswith('-') and value.endswith('d'):
        # Relative days
        try:
            days = int(value[1:-1])
            return days_ago_utc(days)
        except ValueError:
            return None
    
    # Try parsing as ISO date
    try:
        dt = datetime.fromisoformat(value)
        # If timezone-naive, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None

async def handle_database_info(args):
    """Handle 'maia database info' command."""
    if not args.name:
        print("Database name is required")
        return
    
    db_config = get_database_config(args.name)
    if not db_config:
        print(f"Database '{args.name}' not found")
        return
    
    print(f"Database: {args.name}")
    print(f"  Type: {db_config.source_type}")
    print(f"  ID: {db_config.database_id}")
    print(f"  Description: {db_config.description}")
    print(f"  Sync enabled: {db_config.sync_enabled}")
    print(f"  Output directory: {db_config.output_directory}")
    print(f"  Default days: {db_config.default_days}")
    
    if db_config.property_filters:
        print("  Property filters:")
        for prop, values in db_config.property_filters.items():
            print(f"    {prop}: {values}")
    
    # Test connection and get schema
    try:
        connector = ConnectorRegistry.get_connector(db_config.source_type, db_config.to_dict())
        if connector:
            if await connector.test_connection():
                print("  Connection: ✓ Working")
                
                if args.schema:
                    schema = await connector.get_database_schema()
                    if schema:
                        print("  Schema:")
                        for prop_name, prop_type in schema.items():
                            print(f"    {prop_name}: {prop_type}")
            else:
                print("  Connection: ✗ Failed")
    except Exception as e:
        print(f"  Connection: ✗ Error - {e}")

async def handle_database_push(args):
    """Handle 'maia database push' command - push local markdown changes to Notion."""
    from promaia.storage.notion_push import push_database_changes
    from promaia.config.workspaces import get_workspace_manager

    # Get workspace
    workspace_manager = get_workspace_manager()
    workspace = getattr(args, 'workspace', None) or workspace_manager.get_default_workspace()

    if not workspace:
        print("❌ No workspace available. Please configure a workspace first.")
        return

    # Get database name(s)
    database = getattr(args, 'database', None)
    force = getattr(args, 'force', False)

    db_manager = get_database_manager()

    if database:
        # Push specific database
        databases = [database]
    else:
        # Push all enabled Notion databases in workspace
        databases = []
        for db_name in db_manager.list_databases(workspace=workspace):
            db_config = db_manager.get_database(db_name)
            if db_config and db_config.sync_enabled and db_config.source_type == 'notion':
                databases.append(db_config.nickname)

    if not databases:
        print(f"❌ No Notion databases found in workspace '{workspace}'")
        return

    print(f"🚀 Pushing local markdown changes to Notion")
    print(f"   Workspace: {workspace}")
    print(f"   Databases: {', '.join(databases)}")
    if force:
        print("   Mode: FORCE (push all files)")
    print()

    total_created = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0

    for db_name in databases:
        print(f"📤 Pushing {workspace}.{db_name}...")

        try:
            result = await push_database_changes(
                database_name=db_name,
                workspace=workspace,
                force=force
            )

            if result['success']:
                total_created += result['created']
                total_updated += result['updated']
                total_skipped += result['skipped']
                total_failed += result['failed']

                print(f"   ✅ Created: {result['created']}")
                print(f"   ✅ Updated: {result['updated']}")
                print(f"   ⏭️  Skipped: {result['skipped']}")
                if result['failed'] > 0:
                    print(f"   ❌ Failed: {result['failed']}")

                # Show conflicts if any
                conflicts = sum(1 for r in result.get('results', []) if r.get('status') == 'conflict')
                if conflicts > 0:
                    print(f"   ⚠️  Conflicts: {conflicts} (local and Notion both changed)")
            else:
                print(f"   ❌ Push failed: {result.get('error')}")
                total_failed += 1
        except Exception as e:
            print(f"   ❌ Error: {e}")
            total_failed += 1

        print()

    # Summary
    print("=" * 50)
    print("📊 PUSH SUMMARY")
    print(f"   Created: {total_created}")
    print(f"   Updated: {total_updated}")
    print(f"   Skipped: {total_skipped}")
    if total_failed > 0:
        print(f"   Failed: {total_failed}")
    print("=" * 50)
async def handle_database_status(args):
    """Handle 'maia database status' command - show what needs to be synced."""
    from promaia.storage.hybrid_storage import get_hybrid_registry
    from promaia.config.databases import get_database_manager
    
    target = getattr(args, 'target', 'all')
    registry = get_hybrid_registry()
    db_manager = get_database_manager()
    
    if target == 'all':
        # Get unique database names from actual content
        content_list = registry.list_content()
        content_types = list(set(item['database_name'] for item in content_list))
    else:
        content_types = [target]
    
    total_to_sync = 0
    total_synced = 0
    
    for content_type in content_types:
        print(f"\n=== {content_type} ===")
        
        # Get pages from hybrid_metadata.db for this content type
        content_list = registry.list_content(database_name=content_type)
        
        if not content_list:
            print(f"📂 No local data found")
            continue
        
        needs_sync = []
        is_synced = []
        
        for item in content_list:
            title = item.get('title', 'Unknown')
            sync_status = item.get('sync_status', 'unknown')
            
            if sync_status != 'synced':
                needs_sync.append(title)
            else:
                is_synced.append(title)
        
        if needs_sync:
            print(f"📝 Needs sync ({len(needs_sync)}):")
            for title in needs_sync[:5]:  # Show first 5
                print(f"  • {title}")
            if len(needs_sync) > 5:
                print(f"  ... and {len(needs_sync) - 5} more")
        
        if is_synced:
            print(f"✅ Synced: {len(is_synced)} pages")
        
        total_to_sync += len(needs_sync)
        total_synced += len(is_synced)
    
    print(f"\n=== OVERALL STATUS ===")
    print(f"📝 Needs sync: {total_to_sync}")
    print(f"✅ Synced: {total_synced}")
    
    if total_to_sync > 0:
        if target == 'all':
            print(f"\nTo push all changes: maia database push all")
        else:
            print(f"\nTo push changes: maia database push {target}")

async def handle_database_list_sources(args):
    """Handle 'maia database list-sources' command."""
    db_manager = get_database_manager()
    databases = db_manager.list_databases()
    
    source_names = []
    for db_name in databases:
        db_config = db_manager.get_database(db_name)
        if db_config:
            source_names.append(db_config.get_qualified_name())
            
    print(json.dumps(source_names))

async def handle_validate_registry(args):
    """Handle 'maia database validate-registry' command."""
    from promaia.config.registry_sync import get_config_registry_sync
    
    try:
        sync_manager = get_config_registry_sync()
        
        print("🔍 Validating registry synchronization with configuration...")
        validation = sync_manager.validate_registry_sync()
        
        print(f"\n📊 Validation Results:")
        print(f"  Databases checked: {validation['databases_checked']}")
        print(f"  Registry in sync: {'✓ Yes' if validation['in_sync'] else '✗ No'}")
        
        if validation['issues']:
            print(f"\n⚠️  Issues found:")
            for issue in validation['issues']:
                print(f"    - {issue}")
        
        if validation['missing_registrations']:
            print(f"\n📝 Missing registrations:")
            for missing in validation['missing_registrations']:
                print(f"    - {missing['database']}: {missing['count']} files not registered")
        
        if validation['orphaned_entries']:
            print(f"\n🗑️  Orphaned registry entries:")
            for orphaned in validation['orphaned_entries']:
                print(f"    - {orphaned['database']}: {orphaned['count']} entries without files")
        
        if validation['recommendations']:
            print(f"\n💡 Recommendations:")
            for rec in validation['recommendations']:
                print(f"    - {rec}")
        
        if args.auto_fix and not validation['in_sync']:
            print(f"\n🔧 Auto-fixing issues...")
            fix_results = sync_manager.auto_register_missing_files(dry_run=args.dry_run)
            
            if fix_results['success']:
                if args.dry_run:
                    print(f"Would register {fix_results['registered_count']} files")
                else:
                    print(f"✓ Successfully registered {fix_results['registered_count']} files")
                
                if fix_results['databases_processed']:
                    print("  Processed databases:")
                    for db in fix_results['databases_processed']:
                        print(f"    - {db}")
            else:
                print("✗ Auto-fix failed:")
                for error in fix_results['errors']:
                    print(f"    - {error}")
        
        print("\n" + "="*50)
        
    except Exception as e:
        print(f"Error: {e}")
        raise

async def handle_register_md(args):
    """Handle 'maia database register-md' command.

    Rebuilds the SQL registry and vector embeddings from existing local
    markdown files. No external API calls — purely local filesystem scan.
    Uses registry.add_content() to route each file to the correct
    per-database table (matching the sync system's storage pattern).
    """
    import glob
    import re
    from datetime import datetime
    from promaia.storage.hybrid_storage import get_hybrid_registry
    from promaia.utils.env_writer import get_data_dir

    try:
        db_manager = get_database_manager()
        registry = get_hybrid_registry()

        # Initialize vector DB for embedding generation (unless skipped)
        vector_db = None
        skip_embeddings = getattr(args, 'skip_embeddings', False)
        if not skip_embeddings:
            try:
                from promaia.storage.vector_db import VectorDBManager
                vector_db = VectorDBManager()
                print("Vector embeddings: enabled")
            except Exception as e:
                print(f"Warning: Could not initialize vector DB ({e}), skipping embeddings")
                vector_db = None

        # Get databases to process
        databases_to_process = []
        for db_name in db_manager.list_databases():
            db_config = db_manager.get_database(db_name)
            if args.workspace and db_config.workspace != args.workspace:
                continue
            if args.database and db_config.nickname != args.database:
                continue
            databases_to_process.append(db_config)

        if not databases_to_process:
            print("No databases found matching criteria.")
            return

        total_registered = 0
        total_embedded = 0
        data_dir = str(get_data_dir())

        for db_config in databases_to_process:
            print(f"\nProcessing {db_config.get_qualified_name()}...")

            # Resolve markdown directory (relative to data dir)
            md_dir = db_config.markdown_directory
            if md_dir and not os.path.isabs(md_dir):
                md_dir = os.path.join(data_dir, md_dir)
            if not md_dir or not os.path.exists(md_dir):
                print(f"  Skipped: directory not found ({db_config.markdown_directory})")
                continue

            # Find markdown files
            md_files = glob.glob(os.path.join(md_dir, "**/*.md"), recursive=True)
            print(f"  Found {len(md_files)} markdown files")

            registered_count = 0
            embedded_count = 0
            skipped_count = 0

            for md_file in md_files:
                try:
                    filename = os.path.basename(md_file)

                    # Extract page ID from filename
                    # Supports: Notion UUIDs, Gmail message IDs, Slack msg_ IDs, conversation threads
                    page_id_match = re.search(
                        r'(thread_\d{8}_\d{6}|msg_[\d.]+|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|[a-f0-9]{32})',
                        filename, re.IGNORECASE
                    )
                    if not page_id_match:
                        skipped_count += 1
                        continue

                    page_id = page_id_match.group(1)

                    # Extract title and date from filename
                    title_match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(.+?)\s+[a-f0-9]', filename)
                    if title_match:
                        date_str = title_match.group(1)
                        title = title_match.group(2).strip()
                        created_time = f"{date_str}T00:00:00Z"
                    else:
                        title = re.sub(r'\s+[a-f0-9-]{16,}\.md$', '', filename, flags=re.IGNORECASE).strip()
                        title = re.sub(r'\s+msg_[\d.]+\.md$', '', title, flags=re.IGNORECASE).strip()
                        file_mtime = datetime.fromtimestamp(os.path.getmtime(md_file))
                        created_time = file_mtime.isoformat() + "Z"

                    if args.dry_run:
                        print(f"    Would register: {title} ({page_id[:20]}...)")
                        registered_count += 1
                        continue

                    # Build content data for add_content routing
                    # database_name must be the nickname (not qualified) for correct table routing
                    content_data = {
                        'page_id': page_id,
                        'workspace': db_config.workspace,
                        'database_id': db_config.database_id,
                        'database_name': db_config.nickname,
                        'file_path': os.path.relpath(md_file, data_dir),
                        'title': title,
                        'created_time': created_time,
                        'last_edited_time': created_time,
                        'synced_time': datetime.now().isoformat() + "Z",
                        'content_type': db_config.source_type,
                        'data_source': db_config.source_type,
                        'metadata': {},
                    }

                    # Route to correct table via add_content
                    success = registry.add_content(content_data)

                    if success:
                        registered_count += 1

                        # Generate vector embedding
                        if vector_db:
                            try:
                                with open(md_file, 'r', encoding='utf-8') as f:
                                    file_content = f.read()
                                if file_content.strip():
                                    vector_db.add_content(page_id, file_content, {
                                        'database_name': db_config.nickname,
                                        'workspace': db_config.workspace,
                                        'content_type': db_config.source_type,
                                        'created_time': created_time,
                                        'title': title,
                                    })
                                    embedded_count += 1
                            except Exception as e:
                                logger.debug(f"Embedding failed for {page_id}: {e}")

                except Exception as e:
                    print(f"    Error: {os.path.basename(md_file)}: {e}")
                    continue

            action = "Would register" if args.dry_run else "Registered"
            embed_note = f" ({embedded_count} embedded)" if embedded_count else ""
            skip_note = f" ({skipped_count} skipped)" if skipped_count else ""
            print(f"  {action} {registered_count} new files{embed_note}{skip_note}")
            total_registered += registered_count
            total_embedded += embedded_count

        # Rebuild unified_content view to include all tables
        if not args.dry_run and total_registered > 0:
            try:
                registry.rebuild_unified_content_view()
                print("\nRebuilt unified content view.")
            except Exception as e:
                print(f"\nWarning: Could not rebuild view: {e}")

        embed_total = f" ({total_embedded} vector embeddings)" if total_embedded else ""
        action = "Would register" if args.dry_run else "Registered"
        print(f"\n{action} {total_registered} total files.{embed_total}")

        if args.dry_run:
            print("\nRun without --dry-run to actually register the files.")

    except Exception as e:
        print(f"Error: {e}")
        raise


# Keep old name as alias
handle_register_markdown_files = handle_register_md

# Add argument parsers for database commands
def add_database_commands(subparsers):
    """Adds database-related subparsers to the main parser."""
    db_parser = subparsers.add_parser('database', help='Manage databases')
    db_subparsers = db_parser.add_subparsers(dest='database_command', help='Database commands')
    add_database_commands_to_existing_parser(db_parser, db_subparsers)
    return db_parser

def add_database_commands_to_existing_parser(parent_parser, subparsers):
    """Helper function to add database subcommands to any parser with aliases."""
    
    # List databases
    list_parser = subparsers.add_parser('list', help='List configured databases')
    list_parser.set_defaults(func=handle_database_list)
    
    # Add 'ls' alias for list
    ls_parser = subparsers.add_parser('ls', help='List configured databases (alias for list)')
    ls_parser.set_defaults(func=handle_database_list)
    
    # Add database
    add_parser = subparsers.add_parser('add', help='Add a new database')
    add_parser.add_argument('name', nargs='?', help='Name of the database (e.g., "journal")')
    add_parser.add_argument('--source-type', help='Source type (e.g., notion)')
    add_parser.add_argument('--id', '--database-id', dest='database_id', help='Database ID (get from Notion URL: notion.so/workspace/DATABASE_ID?v=...)')
    add_parser.add_argument('--description', help='Database description')
    add_parser.add_argument('--workspace', help='Workspace name')
    add_parser.set_defaults(func=handle_database_add)
    
    # Remove database
    remove_parser = subparsers.add_parser('remove', help='Remove a database')
    remove_parser.add_argument('name', help='Database name to remove')
    remove_parser.set_defaults(func=handle_database_remove)
    
    # Add 'rm' alias for remove
    rm_parser = subparsers.add_parser('rm', help='Remove a database (alias for remove)')
    rm_parser.add_argument('name', help='Database name to remove')
    rm_parser.set_defaults(func=handle_database_remove)
    
    # --- Channel subcommand group: maia database channel {add,remove,list,rmi} ---
    channel_parser = subparsers.add_parser('channel', help='Manage database channels')
    channel_subparsers = channel_parser.add_subparsers(dest='channel_command', help='Channel commands')

    ch_add_parser = channel_subparsers.add_parser('add', help='Add channels to a database via interactive browser')
    ch_add_parser.add_argument('database_name', nargs='?', help='Database name (e.g., "dreamshare" or "koii.slack-general"). Omit to select interactively.')
    ch_add_parser.set_defaults(func=handle_database_add_channels)

    ch_remove_parser = channel_subparsers.add_parser('remove', help='Remove channels from a database via interactive browser')
    ch_remove_parser.add_argument('database_name', nargs='?', help='Database name (e.g., "dreamshare" or "koii.slack-general"). Omit to select interactively.')
    ch_remove_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    ch_remove_parser.set_defaults(func=handle_database_remove_channels)

    ch_list_parser = channel_subparsers.add_parser('list', help='List configured channels for a database')
    ch_list_parser.add_argument('database_name', nargs='?', help='Database name (e.g., "dreamshare" or "koii.slack-general"). Omit to select interactively.')
    ch_list_parser.set_defaults(func=handle_database_channel_list)

    ch_rmi_parser = channel_subparsers.add_parser('rmi', help='Interactively select and remove channels with data purging')
    ch_rmi_parser.add_argument('--workspace', '-ws', help='Workspace to show channels from (defaults to default workspace)')
    ch_rmi_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    ch_rmi_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without making changes')
    ch_rmi_parser.set_defaults(func=handle_channel_remove_interactive)

    # --- Hidden backward-compat aliases for old flat commands ---
    add_channels_parser = subparsers.add_parser('add-channels', help=argparse.SUPPRESS)
    add_channels_parser.add_argument('database_name', nargs='?', help='Database name')
    add_channels_parser.set_defaults(func=handle_database_add_channels)

    remove_channels_parser = subparsers.add_parser('remove-channels', help=argparse.SUPPRESS)
    remove_channels_parser.add_argument('database_name', nargs='?', help='Database name')
    remove_channels_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    remove_channels_parser.set_defaults(func=handle_database_remove_channels)

    remove_channels_interactive_parser = subparsers.add_parser('remove-channels-interactive', help=argparse.SUPPRESS)
    remove_channels_interactive_parser.add_argument('--workspace', '-ws', help='Workspace to show channels from')
    remove_channels_interactive_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    remove_channels_interactive_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without making changes')
    remove_channels_interactive_parser.set_defaults(func=handle_channel_remove_interactive)

    rmci_parser = subparsers.add_parser('rmci', help=argparse.SUPPRESS)
    rmci_parser.add_argument('--workspace', '-ws', help='Workspace to show channels from')
    rmci_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    rmci_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without making changes')
    rmci_parser.set_defaults(func=handle_channel_remove_interactive)

    # --- Edit command ---
    edit_parser = subparsers.add_parser('edit', help='Show available edit actions for a database')
    edit_parser.add_argument('name', nargs='?', help='Database name. Omit to select interactively.')
    edit_parser.set_defaults(func=handle_database_edit)

    # Enhanced remove operations with data purging
    remove_with_data_parser = subparsers.add_parser('purge', help='Remove a database and purge all its locally stored data')
    remove_with_data_parser.add_argument('name', help='Database name to remove and purge')
    remove_with_data_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    remove_with_data_parser.set_defaults(func=handle_database_remove_with_data_purge)

    # Interactive database removal using simple selector
    remove_interactive_parser = subparsers.add_parser('remove-interactive', help='Interactively select and remove databases with data purging')
    remove_interactive_parser.add_argument('--workspace', '-ws', help='Workspace to show databases from (defaults to default workspace)')
    remove_interactive_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    remove_interactive_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without making changes')
    remove_interactive_parser.set_defaults(func=handle_database_remove_interactive)

    # Add shorter alias for interactive remove
    rmi_parser = subparsers.add_parser('rmi', help='Interactively remove databases (alias for remove-interactive)')
    rmi_parser.add_argument('--workspace', '-ws', help='Workspace to show databases from (defaults to default workspace)')
    rmi_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    rmi_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without making changes')
    rmi_parser.set_defaults(func=handle_database_remove_interactive)
    
    # Test database connection
    test_parser = subparsers.add_parser('test', help='Test database connections')
    test_parser.add_argument('names', nargs='*', help='Database names to test (default: all)')
    test_parser.set_defaults(func=handle_database_test)
    
    # Sync databases
    sync_parser = subparsers.add_parser('sync', help='Sync databases')
    sync_parser.add_argument('--source', '-s', action='append', dest='sources', help='Source specifications (e.g., journal:30, trass.stories:7). Can be used multiple times.')
    sync_parser.add_argument('--browse', '-b', nargs='*', help='Browse and select Discord channels to sync. Optionally specify databases (e.g., -b trass.discord trass.yeeps_discord)')
    sync_parser.add_argument('--workspace', '-ws', help='Workspace to sync (expands to all enabled databases in workspace with default days)')
    sync_parser.add_argument('--days', type=int, help='Number of days to sync')
    sync_parser.add_argument('--force', action='store_true', help='Force update all files')
    sync_parser.add_argument('--properties-only', action='store_true', help='Only sync properties without re-downloading page content (much faster)')

    # Add simple date range arguments
    sync_parser.add_argument('--start-date', help='Start date for sync (e.g., 2025-02-01)')
    sync_parser.add_argument('--end-date', help='End date for sync (e.g., 2025-03-31)')
    sync_parser.add_argument('--date-range', help='Date range for sync (e.g., 2025-02-01,2025-03-31)')
    
    sync_parser.set_defaults(func=handle_database_sync)
    
    # Database info
    info_parser = subparsers.add_parser('info', help='Show database information')
    info_parser.add_argument('name', help='Database name')
    info_parser.add_argument('--schema', action='store_true', help='Show database schema')
    info_parser.set_defaults(func=handle_database_info)
    
    # Push database
    push_parser = subparsers.add_parser('push', help='Push local markdown changes to Notion')
    push_parser.add_argument('database', nargs='?', help='Database name to push (omit to push all enabled databases)')
    push_parser.add_argument('--force', action='store_true', help='Force push all files regardless of changes')
    push_parser.add_argument('--workspace', help='Workspace to push from (defaults to default workspace)')
    push_parser.set_defaults(func=handle_database_push)
    
    # Database status
    status_parser = subparsers.add_parser('status', help='Show what needs to be synced')
    status_parser.add_argument('target', nargs='?', default='all', help='Content type to show status for (default: all)')
    status_parser.set_defaults(func=handle_database_status)
    
    # Add 'st' alias for status
    st_parser = subparsers.add_parser('st', help='Show what needs to be synced (alias for status)')
    st_parser.add_argument('target', nargs='?', default='all', help='Content type to show status for (default: all)')
    st_parser.set_defaults(func=handle_database_status)
    
    # List sources command
    list_sources_parser = subparsers.add_parser('list-sources', help='List all available database sources in JSON format')
    list_sources_parser.set_defaults(func=handle_database_list_sources)
    
    # Add 'sources' alias for list-sources
    sources_parser = subparsers.add_parser('sources', help='List all available database sources in JSON format (alias for list-sources)')
    sources_parser.set_defaults(func=handle_database_list_sources)

    # Validate registry command
    validate_parser = subparsers.add_parser('validate-registry', help='Validate that registry is in sync with configuration')
    validate_parser.add_argument('--auto-fix', action='store_true', help='Automatically fix issues by registering missing files')
    validate_parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    validate_parser.set_defaults(func=handle_validate_registry)

    # Register markdown files command
    register_parser = subparsers.add_parser('register-md', aliases=['register-markdown-files'],
        help='Rebuild SQL registry and vector embeddings from local markdown files (no API calls)')
    register_parser.add_argument('--workspace', help='Workspace to register files for (optional)')
    register_parser.add_argument('--database', help='Database nickname to register files for (optional)')
    register_parser.add_argument('--dry-run', action='store_true', help='Show what would be registered without making changes')
    register_parser.add_argument('--skip-embeddings', action='store_true', help='Skip vector embedding generation (SQL registry only)')
    register_parser.set_defaults(func=handle_register_md)
