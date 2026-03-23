"""
Workspace management commands for the Maia CLI.
"""
import asyncio
import argparse
import logging
from typing import List, Dict, Any, Optional

from promaia.config.workspaces import get_workspace_manager
from promaia.config.databases import get_database_manager

logger = logging.getLogger(__name__)

async def handle_workspace_list(args):
    """Handle 'maia workspace list' command."""
    workspace_manager = get_workspace_manager()
    include_archived = getattr(args, 'archived', False)
    workspaces = workspace_manager.list_workspaces(include_archived=include_archived)
    default_workspace = workspace_manager.get_default_workspace()

    if not workspaces:
        if include_archived:
            print("No workspaces configured.")
        else:
            print("No active workspaces configured.")
            print("Use --archived to see archived workspaces.")
        print("Add a workspace with: maia workspace add <name> --api-key <your_notion_token>")
        return

    print("Configured workspaces:")
    for workspace_name in workspaces:
        workspace = workspace_manager.get_workspace(workspace_name)
        status = "✓" if workspace.enabled else "✗"
        default_marker = " (default)" if workspace_name == default_workspace else ""
        archived_marker = " [ARCHIVED]" if workspace.archived else ""

        print(f"  {status} {workspace_name}{default_marker}{archived_marker}")
        if workspace.description:
            print(f"    Description: {workspace.description}")
        if workspace.archived and workspace.archived_at:
            from datetime import datetime
            archived_date = datetime.fromisoformat(workspace.archived_at).strftime("%Y-%m-%d")
            print(f"    Archived: {archived_date}", end="")
            if workspace.archived_reason:
                print(f" - {workspace.archived_reason}")
            else:
                print()

    print(f"\nDefault workspace: {default_workspace or 'None'}")

async def handle_workspace_add(args):
    """Handle 'maia workspace add' command."""
    workspace_manager = get_workspace_manager()

    name = args.name
    api_key = getattr(args, 'api_key', None)
    description = getattr(args, 'description', '')

    if workspace_manager.add_workspace(name, description):
        print(f"✓ Added workspace '{name}'")

        # Store credential via the unified auth module (not workspace config).
        if api_key:
            from promaia.auth import get_integration
            get_integration("notion").store_credential(api_key, workspace=name)
            print(f"✓ Stored Notion credentials for '{name}'")
        else:
            print(f"  Run 'maia auth configure notion' to add credentials")

        # Set as default if requested or if it's the first workspace
        if getattr(args, 'set_default', False) or not workspace_manager.get_default_workspace():
            workspace_manager.set_default_workspace(name)
            print(f"✓ Set '{name}' as default workspace")
    else:
        print(f"✗ Failed to add workspace '{name}' (already exists)")

async def handle_workspace_remove(args):
    """Handle 'maia workspace remove' command."""
    workspace_manager = get_workspace_manager()
    db_manager = get_database_manager()
    
    name = args.name
    
    # Check if workspace has databases
    workspace_databases = db_manager.get_workspace_databases(name)
    if workspace_databases and not getattr(args, 'force', False):
        print(f"✗ Workspace '{name}' has {len(workspace_databases)} databases:")
        for db in workspace_databases:
            print(f"  - {db.get_qualified_name()}")
        print("Use --force to remove workspace and all its databases")
        return
    
    # Remove databases first if force is used
    if workspace_databases and getattr(args, 'force', False):
        for db in workspace_databases:
            db_manager.remove_database(db.nickname, name)
        print(f"Removed {len(workspace_databases)} databases from workspace")
    
    if workspace_manager.remove_workspace(name):
        print(f"✓ Removed workspace '{name}'")
    else:
        print(f"✗ Failed to remove workspace '{name}' (not found)")

async def handle_workspace_set_default(args):
    """Handle 'maia workspace set-default' command."""
    workspace_manager = get_workspace_manager()
    
    name = args.name
    
    if workspace_manager.set_default_workspace(name):
        print(f"✓ Set '{name}' as default workspace")
    else:
        print(f"✗ Failed to set default workspace ('{name}' not found)")

async def handle_workspace_info(args):
    """Handle 'maia workspace info' command."""
    workspace_manager = get_workspace_manager()
    db_manager = get_database_manager()

    name = args.name
    workspace = workspace_manager.get_workspace(name)

    if not workspace:
        print(f"✗ Workspace '{name}' not found")
        return

    print(f"Workspace: {name}")
    print(f"Description: {workspace.description or 'None'}")
    print(f"Enabled: {workspace.enabled}")
    print(f"Archived: {workspace.archived}")
    if workspace.archived:
        if workspace.archived_at:
            from datetime import datetime
            archived_date = datetime.fromisoformat(workspace.archived_at).strftime("%Y-%m-%d %H:%M:%S")
            print(f"Archived At: {archived_date}")
        if workspace.archived_reason:
            print(f"Archive Reason: {workspace.archived_reason}")
    from promaia.auth import get_integration
    token = get_integration("notion").get_notion_credentials(name)
    if token:
        print(f"Credentials: {'*' * 30}...{token[-4:]}")
    else:
        print("Credentials: Not configured")
    print(f"Created: {workspace.created_at}")

    # Show databases in this workspace
    workspace_databases = db_manager.get_workspace_databases(name)
    print(f"\nDatabases ({len(workspace_databases)}):")
    if workspace_databases:
        for db in workspace_databases:
            sync_status = "✓" if db.sync_enabled else "✗"
            print(f"  {sync_status} {db.get_qualified_name()} - {db.description}")
    else:
        print("  None")

async def handle_workspace_test(args):
    """Handle 'maia workspace test' command."""
    workspace_manager = get_workspace_manager()

    name = args.name
    workspace = workspace_manager.get_workspace(name)

    if not workspace:
        print(f"✗ Workspace '{name}' not found")
        return

    from promaia.auth import get_integration
    token = get_integration("notion").get_notion_credentials(name)
    if not token:
        print(f"✗ Workspace '{name}' has no Notion credentials configured")
        print(f"  Run: maia auth configure notion")
        return

    # Test the credentials by making a simple request
    try:
        from notion_client import Client
        client = Client(auth=token)

        # Try to list users (minimal API call)
        response = client.users.list()
        print(f"✓ Workspace '{name}' API key is valid")
        print(f"  Connected to workspace with {len(response.get('results', []))} users")

    except Exception as e:
        print(f"✗ Workspace '{name}' API key test failed: {str(e)}")

async def handle_workspace_archive(args):
    """Handle 'maia workspace archive' command."""
    workspace_manager = get_workspace_manager()

    name = args.name
    reason = getattr(args, 'reason', '')

    workspace = workspace_manager.get_workspace(name)
    if not workspace:
        print(f"✗ Workspace '{name}' not found")
        return

    if workspace.archived:
        print(f"✗ Workspace '{name}' is already archived")
        return

    if workspace_manager.archive_workspace(name, reason):
        print(f"✓ Archived workspace '{name}'")
        if reason:
            print(f"  Reason: {reason}")
        print("\nWhat happens when a workspace is archived:")
        print("  • Stops syncing automatically")
        print("  • Hidden from browser and context by default")
        print("  • Excluded from mail processing")
        print("  • Data preserved - can still access with explicit -ws flag")
        print(f"\nTo unarchive: maia workspace unarchive {name}")
    else:
        print(f"✗ Failed to archive workspace '{name}'")

async def handle_workspace_unarchive(args):
    """Handle 'maia workspace unarchive' command."""
    workspace_manager = get_workspace_manager()

    name = args.name

    workspace = workspace_manager.get_workspace(name)
    if not workspace:
        print(f"✗ Workspace '{name}' not found")
        return

    if not workspace.archived:
        print(f"✗ Workspace '{name}' is not archived")
        return

    if workspace_manager.unarchive_workspace(name):
        print(f"✓ Unarchived workspace '{name}'")
        print("\nWorkspace is now active:")
        print("  • Will sync automatically")
        print("  • Visible in browser and context")
        print("  • Included in mail processing")
    else:
        print(f"✗ Failed to unarchive workspace '{name}'")

async def handle_workspace_setup_promaia(args):
    """Handle 'maia workspace setup-promaia' command."""
    from promaia.agents.notion_setup import setup_promaia_page

    workspace_manager = get_workspace_manager()

    # Get workspace name (use provided or default)
    workspace_name = getattr(args, 'workspace', None) or workspace_manager.get_default_workspace()

    if not workspace_name:
        print("✗ No workspace specified and no default workspace set")
        print("  Add a workspace first: maia workspace add <name> --api-key <token>")
        return

    # Validate workspace exists
    workspace = workspace_manager.get_workspace(workspace_name)
    if not workspace:
        print(f"✗ Workspace '{workspace_name}' not found")
        print(f"  Available workspaces: {', '.join(workspace_manager.list_workspaces())}")
        return

    try:
        print(f"\nSetting up Promaia page for workspace: {workspace_name}")
        promaia_page_id, main_prompt_page_id = await setup_promaia_page(workspace_name)

        print(f"\n✅ Promaia page setup complete!")
        print(f"   Promaia page ID: {promaia_page_id}")
        print(f"   Main prompt page ID: {main_prompt_page_id}")
        print(f"\n💡 Your main prompt will now sync from Notion automatically")
        print(f"   The local prompt file will be used as fallback")

    except Exception as e:
        print(f"\n✗ Failed to set up Promaia page: {str(e)}")
        logger.exception("Error in setup-promaia command")

def add_workspace_commands(subparsers):
    """Add workspace management commands to CLI."""
    workspace_parser = subparsers.add_parser('workspace', help='Manage Notion workspaces')
    workspace_subparsers = workspace_parser.add_subparsers(dest='workspace_command', required=True)
    add_workspace_commands_to_existing_parser(workspace_parser, workspace_subparsers)

def add_workspace_commands_to_existing_parser(parent_parser, subparsers):
    """Helper function to add workspace subcommands to any parser with aliases."""
    
    # List workspaces
    list_parser = subparsers.add_parser('list', help='List all configured workspaces')
    list_parser.add_argument('--archived', action='store_true', help='Include archived workspaces')
    list_parser.set_defaults(func=handle_workspace_list)

    # Add 'ls' alias for list
    ls_parser = subparsers.add_parser('ls', help='List all configured workspaces (alias for list)')
    ls_parser.add_argument('--archived', action='store_true', help='Include archived workspaces')
    ls_parser.set_defaults(func=handle_workspace_list)
    
    # Add workspace
    add_parser = subparsers.add_parser('add', help='Add a new workspace')
    add_parser.add_argument('name', help='Workspace name/nickname')
    add_parser.add_argument('--api-key', dest='api_key', help='Notion API key (can also set later via: maia auth configure notion)')
    add_parser.add_argument('--description', help='Optional description')
    add_parser.add_argument('--set-default', action='store_true', help='Set as default workspace')
    add_parser.set_defaults(func=handle_workspace_add)
    
    # Remove workspace
    remove_parser = subparsers.add_parser('remove', help='Remove a workspace')
    remove_parser.add_argument('name', help='Workspace name to remove')
    remove_parser.add_argument('--force', action='store_true', help='Force removal even if databases exist')
    remove_parser.set_defaults(func=handle_workspace_remove)
    
    # Add 'rm' alias for remove
    rm_parser = subparsers.add_parser('rm', help='Remove a workspace (alias for remove)')
    rm_parser.add_argument('name', help='Workspace name to remove')
    rm_parser.add_argument('--force', action='store_true', help='Force removal even if databases exist')
    rm_parser.set_defaults(func=handle_workspace_remove)
    
    # Set default workspace
    default_parser = subparsers.add_parser('set-default', help='Set default workspace')
    default_parser.add_argument('name', help='Workspace name to set as default')
    default_parser.set_defaults(func=handle_workspace_set_default)
    
    # Workspace info
    info_parser = subparsers.add_parser('info', help='Show workspace information')
    info_parser.add_argument('name', help='Workspace name')
    info_parser.set_defaults(func=handle_workspace_info)
    
    # Test workspace connection
    test_parser = subparsers.add_parser('test', help='Test workspace API connection')
    test_parser.add_argument('name', help='Workspace name to test')
    test_parser.set_defaults(func=handle_workspace_test)

    # Archive workspace
    archive_parser = subparsers.add_parser('archive', help='Archive a workspace (stops syncing, hides from context)')
    archive_parser.add_argument('name', help='Workspace name to archive')
    archive_parser.add_argument('--reason', help='Reason for archiving (optional)')
    archive_parser.set_defaults(func=handle_workspace_archive)

    # Unarchive workspace
    unarchive_parser = subparsers.add_parser('unarchive', help='Unarchive a workspace (re-enables syncing)')
    unarchive_parser.add_argument('name', help='Workspace name to unarchive')
    unarchive_parser.set_defaults(func=handle_workspace_unarchive)

    # Setup Promaia page
    setup_promaia_parser = subparsers.add_parser('setup-promaia', help='Set up Promaia page (main prompt and resources)')
    setup_promaia_parser.add_argument('--workspace', '-ws', help='Workspace name (uses default if not specified)')
    setup_promaia_parser.set_defaults(func=handle_workspace_setup_promaia)

    # Gmail setup (optional)
    try:
        from promaia.cli.gmail_commands import add_workspace_gmail_commands
        add_workspace_gmail_commands(subparsers)
    except ImportError:
        pass  # Gmail commands not available 
    
    # Discord setup (optional)
    try:
        from promaia.cli.discord_commands import add_discord_workspace_commands
        add_discord_workspace_commands(subparsers)
    except ImportError:
        pass  # Discord commands not available 