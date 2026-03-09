"""
Discord integration commands for the Maia CLI.
"""
import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Tuple, Optional

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich import box

from promaia.utils.display import print_text, print_separator
from promaia.utils.env_writer import get_data_dir

logger = logging.getLogger(__name__)


def _discord_creds_dir(workspace: str) -> str:
    """Return the credentials directory path for a workspace."""
    return str(get_data_dir() / "credentials" / workspace)

async def handle_discord_setup(args):
    """Handle 'maia workspace discord-setup' command."""
    workspace = args.workspace
    server_id = getattr(args, 'server_id', None)
    
    print_text(f"🔧 Setting up Discord bot for workspace '{workspace}'")
    if server_id:
        print_text(f"🏰 Server ID: {server_id}")
    print()
    
    # Create workspace credentials directory
    config_dir = _discord_creds_dir(workspace)
    os.makedirs(config_dir, exist_ok=True)
    
    credentials_file = os.path.join(config_dir, "discord_credentials.json")
    
    # Check if credentials already exist
    if os.path.exists(credentials_file):
        overwrite = input(f"Discord credentials already exist for workspace '{workspace}'. Overwrite? (y/n): ").strip().lower()
        if overwrite != 'y':
            print("Setup cancelled.")
            return
    
    print("🤖 Discord Bot Setup Instructions:")
    print()
    print("1. Go to the Discord Developer Portal: https://discord.com/developers/applications")
    print("2. Click 'New Application' and give it a name")
    print("3. Go to the 'Bot' section in the left sidebar")
    print("4. Click 'Add Bot' (if not already created)")
    print("5. Under 'Token', click 'Copy' to copy your bot token")
    print("6. Under 'Privileged Gateway Intents', enable:")
    print("   ✅ Message Content Intent")
    print("   ✅ Server Members Intent")
    print("7. Go to 'OAuth2' > 'URL Generator':")
    print("   - Scopes: Select 'bot'")
    print("   - Bot Permissions: Select 'Read Message History' and 'View Channels'")
    print("8. Copy the generated URL and use it to invite the bot to your Discord server")
    print()
    
    # Get bot token from user
    bot_token = input("🔑 Enter your Discord bot token: ").strip()
    
    if not bot_token:
        print("❌ Bot token is required.")
        return
    
    # Get server ID if not provided
    if not server_id:
        server_id = input("🏰 Enter Discord server ID (optional, press Enter to skip): ").strip()
        if not server_id:
            server_id = None
    
    # Create credentials structure
    creds_data = {
        "bot_token": bot_token,
        "default_server_id": server_id,
        "workspace": workspace,
        "created_at": asyncio.get_event_loop().time()
    }
    
    # Save credentials
    try:
        with open(credentials_file, 'w') as f:
            json.dump(creds_data, f, indent=2)
        
        print_text(f"✅ Credentials saved to {credentials_file}")
        
    except Exception as e:
        print(f"❌ Error saving credentials: {e}")
        return
    
    # Test the bot connection
    print()
    print("🔑 Testing Discord bot connection...")
    
    try:
        # Import here to handle optional dependencies
        from promaia.connectors.discord_connector import DiscordConnector
        
        # Create a test connector to verify bot connection
        test_config = {
            "database_id": server_id,
            "workspace": workspace,
            "bot_token": bot_token
        }
        
        connector = DiscordConnector(test_config)
        
        if await connector.test_connection():
            print("✅ Discord bot setup completed successfully!")
            if server_id:
                print(f"🏰 Connected to Discord server: {connector.guild.name}")
            print()
            print("You can now add Discord as a database source:")
            print(f"  maia database add discord_general \\")
            print(f"    --source-type discord \\")
            print(f"    --database-id {server_id or 'YOUR_SERVER_ID'} \\")
            print(f"    --workspace {workspace}")
            print()
            print("To sync a specific channel:")
            print(f"  maia sync discord_general --filters channel_id=YOUR_CHANNEL_ID")
        else:
            print("❌ Discord bot connection failed.")
            print("Please check your bot token and server permissions.")
            
        # Cleanup the connection
        await connector.cleanup()
        
    except ImportError:
        print("❌ Discord integration not available.")
        print("Please install discord.py: pip install discord.py")
    except Exception as e:
        print(f"❌ Error testing Discord connection: {e}")
        logger.error(f"Discord setup error: {e}")

async def handle_discord_list_channels(args):
    """Handle 'maia discord list-channels' command."""
    workspace = args.workspace
    server_id = getattr(args, 'server_id', None)
    
    try:
        # Load credentials
        config_dir = _discord_creds_dir(workspace)
        credentials_file = os.path.join(config_dir, "discord_credentials.json")
        
        if not os.path.exists(credentials_file):
            print_text(f"❌ Discord credentials not found for workspace '{workspace}'")
            print_text(f"Please run: maia workspace discord-setup {workspace}")
            return
        
        with open(credentials_file, 'r') as f:
            creds_data = json.load(f)
        
        bot_token = creds_data.get("bot_token")
        if not server_id:
            server_id = creds_data.get("default_server_id")
        
        if not server_id:
            print("❌ Server ID is required")
            print("Either provide --server-id or set it during setup")
            return
        
        # Import here to handle optional dependencies
        from promaia.connectors.discord_connector import DiscordConnector
        
        # Create connector
        connector = DiscordConnector({
            "database_id": server_id,
            "workspace": workspace,
            "bot_token": bot_token
        })
        
        if not await connector.connect():
            print("❌ Failed to connect to Discord")
            return
        
        # Get guild data using the new method
        guild_data = await connector._get_guild_data()
        
        if not guild_data:
            print("❌ Failed to fetch guild data")
            return
        
        print(f"📋 Channels in server '{guild_data['name']}':")
        print()
        
        text_channels = [ch for ch in guild_data['channels'] if ch['type'] == 'text']
        
        if not text_channels:
            print("No accessible text channels found.")
            return
        
        for channel in text_channels:
            print(f"  #{channel['name']} (ID: {channel['id']})")
            print()
        
        print(f"Total: {len(text_channels)} text channels")
        print()
        print("To sync a channel, use:")
        print(f"  maia sync DATABASE_NAME --filters channel_id=CHANNEL_ID")
        
        # Cleanup
        await connector.cleanup()
        
    except ImportError:
        print("❌ Discord integration not available.")
        print("Please install discord.py: pip install discord.py")
    except Exception as e:
        print(f"❌ Error listing Discord channels: {e}")
        logger.error(f"Discord list channels error: {e}")

async def handle_discord_sync(args):
    """Handle 'maia discord sync' command."""
    database_name = args.database
    workspace = getattr(args, 'workspace', None)
    channel_id = getattr(args, 'channel_id', None)
    days = getattr(args, 'days', 7)
    limit = getattr(args, 'limit', 100)
    
    if not channel_id:
        print("❌ Channel ID is required for Discord sync")
        print("Use: maia discord sync DATABASE_NAME --channel-id CHANNEL_ID")
        return
    
    try:
        from promaia.config.databases import get_database_manager
        from promaia.storage.unified_storage import get_unified_storage
        from promaia.connectors.base import QueryFilter, DateRangeFilter
        from datetime import datetime, timedelta
        
        # Get database config
        db_manager = get_database_manager()
        db_config = db_manager.get_database(database_name)
        
        if not db_config:
            print(f"❌ Database '{database_name}' not found")
            print("Add it first with: maia database add")
            return
        
        if db_config.source_type != "discord":
            print(f"❌ Database '{database_name}' is not a Discord database")
            return
        
        # Load credentials
        config_dir = _discord_creds_dir(workspace)
        credentials_file = os.path.join(config_dir, "discord_credentials.json")
        
        if not os.path.exists(credentials_file):
            print(f"❌ Discord credentials not found for workspace '{workspace}'")
            print(f"Please run: maia workspace discord-setup {workspace}")
            return
        
        with open(credentials_file, 'r') as f:
            creds_data = json.load(f)
        
        # Create connector
        from promaia.connectors.discord_connector import DiscordConnector
        
        connector = DiscordConnector({
            "database_id": db_config.database_id,
            "workspace": workspace,
            "bot_token": creds_data.get("bot_token"),
            "sync_limit": limit
        })
        
        # Create filters
        filters = [QueryFilter("channel_id", "eq", str(channel_id))]
        date_filter = DateRangeFilter("timestamp", days_back=days)
        
        # Get storage
        storage = get_unified_storage()
        
        print(f"🔄 Syncing Discord messages from channel {channel_id}...")
        print(f"📅 Days back: {days}")
        print(f"📊 Limit: {limit}")
        print()
        
        # Perform sync
        result = await connector.sync_to_local_unified(
            storage=storage,
            db_config=db_config,
            filters=filters,
            date_filter=date_filter,
            include_properties=True,
            force_update=False
        )
        
        # Display results
        print("📊 Sync Results:")
        print(f"  📥 Messages fetched: {result.pages_fetched}")
        print(f"  💾 Messages saved: {result.pages_saved}")
        print(f"  ⏭️  Messages skipped: {result.pages_skipped}")
        print(f"  ❌ Errors: {result.pages_failed}")
        
        if result.errors:
            print()
            print("🚨 Errors encountered:")
            for error in result.errors:
                print(f"  - {error}")
        
        print()
        print("✅ Discord sync completed!")
        
        # Cleanup
        await connector.cleanup()
        
    except ImportError:
        print("❌ Discord integration not available.")
        print("Please install discord.py: pip install discord.py")
    except Exception as e:
        print(f"❌ Discord sync failed: {e}")
        logger.error(f"Discord sync error: {e}")

async def handle_discord_browse(args):
    """Handle 'maia discord browse' command - Interactive channel browser."""
    workspace = args.workspace
    
    try:
        # Import here to handle optional dependencies
        from promaia.connectors.discord_connector import DiscordConnector
        from promaia.config.databases import get_database_manager
        
        console = Console()
        
        # Get all Discord databases for this workspace
        db_manager = get_database_manager()
        discord_databases = []
        
        for db_name, db_config in db_manager.databases.items():
            if db_config.workspace == workspace and db_config.source_type == "discord":
                discord_databases.append((db_name, db_config))
        
        if not discord_databases:
            print_text(f"❌ No Discord databases found for workspace '{workspace}'", style="red")
            return
        
        # Load credentials
        config_dir = _discord_creds_dir(workspace)
        credentials_file = os.path.join(config_dir, "discord_credentials.json")
        
        if not os.path.exists(credentials_file):
            print_text(f"❌ Discord credentials not found for workspace '{workspace}'", style="red")
            print_text(f"Please run: maia workspace discord-setup {workspace}")
            return
        
        with open(credentials_file, 'r') as f:
            creds_data = json.load(f)
        
        bot_token = creds_data.get("bot_token")
        
        # Fetch channels for each server
        all_channels = []
        
        for db_name, db_config in discord_databases:
            with console.status(f"[bold blue]Checking permissions for {db_name}..."):
                try:
                    connector = DiscordConnector({
                        "database_id": db_config.database_id,
                        "workspace": workspace,
                        "bot_token": bot_token
                    })
                    
                    # Get server info and channels
                    server_info = await get_server_channels(connector, db_config.database_id)
                    
                    # Get already synced channels from filesystem (fast)
                    synced_channels = get_synced_channels_from_filesystem(db_config)
                    
                    # Ensure synced_channels is never None
                    if synced_channels is None:
                        synced_channels = []
                    
                    # Combine server info with synced channels
                    all_channels.append({
                        "db_name": db_name,
                        "db_config": db_config,
                        "server_name": server_info.get("server_name", "Unknown Server"),
                        "channels": synced_channels  # Only show channels that have been synced
                    })
                    
                except Exception as e:
                    print_text(f"⚠️  Error fetching channels for {db_name}: {e}", style="yellow")
        
        if not any(server["channels"] for server in all_channels):
            print_text("❌ No accessible channels found", style="red")
            return
        
        # Start interactive browser
        selected_channels, _ = await interactive_channel_browser(console, all_channels, workspace)
        
        if selected_channels:
            print_text(f"\n🎉 Selected {len(selected_channels)} channels!")
            
            # Return the selected channels for chat integration
            return selected_channels
        else:
            print_text("ℹ️  No channels selected", style="cyan")
            return []
        
    except ImportError:
        print("❌ Discord integration not available.")
        print("Please install discord.py: pip install discord.py")
    except Exception as e:
        print(f"❌ Error in Discord browse: {e}", style="red")
        logger.error(f"Discord browse error: {e}")


async def handle_discord_browse_filtered(args, previous_selections=None):
    """Handle filtered Discord channel browser - only show specific databases."""
    workspace = args.workspace
    filter_databases = getattr(args, 'databases', None)  # List of database names to filter by
    database_days = getattr(args, 'database_days', {})  # Map of database -> days
    previous_selections = previous_selections or []  # Previous channel selections for pre-population
    
    # Initialize console first so it's available in except blocks
    console = Console()
    
    try:
        # Import here to handle optional dependencies
        from promaia.connectors.discord_connector import DiscordConnector
        from promaia.config.databases import get_database_manager
        
        # Show date filtering info if specified
        if database_days:
            print_text("📅 Date filtering active:")
            for db_name, days in database_days.items():
                print_text(f"   • {db_name}: last {days} days")
            print()
        
        # Get all Discord databases for this workspace
        db_manager = get_database_manager()
        discord_databases = []
        
        if filter_databases is None:
            # No filter - include all Discord databases in workspace
            for db_name, db_config in db_manager.databases.items():
                if db_config.workspace == workspace and db_config.source_type == "discord":
                    days = 30  # Default
                    discord_databases.append((db_name, db_config, days))
        else:
            # Filter specified - resolve each filter name properly
            for filter_name in filter_databases:
                # Strip day specification to get just the database name
                db_name_only = filter_name.split(':')[0] if ':' in filter_name else filter_name
                
                # Use proper database resolution (handles nicknames)
                db_config = db_manager.get_database_by_qualified_name(db_name_only)
                
                if db_config and db_config.workspace == workspace and db_config.source_type == "discord":
                    # Get the actual database key (config name)
                    db_name = db_config.name
                    
                    # Determine days for this database for display
                    days = None
                    if filter_name in database_days:
                        days = database_days[filter_name]
                    elif db_name in database_days:
                        days = database_days[db_name]
                    elif db_config.get_qualified_name() in database_days:
                        days = database_days[db_config.get_qualified_name()]
                    else:
                        days = 30  # Default
                    
                    discord_databases.append((db_name, db_config, days))
        
        if not discord_databases:
            if filter_databases:
                print_text(f"❌ No Discord databases found matching: {', '.join(filter_databases)}", style="red")
                
                # Show available Discord databases to help user
                all_discord_dbs = []
                for db_name, db_config in db_manager.databases.items():
                    if db_config.workspace == workspace and db_config.source_type == "discord":
                        qualified_name = db_config.get_qualified_name()
                        # Show both qualified name and full config name if different
                        if qualified_name != db_name:
                            all_discord_dbs.append(f"{qualified_name} (or {db_name})")
                        else:
                            all_discord_dbs.append(qualified_name)
                
                if all_discord_dbs:
                    print_text(f"📋 Available Discord databases for workspace '{workspace}':", style="cyan")
                    for db in all_discord_dbs:
                        print_text(f"   • {db}", style="dim cyan")
                    # For suggestion, use the qualified name (which includes nickname)
                    first_suggestion = all_discord_dbs[0].split(' (or ')[0]  # Get just the qualified name part
                    print_text(f"\n💡 Try: -b {first_suggestion}:7", style="dim yellow")
                else:
                    print_text(f"ℹ️  No Discord databases configured for workspace '{workspace}'", style="yellow")
                    print_text(f"💡 Set up Discord integration: maia workspace discord-setup {workspace}", style="dim yellow")
            else:
                print_text(f"❌ No Discord databases found for workspace '{workspace}'", style="red")
            return []
        
        # Load credentials
        config_dir = _discord_creds_dir(workspace)
        credentials_file = os.path.join(config_dir, "discord_credentials.json")
        
        if not os.path.exists(credentials_file):
            print_text(f"❌ Discord credentials not found for workspace '{workspace}'", style="red")
            print_text(f"Please run: maia workspace discord-setup {workspace}")
            return []
        
        with open(credentials_file, 'r') as f:
            creds_data = json.load(f)
        
        bot_token = creds_data.get("bot_token")
        
        # Fetch channels for each server
        all_channels = []
        
        for db_name, db_config, days in discord_databases:
            with console.status(f"[bold blue]Checking permissions for {db_name} ({days} days)..."):
                try:
                    connector = DiscordConnector({
                        "database_id": db_config.database_id,
                        "workspace": workspace,
                        "bot_token": bot_token
                    })
                    
                    # Get server info and channels
                    server_info = await get_server_channels(connector, db_config.database_id)
                    
                    # Get accessible channels from cache (with auto-discovery on first use)
                    accessible_channels, cached_server_name = await get_accessible_channels_cached(db_config, bot_token)

                    # Ensure accessible_channels is never None
                    if accessible_channels is None:
                        accessible_channels = []

                    # Use real server name from cache, fall back to server_info
                    real_server_name = cached_server_name or server_info.get("server_name", "Unknown Server")

                    # Combine server info with accessible channels
                    all_channels.append({
                        "db_name": db_name,
                        "db_config": db_config,
                        "days": days,
                        "server_name": real_server_name,
                        "channels": accessible_channels  # Show all accessible channels
                    })
                    
                except Exception as e:
                    print_text(f"⚠️  Error fetching channels for {db_name}: {e}", style="yellow")
        
        if not any(server["channels"] for server in all_channels):
            print_text("❌ No accessible channels found", style="red")
            return []
        
        # Start interactive browser
        selected_channels, updated_days = await interactive_channel_browser(console, all_channels, workspace, previous_selections)
        
        if selected_channels:
            print_text(f"\n🎉 Selected {len(selected_channels)} channels!")
            
            # Show any date range changes
            if updated_days:
                for db_name, days in updated_days.items():
                    original_days = None
                    for orig_db_name, _, orig_days in discord_databases:
                        if orig_db_name == db_name:
                            original_days = orig_days
                            break
                    if original_days and original_days != days:
                        print_text(f"�� Updated {db_name}: {original_days} → {days} days", style="cyan")
            
            # Return the selected channels for chat integration
            # For now, we'll still return the original format for compatibility
            # but in the future, we could return updated days too
            return selected_channels
        else:
            print_text("ℹ️  No channels selected", style="cyan")
            return []
        
    except ImportError:
        print("❌ Discord integration not available.")
        print("Please install discord.py: pip install discord.py")
        return []
    except Exception as e:
        print(f"❌ Error in Discord browse: {e}", style="red")
        logger.error(f"Discord browse error: {e}")
        return []


async def get_server_channels(connector, server_id: str) -> Dict:
    """Get channels for a Discord server that have already been synced (fast lookup)."""
    try:
        # Use the same approach as the working _get_guild_data method  
        import discord
        
        client = discord.Client(intents=connector.intents)
        
        try:
            await client.login(connector.bot_token)
            guild = await client.fetch_guild(int(server_id))
            
            return {
                "server_name": guild.name,
                "channels": []  # Will be populated by checking existing synced channels
            }
            
        finally:
            await client.close()
            
    except Exception as e:
        logger.error(f"Error fetching server info: {e}")
        return {"server_name": "Unknown Server", "channels": []}

def get_synced_channels_from_filesystem(db_config) -> List[Dict]:
    """Get list of channels that have already been synced by checking filesystem."""
    channels = []
    
    try:
        import os
        from pathlib import Path
        
        # Check the markdown directory for this Discord database
        md_dir = db_config.markdown_directory
        if not os.path.isabs(md_dir):
            from promaia.utils.env_writer import get_data_dir
            md_dir = os.path.join(str(get_data_dir()), md_dir)

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
                        "id": "unknown",  # We don't need the ID for chat browsing
                        "name": channel_dir.name,  # Use directory name as-is
                        "message_count": len(message_files),
                        "last_activity": last_sync
                    })
    
    except Exception as e:
        logger.error(f"Error reading synced channels from filesystem: {e}")
    
    return channels

async def get_accessible_channels_cached(db_config, bot_token) -> Tuple[List[Dict], str]:
    """Get accessible channels from cache, with auto-discovery on first use.

    Returns:
        Tuple of (formatted_channels, server_name).
    """
    try:
        from promaia.connectors.discord_connector import DiscordConnector

        connector = DiscordConnector({
            "database_id": db_config.database_id,
            "workspace": db_config.workspace,
            "bot_token": bot_token
        })

        # Always fetch fresh channels from Discord API so newly added channels appear
        channel_data = await connector.get_cached_accessible_channels(force_refresh=True)
        accessible_channels = channel_data.get('channels', [])
        server_name = channel_data.get('server_name', f"Discord Server ({db_config.nickname})")

        # Convert to format expected by browser and add sync status
        synced_channels = get_synced_channels_from_filesystem(db_config)
        synced_names = {ch['name'] for ch in synced_channels}
        synced_counts = {ch['name']: ch['message_count'] for ch in synced_channels}
        synced_activity = {ch['name']: ch['last_activity'] for ch in synced_channels}

        formatted_channels = []
        for channel in accessible_channels:
            channel_name = channel['name']
            formatted_channels.append({
                "id": channel['id'],
                "name": channel_name,
                "message_count": synced_counts.get(channel_name, 0),
                "last_activity": synced_activity.get(channel_name, "not synced"),
                "is_synced": channel_name in synced_names,
                "discovered_at": channel.get('discovered_at', 'unknown')
            })

        return formatted_channels, server_name

    except Exception as e:
        logger.error(f"Error getting accessible channels: {e}")
        # Fall back to filesystem-only approach
        fallback_channels = get_synced_channels_from_filesystem(db_config)
        return (fallback_channels if fallback_channels is not None else []), f"Discord Server ({db_config.nickname})"

async def interactive_channel_browser(console: Console, servers: List[Dict], workspace: str, previous_selections=None) -> Tuple[List[Tuple[str, str, str, int]], Dict[Tuple[str, str], int]]:
    """Interactive channel browser using editable TextArea widgets (same UX as workspace browser).

    Each channel is shown as an editable line like ``#general:30`` — arrow keys navigate,
    typing changes the days value, SPACE toggles selection, ENTER confirms.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.widgets import TextArea

    # --- Build flat list of channels ---------------------------------------------------
    nav_items = []
    previous_selections = previous_selections or []

    previous_days_map = {}
    previous_selection_set = set()
    for prev_db_name, prev_channel_id, prev_channel_name, prev_days in previous_selections:
        key = (prev_db_name, prev_channel_name)
        previous_selection_set.add(key)
        previous_days_map[key] = prev_days

    for server in servers:
        server_default_days = server.get("days", 30)
        channels = server.get("channels") or []
        for channel in channels:
            channel_key = (server["db_name"], channel["name"])
            days = previous_days_map.get(channel_key, server_default_days)
            nav_items.append({
                "server_name": server["server_name"],
                "db_name": server["db_name"],
                "channel": channel,
                "selected": channel_key in previous_selection_set,
                "days": days,
            })

    if not nav_items:
        return [], {}

    # --- Build TextArea widgets (one per channel) -------------------------------------
    text_areas: List[TextArea] = []
    enabled_states: List[bool] = []
    source_windows: List = []

    def get_prefix(idx):
        checkbox = "☑" if enabled_states[idx] else "☐"
        return f"{checkbox}      "

    current_server = None
    for item in nav_items:
        # Server header
        if current_server != item["server_name"]:
            if current_server is not None:
                source_windows.append(Window(height=1))  # spacer between servers
            current_server = item["server_name"]
            source_windows.append(
                Window(FormattedTextControl(text=f"📂 {current_server} ({item['db_name']})"), height=1)
            )

        spec = f"#{item['channel']['name']}:{item['days']}"
        ta = TextArea(text=spec, height=1, multiline=False, wrap_lines=False, scrollbar=False, focusable=True)
        ta.buffer.cursor_position = len(spec)
        text_areas.append(ta)
        enabled_states.append(item["selected"])

        idx = len(text_areas) - 1
        prefix_text = get_prefix(idx)
        row = VSplit([
            Window(FormattedTextControl(text=prefix_text), width=8, dont_extend_width=True),
            ta,
        ])
        source_windows.append(row)

    # Map text_area index → source_windows index (skip headers/spacers)
    ta_to_sw = [i for i, w in enumerate(source_windows) if hasattr(w, 'children') and len(w.children) > 1]

    # --- Layout -----------------------------------------------------------------------
    current_focus = 0
    confirmed = False

    def get_status():
        sel = sum(enabled_states)
        total = len(enabled_states)
        return f"📋 {workspace} | Channels: {total} | Selected: {sel}/{total} | ↑↓ Navigate  SPACE Toggle  ENTER Confirm  ESC Cancel"

    status_window = Window(FormattedTextControl(text=get_status), height=1)
    hint_window = Window(FormattedTextControl(text="Don't see the channel you want? Make sure it's public or that the Promaia Discord bot has been added."), height=1)

    container = HSplit([status_window, Window(height=1), *source_windows, Window(height=1), hint_window])
    layout = Layout(container)
    layout.focus(text_areas[current_focus])

    # --- Key bindings -----------------------------------------------------------------
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

    @bindings.add(' ')
    def toggle(event):
        enabled_states[current_focus] = not enabled_states[current_focus]
        sw_idx = ta_to_sw[current_focus]
        source_windows[sw_idx].children[0].content.text = get_prefix(current_focus)
        status_window.content.text = get_status

    @bindings.add(Keys.Enter)
    def confirm(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(layout=layout, key_bindings=bindings, full_screen=False, mouse_support=False)
    await app.run_async()

    # --- Parse results ----------------------------------------------------------------
    if not confirmed:
        return [], {}

    selected_channels = []
    channel_days: Dict[Tuple[str, str], int] = {}

    for i, item in enumerate(nav_items):
        # Parse days from the (possibly edited) TextArea text
        raw = text_areas[i].text.strip()
        if ':' in raw:
            days_str = raw.rsplit(':', 1)[1]
            try:
                days = int(days_str)
            except ValueError:
                days = item["days"]  # fallback to original
        else:
            days = item["days"]

        channel_key = (item["db_name"], item["channel"]["name"])
        channel_days[channel_key] = days

        if enabled_states[i]:
            selected_channels.append((item["db_name"], item["channel"]["id"], item["channel"]["name"], days))

    return selected_channels, channel_days

async def bulk_sync_channels(console: Console, selected_channels: List[Tuple[str, str, str, int]], workspace: str, limit: int):
    """Perform bulk sync of selected channels."""
    from promaia.cli.database_commands import handle_database_sync
    from promaia.config.databases import get_database_manager
    
    console.print(f"\n🚀 Starting bulk sync of {len(selected_channels)} channels...")
    
    db_manager = get_database_manager()
    
    for i, (db_name, channel_id, channel_name, channel_days) in enumerate(selected_channels, 1):
        console.print(f"\n[{i}/{len(selected_channels)}] Syncing #{channel_name} ({channel_days} days)...")
        
        try:
            # Create a mock args object for the sync function
            class MockArgs:
                def __init__(self):
                    self.database = db_name
                    self.channel_id = channel_id
                    self.workspace = workspace
                    self.days = channel_days  # Use channel-specific days
                    self.limit = limit
            
            # Use the existing Discord sync function
            await handle_discord_sync(MockArgs())
            console.print(f"   ✅ Synced #{channel_name}", style="green")
            
        except Exception as e:
            console.print(f"   ❌ Failed to sync #{channel_name}: {e}", style="red")
    
    console.print(f"\n🎉 Bulk sync completed!")

async def handle_discord_debug_channels(args):
    """Handle 'maia discord debug-channels' command to list available Discord channels."""
    from promaia.config.databases import get_database_manager
    
    # Get workspace (default to current or prompt user)
    workspace = getattr(args, 'workspace', None)
    if not workspace:
        from promaia.config.workspaces import get_workspace_manager
        workspace_manager = get_workspace_manager()
        workspace = workspace_manager.get_default_workspace()
        if not workspace:
            print("❌ No workspace specified and no default workspace configured.")
            return
    
    print_text(f"🔍 Debugging Discord channels for workspace: {workspace}")
    
    # Get all Discord databases for this workspace
    db_manager = get_database_manager()
    discord_dbs = []
    
    for db_name in db_manager.list_databases():
        db_config = db_manager.get_database(db_name)
        if db_config and db_config.source_type == 'discord' and db_config.workspace == workspace:
            discord_dbs.append((db_name, db_config))
    
    if not discord_dbs:
        print_text(f"❌ No Discord databases found for workspace '{workspace}'", style="red")
        return
    
    print_text(f"📋 Found {len(discord_dbs)} Discord database(s):")
    
    for db_name, db_config in discord_dbs:
        print(f"\n🗃️  Database: {db_name} (ID: {db_config.database_id})")
        
        # Create Discord connector for this database
        try:
            # Load Discord credentials
            import os
            import json
            
            config_dir = _discord_creds_dir(workspace)
            credentials_file = os.path.join(config_dir, "discord_credentials.json")
            
            if not os.path.exists(credentials_file):
                print_text(f"   ❌ Discord credentials not found for workspace '{workspace}'", style="red")
                continue
            
            with open(credentials_file, 'r') as f:
                creds_data = json.load(f)
            
            # Create connector config
            connector_config = db_config.to_dict()
            connector_config['bot_token'] = creds_data.get("bot_token")
            
            # Create connector
            from promaia.connectors.discord_connector import DiscordConnector
            connector = DiscordConnector(connector_config)
            
            # List channels
            await connector.list_server_channels()
            
        except Exception as e:
            print(f"   ❌ Error debugging channels for {db_name}: {e}")
            import traceback
            traceback.print_exc()

async def handle_discord_refresh(args):
    """Handle 'maia discord refresh' command to refresh accessible channel cache."""
    workspace = args.workspace
    
    try:
        from promaia.connectors.discord_connector import DiscordConnector
        from promaia.config.databases import get_database_manager
        
        console = Console()
        
        # Get all Discord databases for this workspace
        db_manager = get_database_manager()
        discord_databases = []
        
        for db_name, db_config in db_manager.databases.items():
            if db_config.workspace == workspace and db_config.source_type == "discord":
                discord_databases.append((db_name, db_config))
        
        if not discord_databases:
            print_text(f"❌ No Discord databases found for workspace '{workspace}'", style="red")
            return
        
        # Load credentials
        config_dir = _discord_creds_dir(workspace)
        credentials_file = os.path.join(config_dir, "discord_credentials.json")
        
        if not os.path.exists(credentials_file):
            print_text(f"❌ Discord credentials not found for workspace '{workspace}'", style="red")
            print_text(f"Please run: maia workspace discord-setup {workspace}")
            return
        
        with open(credentials_file, 'r') as f:
            creds_data = json.load(f)
        
        bot_token = creds_data.get("bot_token")
        
        # Refresh cache for each Discord database
        total_refreshed = 0
        total_tested = 0
        
        import time
        start_time = time.time()
        
        for db_name, db_config in discord_databases:
            with console.status(f"[bold blue]Testing channel permissions for {db_name}..."):
                try:
                    connector = DiscordConnector({
                        "database_id": db_config.database_id,
                        "workspace": workspace,
                        "bot_token": bot_token
                    })
                    
                    # Refresh the channel cache with improved permission checking
                    channel_data = await connector.refresh_channel_cache()
                    accessible_count = len(channel_data.get('channels', []))
                    tested_count = channel_data.get('total_tested', 0)
                    
                    total_refreshed += accessible_count
                    total_tested += tested_count
                    
                    console.print(f"✅ {db_name}: {accessible_count}/{tested_count} channels accessible")
                    
                except Exception as e:
                    console.print(f"❌ Error refreshing {db_name}: {e}", style="red")
        
        end_time = time.time()
        duration = end_time - start_time
        
        console.print()
        console.print(f"🎉 Refresh complete in {duration:.1f}s!")
        console.print(f"📊 Results: {total_refreshed}/{total_tested} channels accessible across {len(discord_databases)} Discord server(s)")
        if total_tested > total_refreshed:
            console.print(f"ℹ️  {total_tested - total_refreshed} channels visible but not readable (missing permissions)", style="dim")
        console.print("💡 You can now use 'maia sync -b' or 'maia chat -b' to browse accessible channels only.")
        
    except Exception as e:
        console.print(f"❌ Error refreshing Discord channels: {e}", style="red")
        logger.error(f"Discord refresh error: {e}")

def setup_discord_commands(subparsers):
    """Set up Discord-related CLI commands."""
    
    # Main Discord command group
    discord_parser = subparsers.add_parser('discord', help='Discord integration commands')
    discord_subparsers = discord_parser.add_subparsers(dest='discord_command', help='Discord commands')
    
    # Registry sync commands
    registry_check_parser = discord_subparsers.add_parser('registry-check', help='Check for orphaned registry entries from renamed databases')
    registry_check_parser.set_defaults(func=handle_discord_registry_check)
    
    registry_sync_parser = discord_subparsers.add_parser('registry-sync', help='Sync registry entries when database names change')
    registry_sync_parser.add_argument('--dry-run', action='store_true', help='Show what would be updated without making changes')
    registry_sync_parser.set_defaults(func=handle_discord_registry_sync)
    

    
    # Browse command (new interactive browser)
    browse_parser = discord_subparsers.add_parser('browse', help='Interactive channel browser for Discord')
    browse_parser.add_argument('workspace', help='Workspace name')
    browse_parser.set_defaults(func=handle_discord_browse)
    
    # List channels command
    list_channels_parser = discord_subparsers.add_parser('list-channels', help='List Discord channels')
    list_channels_parser.add_argument('workspace', help='Workspace name')
    list_channels_parser.add_argument('--server-id', help='Discord server ID (optional if set during setup)')
    list_channels_parser.set_defaults(func=handle_discord_list_channels)
    
    # Debug channels command
    debug_channels_parser = discord_subparsers.add_parser('debug-channels', help='Debug Discord channel connectivity')
    debug_channels_parser.add_argument('--workspace', help='Workspace name (defaults to current workspace)')
    debug_channels_parser.set_defaults(func=handle_discord_debug_channels)
    
    # Refresh channels command
    refresh_parser = discord_subparsers.add_parser('refresh', help='Refresh accessible channel cache')
    refresh_parser.add_argument('workspace', help='Workspace name')
    refresh_parser.set_defaults(func=handle_discord_refresh)
    
    # Sync command
    sync_parser = discord_subparsers.add_parser('sync', help='Sync Discord messages')
    sync_parser.add_argument('database', help='Database name to sync')
    sync_parser.add_argument('--channel-id', required=True, help='Discord channel ID to sync')
    sync_parser.add_argument('--workspace', default=None, help='Workspace name')
    sync_parser.add_argument('--days', type=int, default=7, help='Number of days to sync back')
    sync_parser.add_argument('--limit', type=int, default=100, help='Maximum number of messages to sync')
    sync_parser.set_defaults(func=handle_discord_sync)

async def handle_discord_registry_check(args):
    """Handle 'maia discord registry-check' command."""
    console = Console()
    
    try:
        from promaia.config.database_registry_sync import get_database_registry_sync
        
        registry_sync = get_database_registry_sync()
        orphaned_entries = registry_sync.find_orphaned_registry_entries()
        
        if not orphaned_entries:
            print_text("✅ No orphaned registry entries found", style="bold green")
            print_text("🎯 All registry entries match current database configurations", style="dim")
            return
        
        print_text(f"📊 Found {len(orphaned_entries)} orphaned database(s) in registry:", style="bold yellow")
        print()
        
        # Create table of orphaned entries
        table = Table(title="Orphaned Registry Entries", box=box.ROUNDED)
        table.add_column("Old Database Name", style="red")
        table.add_column("Entry Count", style="yellow")
        table.add_column("Sample File Paths", style="dim")
        
        for db_name, entries in orphaned_entries.items():
            sample_paths = []
            for entry in entries[:3]:  # Show first 3 file paths
                path = entry.get("file_path", "Unknown")
                if len(path) > 50:
                    path = "..." + path[-47:]
                sample_paths.append(path)
            
            table.add_row(
                db_name,
                str(len(entries)) + ("+" if len(entries) >= 10 else ""),
                "\n".join(sample_paths)
            )
        
        console.print(table)
        console.print()
        
        # Get suggestions
        suggestions = registry_sync.suggest_database_mappings(orphaned_entries)
        
        if suggestions:
            print_text("💡 Suggested mappings:", style="bold blue")
            for old_name, new_name in suggestions.items():
                print_text(f"  {old_name} → {new_name}")
            
            print()
            print_text("💡 Run 'maia discord registry-sync --dry-run' to see what would be updated", style="dim")
            print_text("💡 Run 'maia discord registry-sync' to apply the mappings", style="dim")
        else:
            print_text("⚠️  No automatic mappings found. Manual intervention may be required.", style="yellow")
        
    except Exception as e:
        print_text(f"❌ Error checking registry: {e}", style="bold red")
        logger.error(f"Discord registry check failed: {e}", exc_info=True)

async def handle_discord_registry_sync(args):
    """Handle 'maia discord registry-sync' command."""
    console = Console()
    
    try:
        from promaia.config.database_registry_sync import get_database_registry_sync
        
        registry_sync = get_database_registry_sync()
        dry_run = args.dry_run
        
        if dry_run:
            print_text("🔍 Performing registry sync dry run (no changes will be made)...", style="bold blue")
        else:
            print_text("🚀 Starting Discord registry synchronization...", style="bold green")
        
        # Find orphaned entries and get suggestions
        orphaned_entries = registry_sync.find_orphaned_registry_entries()
        
        if not orphaned_entries:
            print_text("✅ No orphaned registry entries found", style="bold green")
            print_text("🎯 All registry entries match current database configurations", style="dim")
            return
        
        suggestions = registry_sync.suggest_database_mappings(orphaned_entries)
        
        if not suggestions:
            print_text("⚠️  No automatic mappings could be determined", style="bold yellow")
            print_text("💡 Run 'maia discord registry-check' to see orphaned entries", style="dim")
            return
        
        print_text(f"📊 Found {len(suggestions)} database mapping(s):", style="bold cyan")
        for old_name, new_name in suggestions.items():
            print_text(f"  {old_name} → {new_name}")
        print()
        
        # Apply the mappings
        results = registry_sync.sync_all_suggested_mappings(suggestions, dry_run=dry_run)
        
        print_text(f"📊 Registry Sync Results ({'' if not dry_run else 'DRY RUN '}Summary):", style="bold cyan")
        print_text(f"  • Total mappings processed: {results['total_mappings']}")
        print_text(f"  • Successful updates: {results['successful_updates']}")
        print_text(f"  • Failed updates: {results['failed_updates']}")
        print_text(f"  • Total registry entries updated: {results['total_entries_updated']}")
        
        # Show detailed results
        if results["update_results"]:
            print()
            for update_result in results["update_results"]:
                if dry_run:
                    count_key = "entries_would_update"
                    count_value = update_result.get(count_key, 0)
                    status = "🔍 WOULD UPDATE" if count_value > 0 else "ℹ️  NO ENTRIES"
                    print_text(f"{status} {update_result['old_name']} → {update_result['new_name']}")
                    print_text(f"  • Entries: {count_value}")
                else:
                    status = "✅ SUCCESS" if update_result.get("success") else "❌ FAILED"
                    print_text(f"{status} {update_result['old_name']} → {update_result['new_name']}")
                    print_text(f"  • Entries updated: {update_result.get('entries_updated', 0)}")
                    
                    if update_result.get("error"):
                        print_text(f"  • Error: {update_result['error']}", style="red")
                print()
        
        # Show any general errors
        if results.get("errors"):
            print_text("❌ Errors encountered:", style="bold red")
            for error in results["errors"]:
                print_text(f"  • {error}", style="red")
            print()
        
        if dry_run:
            print_text("💡 Run without --dry-run to perform the actual registry update", style="dim")
        elif results["successful_updates"] > 0:
            print_text("🎉 Registry synchronization completed successfully!", style="bold green")
            print_text("💡 You can now use renamed Discord databases without registry issues", style="dim")
        
    except Exception as e:
        print_text(f"❌ Error during registry sync: {e}", style="bold red")
        logger.error(f"Discord registry sync failed: {e}", exc_info=True)



def add_discord_workspace_commands(workspace_subparsers):
    """Add Discord setup to workspace commands."""
    discord_setup_parser = workspace_subparsers.add_parser('discord-setup', help='Set up Discord bot for workspace')
    discord_setup_parser.add_argument('workspace', help='Workspace name')
    discord_setup_parser.add_argument('--server-id', help='Discord server ID (optional)')
    discord_setup_parser.set_defaults(func=handle_discord_setup) 