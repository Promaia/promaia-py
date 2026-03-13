"""
CLI commands for syncing prompts from Notion to local files.
"""
import os
import logging
from datetime import datetime
from promaia.notion.prompts import sync_main_prompt_to_file, LOCAL_PROMPT_PATH
from promaia.config.workspaces import get_workspace_manager

logger = logging.getLogger(__name__)


def handle_prompt_sync(args):
    """Handle prompt sync command."""
    workspace_mgr = get_workspace_manager()

    # Use default workspace if not specified
    workspace = args.workspace or workspace_mgr.get_default_workspace()

    if not workspace:
        print("❌ No workspace specified and no default workspace configured")
        return

    # Validate workspace
    if not workspace_mgr.validate_workspace(workspace):
        print(f"❌ Workspace '{workspace}' is not valid or not enabled")
        return

    print(f"🔄 Syncing Main prompt from Notion workspace '{workspace}'...")

    success = sync_main_prompt_to_file(workspace=workspace, force=args.force)

    if success:
        print(f"✅ Successfully synced Main prompt to {LOCAL_PROMPT_PATH}")
        print("   Your changes from Notion are now available for chat sessions")
    else:
        print("❌ Failed to sync Main prompt from Notion")
        print("   Will use existing local prompt file if available")


def handle_prompt_status(args):
    """Handle prompt status command."""
    workspace_mgr = get_workspace_manager()

    # Use default workspace if not specified
    workspace = args.workspace or workspace_mgr.get_default_workspace()

    workspace_config = workspace_mgr.get_workspace(workspace) if workspace else None

    print(f"📋 Prompt Status for workspace: {workspace or 'default'}")
    print("")

    # Check workspace config
    if workspace_config and workspace_config.main_prompt_page_id:
        print(f"   Notion Page ID: {workspace_config.main_prompt_page_id}")
    else:
        print("   ⚠️  No Main prompt page ID configured for this workspace")

    # Check local file
    if os.path.exists(LOCAL_PROMPT_PATH):
        file_size = os.path.getsize(LOCAL_PROMPT_PATH)
        file_mtime = os.path.getmtime(LOCAL_PROMPT_PATH)
        file_age = datetime.now().timestamp() - file_mtime
        age_hours = file_age / 3600
        age_display = f"{age_hours:.1f} hours" if age_hours >= 1 else f"{file_age/60:.0f} minutes"

        print(f"   Local File: {LOCAL_PROMPT_PATH}")
        print(f"   File Size: {file_size:,} bytes")
        print(f"   Last Synced: {age_display} ago")

        if age_hours > 24:
            print(f"   ⚠️  File is older than 24 hours, consider syncing")
    else:
        print(f"   ⚠️  No local prompt file found at {LOCAL_PROMPT_PATH}")
        print("   Run 'maia prompt sync' to create it")


def add_prompt_commands(subparsers):
    """Add prompt management commands to the CLI."""
    prompt_parser = subparsers.add_parser('prompt', help='Manage Promaia prompts synced from Notion')
    prompt_subparsers = prompt_parser.add_subparsers(dest='prompt_action', help='Prompt actions')

    # Sync command
    sync_parser = prompt_subparsers.add_parser('sync', help='Sync Main prompt from Notion to local file')
    sync_parser.add_argument('--workspace', '-w', help='Workspace to sync prompt from (default: current workspace)')
    sync_parser.add_argument('--force', '-f', action='store_true', help='Force sync even if file is recent')
    sync_parser.set_defaults(func=handle_prompt_sync)

    # Status command
    status_parser = prompt_subparsers.add_parser('status', help='Check status of synced prompt file')
    status_parser.add_argument('--workspace', '-w', help='Workspace to check (default: current workspace)')
    status_parser.set_defaults(func=handle_prompt_status)
