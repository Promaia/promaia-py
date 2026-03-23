#!/usr/bin/env python
"""
Command Line Interface for Maia.
"""
import os
import asyncio
import argparse
import glob
import re
import warnings

# Suppress google-generativeai deprecation noise (migration to google-genai tracked separately)
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\.generativeai")
# Suppress aiohttp unclosed session warnings (transitive dependency GC timing issue)
warnings.filterwarnings("ignore", category=ResourceWarning, message=r"unclosed.*<aiohttp")
from typing import List, Dict, Any, Optional
import traceback
from datetime import datetime, timedelta
import logging
import sys

# Import modules for CMS functionality
from promaia.notion.client import ensure_default_client
from promaia.notion.pages import get_pages_by_date, get_page_title, get_block_content, clear_block_cache
from promaia.notion.journal_router import handle_journal_pull_date_range, handle_journal_pull_with_sub_pages
from promaia.markdown.converter import page_to_markdown
from promaia.storage.files import save_page_to_file, get_existing_page_ids
from promaia.utils.config import update_last_sync_time, get_last_sync_time, get_sync_days_setting, set_sync_days_setting, load_environment, get_config, update_config
from promaia.utils.timezone_utils import now_utc

# Load environment variables from .env file at startup
load_environment()
from promaia.utils.config_loader import get_notion_database_id
from promaia.utils.display import print_text, print_markdown, print_separator

# Import database management commands
from promaia.cli.database_commands import (
    handle_database_list, handle_database_add, handle_database_remove,
    handle_database_test, handle_database_sync, handle_database_info,
    handle_database_push, handle_database_status, handle_database_list_sources,
    handle_register_markdown_files, handle_validate_registry,
    handle_database_add_channels, handle_database_remove_channels,
    handle_database_remove_with_data_purge, handle_database_remove_interactive,
    handle_channel_remove_interactive,
    handle_database_channel_list, handle_database_edit,
    add_database_commands, add_database_commands_to_existing_parser
)
from promaia.cli.conversion_commands import add_conversion_commands
# from promaia.cli.edit_commands import edit  # Remove this import as we're using argparse handlers

# Import newsletter commands
from promaia.newsletter.commands import newsletter_sync_command, newsletter_test_command

# Import mail commands
from promaia.cli.mail_commands import add_mail_commands

# Import Gmail commands
from promaia.cli.gmail_commands import add_gmail_commands

# Import prompt sync commands
from promaia.cli.prompt_sync_commands import add_prompt_commands

# Import agent commands
from promaia.cli.agent_commands import add_agent_commands
# Note: scheduled_agent_commands lazy-loaded (below) to keep imports clean

# Import daemon commands
from promaia.cli.daemon_commands import add_daemon_commands

# Import feed commands
from promaia.cli.feed_commands import add_feed_commands

# Import TUI commands
from promaia.cli.tui_commands import add_tui_commands

# Import team management commands
from promaia.cli.team_commands import add_team_commands

# Import setup wizard commands
from promaia.cli.setup_commands import add_setup_commands

# Import service management commands
from promaia.cli.service_commands import add_service_commands

# Import auth management commands
from promaia.cli.auth_commands import add_auth_commands

# Import conversation management commands
from promaia.cli.conversation_commands import (
    add_conversation_commands, add_conversation_commands_to_existing_parser
)

# Import workspace commands
from promaia.cli.workspace_commands import (
    add_workspace_commands, add_workspace_commands_to_existing_parser
)

# Import migration commands
from promaia.cli.migration_commands import (
    add_migration_commands, add_migration_commands_to_existing_parser
)

# Load environment variables
load_environment()

# Initialize console for rich output - Replaced with standard print
# console = Console()

# Import query parsing utilities
from promaia.utils.query_parsing import parse_vs_queries_with_params

# ==================== EDIT COMMAND HANDLERS ====================

def handle_edit_list_pages(args):
    """Handle edit list-pages command"""
    import os
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime
    from promaia.storage.json_editor import NotionJSONEditor
    
    console = Console(width=9999, soft_wrap=False)
    
    try:
        editor = NotionJSONEditor()
        content_dir = editor._get_content_type_dir(args.content_type)
        
        if not os.path.exists(content_dir):
            console.print(f"[red]No data found for content type: {args.content_type}[/red]")
            return
        
        table = Table(title=f"Pages in {args.content_type}")
        table.add_column("Title", style="cyan")
        table.add_column("Page ID", style="green")
        table.add_column("Last Modified", style="yellow")
        table.add_column("Sync Status", style="magenta")
        
        for filename in sorted(os.listdir(content_dir)):
            if filename.endswith('.json') and 'backup' not in filename:
                try:
                    import json
                    with open(os.path.join(content_dir, filename), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    title = data.get('title', 'Unknown')
                    page_id_val = data.get('page_id', 'Unknown')
                    saved_at = data.get('saved_at', 'Unknown')
                    last_synced = data.get('last_synced', 'Never')
                    
                    # Apply filters
                    if args.page_id and args.page_id not in page_id_val:
                        continue
                    if args.title_filter and args.title_filter.lower() not in title.lower():
                        continue
                    
                    # Determine sync status
                    if last_synced == 'Never':
                        sync_status = "[red]Not synced[/red]"
                    else:
                        saved_dt = datetime.fromisoformat(saved_at)
                        synced_dt = datetime.fromisoformat(last_synced)
                        if saved_dt > synced_dt:
                            sync_status = "[yellow]Modified[/yellow]"
                        else:
                            sync_status = "[green]Synced[/green]"
                    
                    table.add_row(title[:50], page_id_val, saved_at[:19], sync_status)
                    
                except Exception as e:
                    console.print(f"[red]Error reading {filename}: {e}[/red]")
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

def handle_edit_update(args):
    """Handle edit update command"""
    from rich.console import Console
    from promaia.storage.json_editor import NotionJSONEditor
    
    console = Console(width=9999, soft_wrap=False)
    
    try:
        editor = NotionJSONEditor()
        
        # Load the page
        data = editor.load_page(args.content_type, args.page_id)
        console.print(f"[green]Loaded page: {data['title']}[/green]")
        
        # Update title if provided
        if args.title:
            data = editor.update_title(data, args.title)
            console.print(f"[cyan]Updated title to: {args.title}[/cyan]")
        
        # Update properties if provided
        if args.property:
            for prop in args.property:
                if '=' not in prop:
                    console.print(f"[red]Invalid property format: {prop}. Use 'Name=Value'[/red]")
                    continue
                
                prop_name, prop_value = prop.split('=', 1)
                
                # Handle different property types (simplified)
                if prop_name in data['notion_data']['properties']:
                    prop_type = data['notion_data']['properties'][prop_name]['type']
                    
                    if prop_type == 'select':
                        prop_value = {"name": prop_value, "color": "default"}
                    elif prop_type == 'rich_text':
                        prop_value = [{
                            "type": "text",
                            "text": {"content": prop_value},
                            "plain_text": prop_value
                        }]
                else:
                    # Default to rich_text for new properties
                    prop_type = 'rich_text'
                    prop_value = [{
                        "type": "text", 
                        "text": {"content": prop_value},
                        "plain_text": prop_value
                    }]
                
                data = editor.update_property(data, prop_name, prop_value, prop_type)
                console.print(f"[cyan]Updated property {prop_name}: {str(prop_value)[:50]}[/cyan]")
        
        # Add content blocks if provided
        if args.add_paragraph:
            block = editor.create_paragraph_block(args.add_paragraph)
            data = editor.add_content_block(data, block)
            console.print(f"[cyan]Added paragraph: {args.add_paragraph[:50]}[/cyan]")
        
        if args.add_heading:
            block = editor.create_heading_block(args.add_heading, args.heading_level)
            data = editor.add_content_block(data, block)
            console.print(f"[cyan]Added heading {args.heading_level}: {args.add_heading[:50]}[/cyan]")
        
        # Save the changes
        filepath = editor.save_page(data, backup=not args.no_backup)
        console.print(f"[green]Saved changes to: {filepath}[/green]")
        
        # Show changes summary
        changes = editor.get_changes_summary()
        if changes:
            console.print("\n[bold]Changes made:[/bold]")
            for change in changes:
                console.print(f"  • {change['description']}")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

def handle_edit_show(args):
    """Handle edit show command"""
    from rich.console import Console
    from rich.tree import Tree
    from promaia.storage.json_editor import NotionJSONEditor
    
    console = Console(width=9999, soft_wrap=False)
    
    try:
        editor = NotionJSONEditor()
        data = editor.load_page(args.content_type, args.page_id)
        
        console.print(f"[bold cyan]Page: {data['title']}[/bold cyan]")
        console.print(f"[dim]ID: {data['page_id']}[/dim]")
        console.print(f"[dim]Type: {data['content_type']}[/dim]")
        console.print(f"[dim]Last saved: {data['saved_at']}[/dim]")
        console.print(f"[dim]Last synced: {data.get('last_synced', 'Never')}[/dim]")
        
        # Show properties
        console.print("\n[bold]Properties:[/bold]")
        props = data['notion_data']['properties']
        for name, prop in props.items():
            prop_type = prop.get('type', 'unknown')
            value = prop.get(prop_type, 'N/A')
            
            # Format value based on type
            if prop_type == 'title' and isinstance(value, list) and value:
                display_value = value[0].get('plain_text', 'N/A')
            elif prop_type == 'rich_text' and isinstance(value, list) and value:
                display_value = value[0].get('plain_text', 'N/A')
            elif prop_type == 'select' and isinstance(value, dict):
                display_value = value.get('name', 'N/A')
            elif prop_type == 'relation' and isinstance(value, list):
                display_value = f"{len(value)} related items"
            else:
                display_value = str(value)[:100]
            
            console.print(f"  [cyan]{name}[/cyan] ({prop_type}): {display_value}")
        
        # Show content summary
        content = data['notion_data']['content']
        console.print(f"\n[bold]Content blocks: {len(content)}[/bold]")
        
        if content:
            tree = Tree("Content Structure")
            for i, block in enumerate(content[:10]):  # Show first 10 blocks
                block_type = block.get('type', 'unknown')
                block_content = ""
                
                if block_type == 'paragraph' and 'paragraph' in block:
                    rich_text = block['paragraph'].get('rich_text', [])
                    if rich_text:
                        block_content = rich_text[0].get('plain_text', '')[:50]
                elif block_type.startswith('heading_') and block_type in block:
                    rich_text = block[block_type].get('rich_text', [])
                    if rich_text:
                        block_content = rich_text[0].get('plain_text', '')[:50]
                
                tree.add(f"[{i}] {block_type}: {block_content}")
            
            if len(content) > 10:
                tree.add(f"... and {len(content) - 10} more blocks")
            
            console.print(tree)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

def handle_edit_sync(args):
    """Handle edit sync command"""
    from rich.console import Console
    from rich.table import Table
    from promaia.storage.notion_sync import NotionSyncer
    from promaia.utils.config import get_config
    
    console = Console(width=9999, soft_wrap=False)
    
    try:
        syncer = NotionSyncer()
        
        if args.dry_run:
            console.print("[yellow]DRY RUN - No changes will be made[/yellow]\n")
            
            if args.content_type:
                plan = syncer.create_sync_plan(args.content_type)
            else:
                plan = syncer.create_sync_plan()
            
            if not plan:
                console.print("[green]No pages need syncing[/green]")
                return
            
            console.print("[bold]Sync Plan:[/bold]")
            for ct, pages in plan.items():
                console.print(f"\n[cyan]{ct}:[/cyan]")
                for page_id in pages:
                    console.print(f"  • {page_id}")
            return
        
        if args.page_id:
            # Sync single page
            if not args.content_type:
                console.print("[red]Content type required when syncing specific page[/red]")
                return
            
            console.print(f"[yellow]Syncing page {args.page_id} in {args.content_type}...[/yellow]")
            result = syncer.sync_page(args.content_type, args.page_id, force=args.force)
            
            if result.success:
                console.print(f"[green]✓ Successfully synced {result.changes_applied} changes[/green]")
            else:
                console.print(f"[red]✗ Sync failed[/red]")
                for error in result.errors:
                    console.print(f"  [red]Error: {error}[/red]")
                for conflict in result.conflicts:
                    console.print(f"  [yellow]Conflict: {conflict}[/yellow]")
        
        elif args.content_type:
            # Sync entire database
            console.print(f"[yellow]Syncing all pages in {args.content_type}...[/yellow]")
            results = syncer.sync_database(args.content_type, force=args.force)
            
            success_count = sum(1 for r in results if r.success)
            total_count = len(results)
            
            console.print(f"\n[bold]Sync Results: {success_count}/{total_count} successful[/bold]")
            
            for result in results:
                if result.success:
                    console.print(f"[green]✓ {result.page_id}: {result.changes_applied} changes[/green]")
                else:
                    console.print(f"[red]✗ {result.page_id}: Failed[/red]")
                    for error in result.errors[:2]:  # Show first 2 errors
                        console.print(f"    [red]{error}[/red]")
        
        else:
            # Sync all databases
            config = get_config()
            all_results = []
            
            for ct in config.keys():
                console.print(f"[yellow]Syncing {ct}...[/yellow]")
                results = syncer.sync_database(ct, force=args.force)
                all_results.extend(results)
            
            success_count = sum(1 for r in all_results if r.success)
            total_count = len(all_results)
            
            console.print(f"\n[bold]Total Sync Results: {success_count}/{total_count} successful[/bold]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

async def handle_discord_bot(args):
    """Handle discord-bot command to start the Promaia Discord bot."""
    import asyncio
    from promaia.discord.bot import run_bot

    print(f"🤖 Starting Promaia Discord bot for workspace: {args.workspace}")
    print("Press Ctrl+C to stop the bot")

    try:
        await run_bot(workspace=args.workspace, token=args.token)
    except KeyboardInterrupt:
        print("\n✅ Bot stopped")
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        import traceback
        traceback.print_exc()

def handle_edit_status(args):
    """Handle edit status command"""
    from rich.console import Console
    from rich.table import Table
    from promaia.storage.notion_sync import NotionSyncer
    from promaia.utils.config import get_config
    
    console = Console(width=9999, soft_wrap=False)
    
    try:
        syncer = NotionSyncer()
        
        if args.content_type:
            content_types = [args.content_type]
        else:
            config = get_config()
            content_types = list(config.keys())
        
        table = Table(title="Sync Status")
        table.add_column("Database", style="cyan")
        table.add_column("Modified Pages", style="yellow")
        table.add_column("Status", style="green")
        
        for ct in content_types:
            modified = syncer.get_modified_pages(ct)
            if modified:
                status_text = f"[yellow]{len(modified)} need sync[/yellow]"
                pages_text = ", ".join(modified[:3])
                if len(modified) > 3:
                    pages_text += f" and {len(modified) - 3} more"
            else:
                status_text = "[green]All synced[/green]"
                pages_text = "None"
            
            table.add_row(ct, pages_text, status_text)
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

# ==================== JOURNAL COMMAND HANDLERS ====================

logger = logging.getLogger(__name__) # Module-level logger

async def handle_journal_pull(args):
    """Handles 'maia journal pull' command."""
    days_arg = args.days
    force_pull_value = args.force # Get the force flag
    summarize_journal = args.summarize # Get summarize flag
    start_date = getattr(args, 'start_date', None)
    end_date = getattr(args, 'end_date', None)
    chunk_days = getattr(args, 'chunk_days', 3)
    no_chunking = getattr(args, 'no_chunking', False)
    
    # Sub-page functionality
    include_sub_pages = getattr(args, 'include_sub_pages', False)
    disable_sub_pages = getattr(args, 'disable_sub_pages', False)
    max_sub_page_depth = getattr(args, 'max_sub_page_depth', 3)
    
    # Determine final sub-page setting
    if disable_sub_pages:
        use_sub_pages = False
        logger.info("Sub-page syncing explicitly disabled via --disable-sub-pages")
    elif include_sub_pages:
        use_sub_pages = True
        logger.info(f"Sub-page syncing enabled via --include-sub-pages (max depth: {max_sub_page_depth})")
    else:
        use_sub_pages = False  # Default to disabled unless explicitly enabled
    
    if force_pull_value:
        logger.info(f"--force flag detected. Journal entries will be re-fetched from Notion ignoring last sync time optimizations and modification times.")

    logger.info("Starting journal pull from Notion")

    try:
        database_id = get_notion_database_id("journal")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error loading Notion database ID for journal: {e}")
        logger.error("Please ensure 'NOTION_JOURNAL_DATABASE_ID' environment variable is set.")
        return

    logger.info(f"Using database ID: {database_id}")

    # Check if date range mode is being used
    if start_date and end_date:
        # Date range mode - for now, date range mode doesn't support sub-pages
        if use_sub_pages:
            logger.warning("Sub-page syncing is not yet supported in date range mode. Using standard sync.")
            use_sub_pages = False
            
        logger.info(f"Date range mode: {start_date} to {end_date} (chunking: {not no_chunking}, chunk_days: {chunk_days})")
        
        try:
            # Validate date formats
            from datetime import datetime
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError as e:
            logger.error(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
            return
        
        try:
            (journal_files, summary_files, 
             saved_originals_count, saved_summaries_count, 
             skipped_originals_count) = await handle_journal_pull_date_range(
                database_id=database_id,
                start_date=start_date,
                end_date=end_date,
                use_chunking=not no_chunking,
                chunk_days=chunk_days,
                force_pull=force_pull_value,
                summarize_flag=summarize_journal
            )
            
            # Log results for date range mode
            if saved_originals_count > 0:
                logger.info(f"Successfully saved {saved_originals_count} new/updated original journal entries:")
                for filepath in journal_files:
                    logger.info(f"  - {filepath}")
            
            if skipped_originals_count > 0:
                logger.info(f"Skipped {skipped_originals_count} original journal entries (already up-to-date).")
            
            if not saved_originals_count and not skipped_originals_count:
                 logger.info("No original journal entries found or processed in the specified date range.")

            if summarize_journal:
                if saved_summaries_count > 0:
                    logger.info(f"Successfully generated {saved_summaries_count} summaries:")
                    for filepath in summary_files:
                        logger.info(f"  - {filepath}")
                elif journal_files:
                    logger.info("No entries were newly summarized (or summaries failed to generate).")
            
            logger.info("Date range journal pull completed successfully!")
            return
            
        except Exception as e:
            logger.error(f"Error processing journal entries in date range: {str(e)}")
            if os.getenv("MAIA_DEBUG") == "1":
                traceback.print_exc()
            return
    elif start_date or end_date:
        logger.error("Both --start-date and --end-date must be provided for date range mode.")
        return

    # Continue with original logic for non-date-range mode
    days_to_process = None
    fetch_all_pages = False
    specific_date = None # Ensure specific_date is initialized

    # Simplified days_arg parsing logic, assuming journal_router or pages.py handles detailed interpretation
    if days_arg is not None:
        if isinstance(days_arg, str) and days_arg.lower() == 'all':
            logger.info("Processing ALL journal entries from Notion.")
            fetch_all_pages = True
        elif isinstance(days_arg, str):
            try: # Check if it's a date
                datetime.strptime(days_arg, "%Y-%m-%d")
                specific_date = days_arg
                days_to_process = None # Explicitly None if specific_date is used
                logger.info(f"Processing journal entries for specific date: {specific_date}")
            except ValueError:
                try: # Try as int
                    days_to_process = int(days_arg)
                    if days_to_process < 0:
                        logger.warning(f"Invalid value for --days ('{days_arg}'). Using default.")
                        days_to_process = get_sync_days_setting()
                    else:
                        logger.info(f"Processing journal entries from the last {days_to_process} days.")
                except ValueError:
                    logger.warning(f"Invalid format for --days: '{days_arg}'. Using default.")
                    days_to_process = get_sync_days_setting()
        elif isinstance(days_arg, int):
            if days_to_process < 0:
                logger.warning(f"Invalid value for --days ('{days_arg}'). Using default.")
                days_to_process = get_sync_days_setting()
            else:
                days_to_process = days_arg
                logger.info(f"Processing journal entries from the last {days_to_process} days.")    
        else:
            logger.warning(f"Unhandled --days format: '{days_arg}'. Using default.")
            days_to_process = get_sync_days_setting()
    else:
        days_to_process = get_sync_days_setting()
        logger.info(f"No --days specified, using default sync days setting for journal: {days_to_process} days.")

    if summarize_journal:
        logger.info("--summarize flag detected. Summaries will be generated for journal entries.")

    try:
        # MODIFIED: Capture new return values from handle_journal_pull
        if use_sub_pages:
            # Use enhanced journal pull with sub-page support
            (journal_files, summary_files, 
             saved_originals_count, saved_summaries_count, 
             skipped_originals_count) = await handle_journal_pull_with_sub_pages(
                database_id=database_id,
                days=days_to_process,
                specific_date=specific_date, 
                fetch_all=fetch_all_pages,
                force_pull=force_pull_value,
                summarize_flag=summarize_journal,
                include_sub_pages=use_sub_pages,
                max_sub_page_depth=max_sub_page_depth
            )
        else:
            # Standard journal pull not implemented - use handle_journal_pull_with_sub_pages instead
            logger.error("Standard journal pull without sub_pages is deprecated. Use --sub-pages flag.")
            return
        
        if saved_originals_count > 0:
            logger.info(f"Successfully saved {saved_originals_count} new/updated original journal entries:")
            for filepath in journal_files: # journal_files now only contains newly saved/updated ones
                logger.info(f"  - {filepath}")
        
        if skipped_originals_count > 0:
            logger.info(f"Skipped {skipped_originals_count} original journal entries (already up-to-date).")
        
        if not saved_originals_count and not skipped_originals_count:
             logger.info("No original journal entries found or processed based on criteria.")

        if summarize_journal:
            if saved_summaries_count > 0:
                logger.info(f"Successfully generated {saved_summaries_count} summaries:")
                for filepath in summary_files: # summary_files only contains newly generated ones
                    logger.info(f"  - {filepath}")
            elif journal_files: # Only print if originals were processed but no summaries made
                logger.info("No entries were newly summarized (or summaries failed to generate).")
        
        # Only update last_sync_time if it was not a forced pull (and not fetching all, which implies force)
        if not force_pull_value and not fetch_all_pages and not specific_date:
            update_last_sync_time() # Consider if this should be journal-specific
            logger.info("Last sync time updated.")
        elif force_pull_value:
            logger.info("Last sync time NOT updated due to --force flag.")
        elif fetch_all_pages:
             logger.info("Last sync time NOT updated because all entries were fetched.")
        elif specific_date:
             logger.info("Last sync time NOT updated because a specific date was processed.")

        logger.info("Journal pull completed successfully!")
        
    except Exception as e:
        logger.error(f"Error processing journal entries: {str(e)}")
        if os.getenv("MAIA_DEBUG") == "1": # Check environment variable
            traceback.print_exc()

async def handle_journal_summarize_file(args):
    """Handles 'maia journal summarize-file' command to summarize a specific journal file."""
    # Summarize functionality has been removed/deprecated
    logger.error("Journal summarize functionality is no longer available.")
    logger.info("This feature has been deprecated and removed from the codebase.")
    return
    
    # Dead code below - keeping for reference but unreachable
    # from promaia.summarize.interface import summarize_journal_entry, save_summary_entry
    
    file_path = args.file_path
    force = args.force
    
    if not os.path.exists(file_path):
        logger.error(f"File does not exist: {file_path}")
        return
    
    logger.info(f"Reading journal entry from: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        file_name = os.path.basename(file_path)
        logger.info(f"Summarizing entry: {file_name}")
        
        # Get the date from the filename
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', file_name)
        if not date_match:
            logger.warning(f"Could not extract date from filename: {file_name}. Using filename as title.")
            title = file_name
        else:
            title = date_match.group(1)
            
        # Generate summary
        summarized_content = await summarize_journal_entry(content, title)
        
        # Save summary
        summary_path = save_summary_entry(summarized_content, title)
        logger.info(f"Summary saved to: {summary_path}")
        
        # Show the first 200 characters of the summary
        summary_preview = summarized_content[:200] + "..." if len(summarized_content) > 200 else summarized_content
        logger.info(f"Summary Preview:\n{summary_preview}")
        
    except Exception as e:
        logger.error(f"Failed to summarize file: {e}")
        if os.getenv("MAIA_DEBUG") == "1":
            traceback.print_exc()

# ==================== CMS COMMAND HANDLERS ====================

async def pull_db_pages(database_id: str, output_dir: str, days: Optional[int] = None, content_type: str = "pages", fetch_all: bool = False, force_pull: bool = False):
    os.makedirs(output_dir, exist_ok=True)
    existing_page_ids = get_existing_page_ids(output_dir)
    logger.info(f"Found {len(existing_page_ids)} existing local '{content_type}' entries in {output_dir}.")
    
    last_sync = None
    if not fetch_all and not force_pull:
        last_sync = get_last_sync_time(content_type)
        logger.info(f"Last sync time for '{content_type}': {last_sync if last_sync else 'Never'}")
    elif force_pull:
        logger.info(f"Force pull enabled, ignoring last sync time for '{content_type}'.")

    logger.info(f"Querying Notion for '{content_type}' entries...")
    try:
        pages = await get_pages_by_date(database_id, days=days, fetch_all=fetch_all, last_sync_time_override=last_sync, force_pull=force_pull, content_type=content_type)
        logger.info(f"Successfully retrieved {len(pages)} pages from Notion for '{content_type}'.")
    except Exception as e:
        logger.error(f"Error retrieving pages from Notion for '{content_type}': {str(e)}")
        return

    pages_to_sync_ids = []
    skipped_count = 0

    if fetch_all or force_pull:
        pages_to_sync_ids = [page["id"] for page in pages]
        logger.info(f"Marked all {len(pages_to_sync_ids)} retrieved '{content_type}' pages for sync (fetch_all={fetch_all}, force_pull={force_pull}).")
    else:
        for page in pages:
            page_id = page["id"]
            last_edited_time_str = page.get("last_edited_time")
            last_edited_time_dt = None
            if last_edited_time_str:
                try:
                    last_edited_time_dt = datetime.fromisoformat(last_edited_time_str.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning(f"Could not parse last_edited_time '{last_edited_time_str}' for page {page_id}")
            
            if page_id not in existing_page_ids or not last_sync or (last_edited_time_dt and last_edited_time_dt > last_sync):
                pages_to_sync_ids.append(page_id)
            else:
                skipped_count +=1
        logger.info(f"Found {len(pages_to_sync_ids)} '{content_type}' entries to pull (skipped {skipped_count} unmodified).")

    if not pages_to_sync_ids:
        logger.info(f"No '{content_type}' entries to pull based on the criteria.")
    else:
        logger.info(f"Pulling {len(pages_to_sync_ids)} '{content_type}' entries...")
        for i, page_id in enumerate(pages_to_sync_ids, 1):
            try:
                logger.info(f"  [{i}/{len(pages_to_sync_ids)}] Fetching & saving: {page_id}")
                clear_block_cache()
                title = await get_page_title(page_id)
                blocks = await get_block_content(page_id)
                markdown_content = page_to_markdown(blocks)
                filepath = await save_page_to_file(page_id, title, markdown_content, content_type)
                logger.info(f"  [{i}/{len(pages_to_sync_ids)}] ✓ Saved: {filepath}")
            except Exception as e:
                logger.error(f"  [{i}/{len(pages_to_sync_ids)}] ERROR processing page {page_id}: {e}")
                if os.getenv("MAIA_DEBUG") == "1":
                    traceback.print_exc()
    
    if not fetch_all and not force_pull:
        update_last_sync_time(content_type)

async def handle_cms_pull(args):
    """Handles 'maia cms pull' command - pulls CMS entries for KOii chat context."""
    logger.info("Starting CMS pull for KOii chat context")
    
    try:
        from promaia.config.databases import get_database_config
        database_config = get_database_config("cms")
        if not database_config:
            raise ValueError("CMS database not found in configuration")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error loading CMS database configuration: {e}")
        return

    await pull_cms_filtered(
        database_config=database_config,
        output_dir="KOii-chat-context", 
        property_filters={"KOii chat": True},
        days=args.days,
        force=args.force,
        description="KOii chat context"
    )

async def handle_cms_push(args):
    """Handles 'maia cms push' command."""
    logger.info(f"Initiating push to CMS")
    
    title = args.title

    try:
        database_id = get_notion_database_id("cms")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error loading Notion database ID for CMS: {e}")
        return

    logger.info(f"Pushing to Notion database ID: {database_id}")

    draft_file_path = args.draft
    if not draft_file_path:
        draft_files = glob.glob("drafts/*.md")
        if not draft_files:
            logger.error("No draft files found in drafts/ directory. Specify with --draft <filepath>.")
            return
        draft_files.sort(key=os.path.getmtime, reverse=True)
        draft_file_path = draft_files[0]
        logger.info(f"No --draft specified, using most recent: {draft_file_path}")

    if not os.path.exists(draft_file_path):
        logger.error(f"Draft file not found: {draft_file_path}")
        return

    with open(draft_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    final_title = title
    if not final_title:
        title_match = re.search(r'^#\\s+(.+)$', content, re.MULTILINE)
        if title_match:
            final_title = title_match.group(1).strip()
        else:
            final_title = os.path.basename(draft_file_path).replace('.md', '').replace('_', ' ').title()
    
    logger.info(f"Creating Notion page with title: '{final_title}'")
    
    try:
        notion_client = ensure_default_client()
        response = await notion_client.pages.create(
            parent={"database_id": database_id},
            properties={
                "Name": {"title": [{"text": {"content": final_title}}]},
                "Status": {"select": {"name": "Draft"}},
                "Publish Date": {"date": {"start": now_utc().strftime("%Y-%m-%d")}}
            },
            children=[
                {"object": "block", "type": "paragraph", "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "Content will be added by the editor. Full content from local draft."}}]
                }}
            ]
        )
        page_id = response["id"]
        page_url = f"https://notion.so/{page_id.replace('-', '')}"
        logger.info(f"SUCCESS: Created Notion page with ID: {page_id}")
        logger.info(f"Page URL: {page_url}")
        logger.info("\nCMS push completed successfully!")
        logger.info("Note: You'll need to manually copy the content from the draft into the Notion page.")
    except Exception as e:
        logger.error(f"Error pushing to Notion: {str(e)}")
        if hasattr(e, 'body'): logger.error(f"Details: {e.body}")

async def handle_cms_sync(args):
    """Handles 'maia cms sync' command."""
    from promaia.webflow.sync import sync_to_webflow

    logger.info(f"Initiating sync for CMS with Webflow.")
    
    try:
        notion_db_id = get_notion_database_id("cms")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error loading Notion database ID for CMS: {e}")
        return

    webflow_collection_id = args.collection or os.getenv("WEBFLOW_COLLECTION_ID")
    blog_status_prop = args.blog_status_property
    force = args.force_update

    if not webflow_collection_id:
        logger.error("Webflow Collection ID not specified (use --collection or WEBFLOW_COLLECTION_ID env var).")
        return

    logger.info(f"Syncing Notion DB (CMS: {notion_db_id}) to Webflow Collection: {webflow_collection_id}")
    logger.info(f"Using Notion status property: '{blog_status_prop}'")
    if force: logger.info("--force-update flag is active.")

    try:
        await sync_to_webflow(
            notion_database_id=notion_db_id,
            webflow_collection_id=webflow_collection_id,
            blog_status_property_name=blog_status_prop,
            force_update=force
        )
    except Exception as e:
        logger.error(f"Error during Notion-Webflow sync: {str(e)}")
        traceback.print_exc()

async def pull_cms_filtered(database_config, output_dir, property_filters, days, force, description):
    """Helper function to pull filtered CMS content to a specific directory."""
    import os
    import json
    from datetime import datetime
    from promaia.connectors import ConnectorRegistry
    # Create output directory
    full_output_dir = os.path.join(os.getcwd(), output_dir)
    os.makedirs(full_output_dir, exist_ok=True)
    
    logger.info(f"Saving {description} to: {full_output_dir}")
    
    # Configure connector with appropriate credentials
    connector_config = database_config.to_dict()
    
    if database_config.source_type == 'discord':
        # Load Discord bot token from credentials file
        from promaia.utils.env_writer import get_data_dir
        config_dir = str(get_data_dir() / "credentials" / database_config.workspace)
        credentials_file = os.path.join(config_dir, "discord_credentials.json")
        
        if not os.path.exists(credentials_file):
            logger.error(f"Discord credentials not found for workspace '{database_config.workspace}'")
            logger.error(f"Please run: maia workspace discord-setup {database_config.workspace}")
            return
        
        try:
            with open(credentials_file, 'r') as f:
                creds_data = json.load(f)
            connector_config['bot_token'] = creds_data.get("bot_token")
        except Exception as e:
            logger.error(f"Failed to load Discord credentials: {e}")
            return
    elif database_config.source_type == 'slack':
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if bot_token:
            connector_config['bot_token'] = bot_token
    else:
        from promaia.auth import get_integration
        api_key = get_integration("notion").get_notion_credentials(database_config.workspace)
        if not api_key:
            logger.error(f"No credentials configured for workspace '{database_config.workspace}'")
            return
        connector_config['api_key'] = api_key

    connector = ConnectorRegistry.get_connector(database_config.source_type, connector_config)
    if not connector:
        logger.error(f"Could not create connector for {database_config.source_type}")
        return
    
    # Parse days argument
    if days is not None:
        if isinstance(days, str) and days.lower() == 'all':
            days = None  # All entries
        else:
            try:
                days = int(days)
            except ValueError:
                days = database_config.default_days
    else:
        days = database_config.default_days
    
    logger.info(f"Filtering CMS pages with {property_filters}, days: {days or 'all'}")
    
    try:
        # Build filters
        from promaia.connectors.base import QueryFilter, DateRangeFilter
        from datetime import datetime
        from promaia.utils.timezone_utils import days_ago_utc, now_utc
        
        filters = []
        for prop_name, prop_value in property_filters.items():
            filters.append(QueryFilter(
                property_name=prop_name,
                operator="eq",
                value=prop_value
            ))
        
        # Create date filter if days is specified
        date_filter = None
        if days:
            start_date = days_ago_utc(days)
            date_filter = DateRangeFilter(
                property_name="last_edited_time",
                start_date=start_date,
                end_date=None
            )
        
        pages = await connector.query_pages(filters=filters, date_filter=date_filter)
        logger.info(f"Found {len(pages)} pages matching filters")
        
        # Save pages
        saved_count = 0
        for page in pages:
            try:
                page_id = page.get("id", "unknown")
                
                # Extract title
                title = "Untitled"
                properties = page.get("properties", {})
                for prop_name, prop_data in properties.items():
                    if prop_data.get("type") == "title" and prop_data.get("title"):
                        title = prop_data["title"][0].get("plain_text", "Untitled")
                        break
                
                # Get page content
                try:
                    from promaia.notion.pages import get_block_content
                    from promaia.markdown.converter import page_to_markdown
                    content_blocks = await get_block_content(page_id)
                    markdown_content = page_to_markdown(content_blocks)
                    page["content"] = markdown_content
                except Exception as content_error:
                    logger.warning(f"Could not fetch content for page {page_id}: {content_error}")
                    page["content"] = ""
                
                # Clean title for filename
                clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                clean_title = clean_title.replace(' ', '_')
                if not clean_title:
                    clean_title = f"untitled_{page_id[:8]}"
                
                # Save as JSON
                json_filename = f"{clean_title}_{page_id[:8]}.json"
                json_path = os.path.join(full_output_dir, json_filename)
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(page, f, indent=2, ensure_ascii=False)
                
                # Save as markdown if content exists
                if page.get("content"):
                    md_filename = f"{clean_title}_{page_id[:8]}.md"
                    md_path = os.path.join(full_output_dir, md_filename)
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(f"# {title}\n\n")
                        f.write(page["content"])
                
                saved_count += 1
                logger.info(f"Saved: {clean_title}")
                
            except Exception as e:
                logger.error(f"Error saving page {page_id}: {e}")
        
        logger.info(f"Successfully saved {saved_count} {description} pages")
        
        # Create metadata file
        metadata = {
            "created_at": now_utc().isoformat(),
            "filter_applied": property_filters,
            "days_filter": days,
            "total_pages": len(pages),
            "saved_pages": saved_count,
            "description": description
        }
        
        metadata_path = os.path.join(full_output_dir, "_metadata.json")
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Error querying CMS pages: {e}")
        import traceback
        traceback.print_exc()

# ==================== CORE COMMANDS ====================

def extract_database_names_from_sources(sources: List[str]) -> List[str]:
    """Extract database nicknames from source selections (e.g., 'koii.journal' -> 'journal')."""
    database_names = []
    for source in sources:
        if '.' in source:
            # Extract database name from workspace.database format
            _, db_name = source.split('.', 1)
            database_names.append(db_name)
        else:
            # Source is already just the database name
            database_names.append(source)
    return list(set(database_names))  # Remove duplicates

def chat_run(args):
    """Run the chat interface."""
    from promaia.chat.interface import chat
    from promaia.config.workspaces import get_workspace_manager
    
    # Handle recents option
    if getattr(args, 'recent', False):
        return chat_run_recents(args)
    
    # Handle -ws shortcut by converting to -b browse mode
    workspace_arg = getattr(args, 'workspace', None)
    if workspace_arg and not getattr(args, 'sources', None) and not getattr(args, 'browse', None):
        # Convert -ws workspace to -b workspace
        args.browse = [workspace_arg]
        args.workspace = None  # Clear original workspace arg
    
    # ========================================================================
    # ⚠️  CRITICAL BROWSE ARGUMENT PARSING - KEEP IN SYNC WITH EDIT MODE ⚠️
    # ========================================================================
    # This section parses -b (browse) arguments for the TOP-LEVEL browser.
    # The EDIT MODE browser (in chat/interface.py) must parse stored browse
    # commands using THE SAME LOGIC to maintain consistency.
    #
    # When modifying this parsing:
    # 1. Update handle_manual_browse_edit() in promaia/chat/interface.py
    # 2. Update handle_browse_in_edit_context() in promaia/chat/interface.py
    # 3. Test both: `maia chat -b X` AND /e editing with browse commands
    #
    # See: promaia/chat/interface.py - handle_manual_browse_edit()
    # See: promaia/chat/interface.py - handle_browse_in_edit_context()
    # ========================================================================
    
    # Handle browse option for workspace or Discord channel selection
    raw_browse_args = getattr(args, 'browse', None)
    sources = getattr(args, 'sources', None)

    # Initialize selected_sources to avoid scoping issues
    selected_sources = None
    
    # Flatten nested lists from multiple -b flags: [['trass'], ['trass.tg']] -> ['trass', 'trass.tg']
    # Also handles single -b with multiple args: [['trass', 'trass.tg']] -> ['trass', 'trass.tg']
    browse_args = None
    if raw_browse_args is not None:
        browse_args = []
        for item in raw_browse_args:
            if isinstance(item, list):
                browse_args.extend(item)
            else:
                browse_args.append(item)
    
    # Detect mixed commands: when user provides sources + browse, OR browse + SQL query, OR browse + vector search
    # ANY command with browse args should be treated as a mixed command to ensure browser launches first
    has_mixed_command = bool(browse_args) and (bool(sources) or (hasattr(args, 'sql_query') and args.sql_query) or (hasattr(args, 'vector_search') and args.vector_search))
    
    if browse_args is not None:
        # If this is a mixed command, handle it specially
        if has_mixed_command:
            if sources and browse_args:
                print_text("🔄 Detected mixed command with sources and browse. Browser will launch first...", style="cyan")
            elif hasattr(args, 'vector_search') and args.vector_search:
                print_text("🔄 Detected mixed command with browse and vector search. Browser will launch first...", style="cyan")
            else:
                print_text("🔄 Detected mixed command with browse and SQL query. Browser will launch first...", style="cyan")
            
            # browse_args is already flattened earlier
            browse_databases = browse_args or []

            # Get other arguments
            filters = getattr(args, 'filters', None)
            original_workspace = getattr(args, 'workspace', None)
            mcp_servers = getattr(args, 'mcp_servers', None)
            sql_prompt = None
            
            # Handle SQL query processing
            sql_prompts = []
            if hasattr(args, 'sql_query') and args.sql_query:
                # Handle both formats: list of strings (pre-processed) or list of lists (from argparse)
                if args.sql_query:
                    if isinstance(args.sql_query[0], list):
                        # From argparse: list of lists
                        sql_prompts = [' '.join(sql_args) for sql_args in args.sql_query if sql_args]
                    else:
                        # Pre-processed: list of strings
                        sql_prompts = args.sql_query
                else:
                    sql_prompts = []

                if len(sql_prompts) > 1:
                    print_text(f"🤖 Will process {len(sql_prompts)} SQL queries after browser", style="white")
                    for i, prompt in enumerate(sql_prompts):
                        print_text(f"   {i+1}. '{prompt}'", style="dim")
                elif sql_prompts:
                    print_text(f"🤖 Will process SQL query after browser: '{sql_prompts[0]}'", style="white")

            # Handle vector search processing
            vs_prompts = []
            if hasattr(args, 'vector_search') and args.vector_search:
                # Handle both formats: list of strings (pre-processed) or list of lists (from argparse)
                if args.vector_search:
                    if isinstance(args.vector_search[0], list):
                        # From argparse: list of lists
                        vs_prompts = [' '.join(vs_args) for vs_args in args.vector_search if vs_args]
                    else:
                        # Pre-processed: list of strings
                        vs_prompts = args.vector_search
                else:
                    vs_prompts = []

                # Vector search prompts will be shown later when processing with selected sources
            
            # Build original browse command to preserve user command for display
            original_command_parts = ["maia", "chat"]
            # For mixed commands, include sources in the command reconstruction
            if sources:
                for source in sources:
                    original_command_parts.extend(["-s", source])
            if browse_args:
                original_command_parts.append("-b")
                original_command_parts.extend(browse_args)
            if sql_prompts:
                # For original command reconstruction, combine all SQL prompts
                # Don't add quotes - the -sql argument parser handles multiple words with nargs="*"
                combined_sql = " ".join([f'-sql {prompt}' for prompt in sql_prompts])
                original_command_parts.append(combined_sql)
            if vs_prompts:
                # For original command reconstruction, combine all VS prompts
                # Don't add quotes - the -vs argument parser handles multiple words with nargs="*"
                combined_vs = " ".join([f'-vs {prompt}' for prompt in vs_prompts])
                original_command_parts.append(combined_vs)
            if mcp_servers:
                for server in mcp_servers:
                    original_command_parts.extend(["-mcp", server])
            original_browse_command = " ".join(original_command_parts)

            # For mixed commands with browse: -s + -b, -b + -sql, or -b + -vs combinations
            if browse_databases and (sources or sql_prompts or vs_prompts):
                print_text("🔄 Processing mixed command with browse. Launching browser first...", style="cyan")

                # Use the same browser launch logic as regular browse commands
                try:
                    # Determine workspace and setup browser parameters (copied from browse logic below)
                    from promaia.config.workspaces import get_workspace_manager
                    from promaia.config.databases import get_database_manager
                    workspace_manager = get_workspace_manager()
                    db_manager = get_database_manager()

                    # Detect multiple workspaces and handle accordingly
                    workspace_names_found = []

                    # First pass: collect all workspace names from browse arguments
                    for browse_spec in browse_databases:
                        # Remove day specification if present
                        base_name = browse_spec.split(':')[0] if ':' in browse_spec else browse_spec

                        # Check if this is a workspace name directly
                        if workspace_manager.validate_workspace(base_name):
                            if base_name not in workspace_names_found:
                                workspace_names_found.append(base_name)
                        # Check if this is a database name (workspace.database format)
                        elif '.' in base_name:
                            potential_workspace = base_name.split('.')[0]
                            if workspace_manager.validate_workspace(potential_workspace):
                                if potential_workspace not in workspace_names_found:
                                    workspace_names_found.append(potential_workspace)

                    # Determine workspace parameter for browser
                    if len(workspace_names_found) > 1:
                        # Multiple workspaces - use None and let browser handle via database_filter
                        original_workspace = None
                        use_workspace_expansion = False  # Don't expand to individual databases
                        print_text(f"INFO: Detected multiple workspaces: {', '.join(workspace_names_found)}", style="cyan")
                    elif len(workspace_names_found) == 1:
                        # Single workspace
                        original_workspace = workspace_names_found[0]
                        use_workspace_expansion = True  # Expand to individual databases
                    else:
                        original_workspace = None
                        use_workspace_expansion = False

                    # Build database filter for browser
                    database_filter = []
                    default_days = None

                    for browse_spec in browse_databases:
                        if ':' in browse_spec:
                            db_name, days_str = browse_spec.rsplit(':', 1)
                            try:
                                days = int(days_str)
                                if default_days is None:
                                    default_days = days
                                database_filter.append(db_name)
                            except ValueError:
                                database_filter.append(browse_spec)
                        else:
                            if workspace_manager.validate_workspace(browse_spec):
                                if use_workspace_expansion:
                                    # Single workspace - expand to all its databases
                                    workspace_databases = db_manager.get_workspace_databases(browse_spec)
                                    for db in workspace_databases:
                                        if db.browser_include:
                                            database_filter.append(db.get_qualified_name())
                                else:
                                    # Multiple workspaces - keep workspace name for browser to handle
                                    database_filter.append(browse_spec)
                            else:
                                database_filter.append(browse_spec)

                    # For mixed commands, launch browser UI with pre-selected sources
                    print_text(f"🔍 Launching browser UI for workspaces: {', '.join(workspace_names_found)}...", style="cyan")

                    # Build pre-selected sources prioritizing user's explicit sources
                    preselected_sources = []
                    
                    # Get default sources from chat config
                    from promaia.utils.config import get_chat_default_sources, get_chat_default_days
                    default_chat_sources = get_chat_default_sources()
                    default_chat_days = get_chat_default_days()

                    # First, create a map of user's explicit sources for overriding
                    user_source_map = {}
                    if sources:  # Only iterate if sources is not None
                        for source_spec in sources:
                            if ':' in source_spec:
                                db_name, days_part = source_spec.rsplit(':', 1)
                                user_source_map[db_name] = source_spec
                            else:
                                user_source_map[source_spec] = source_spec

                    # Add user's explicit sources first (only if they exist)
                    if sources:
                        preselected_sources.extend(sources)

                    # Then add databases with default_include that aren't already specified
                    for workspace_name in workspace_names_found:
                        # Get all databases for this workspace
                        workspace_databases = db_manager.get_workspace_databases(workspace_name)
                        for db in workspace_databases:
                            if db.browser_include:
                                qualified_name = db.get_qualified_name()

                                # Only add if not already specified by user and has default_include=true
                                if qualified_name not in user_source_map and db.default_include:
                                    # Use appropriate default days
                                    if default_days and isinstance(default_days, int) and default_days > 0:
                                        days_to_use = default_days
                                    elif db.default_days and isinstance(db.default_days, int) and db.default_days > 0:
                                        days_to_use = db.default_days
                                    else:
                                        days_to_use = default_chat_days
                                    preselected_sources.append(f"{qualified_name}:{days_to_use}")

                    print_text(f"🎯 Pre-selecting {len(preselected_sources)} sources (user + config defaults)", style="green")
                    
                    # Launch the browser UI with pre-selected sources
                    from promaia.cli.workspace_browser import launch_unified_browser
                    browser_result = launch_unified_browser(
                        original_workspace,
                        default_days,
                        database_filter,
                        preselected_sources  # Pass pre-selected sources for UI
                    )
                    selected_sources = browser_result.sources if not browser_result.cancelled else None

                    if browser_result.cancelled:
                        print_text("ℹ️  Browser cancelled. Mixed command cancelled.", style="yellow")
                        return

                    if not selected_sources:
                        print_text("ℹ️  No sources selected. Only prompt, no context.", style="yellow")
                        # Continue with empty sources — don't return
                    else:
                        print_text(f"✅ Selected {len(selected_sources)} sources from browser", style="green")

                    # DEBUG: Check if we reach this point
                    print(f"DEBUG: After browser selection, about to process sources and call chat function")
                    print(f"DEBUG: selected_sources = {selected_sources}")

                    # Process Discord channel sources and convert to database + filter format
                    processed_sources = []
                    processed_filters = []
                    discord_db_groups = {}

                    for source in selected_sources:
                        if '#' in source:
                            # Discord channel: trass.tg#customer-support:7
                            db_channel, days_part = source.rsplit(':', 1)
                            db_name, channel_name = db_channel.split('#', 1)

                            # Group by database + days combination
                            db_key = f"{db_name}:{days_part}"
                            if db_key not in discord_db_groups:
                                discord_db_groups[db_key] = []
                            discord_db_groups[db_key].append(channel_name)
                        else:
                            # Regular database source
                            processed_sources.append(source)

                    # Convert Discord groups to source + filter combinations
                    for db_spec, channels in discord_db_groups.items():
                        processed_sources.append(db_spec)

                        # Create filter for channels
                        if len(channels) == 1:
                            # Single channel
                            filter_spec = f"{db_spec}:discord_channel_name={channels[0]}"
                            processed_filters.append(filter_spec)
                        else:
                            # Multiple channels - use OR logic
                            channel_conditions = [f"discord_channel_name={ch}" for ch in channels]
                            combined_filter = " or ".join(channel_conditions)
                            filter_spec = f"{db_spec}:({combined_filter})"
                            processed_filters.append(filter_spec)

                    # Browser already includes user preferences, so just use the processed sources
                    all_sources = processed_sources
                    all_filters = processed_filters

                    print_text(f"🔄 Using sources from browser: {len(all_sources)} total", style="green")

                    # Process queries sequentially, then merge results
                    sql_query_content= None

                    # Step 1: Process SQL queries (if present)
                    if sql_prompts:
                        try:
                            from promaia.storage.unified_query import get_query_interface
                            query_interface = get_query_interface()

                            combined_sql_content = {}
                            for i, sql_prompt in enumerate(sql_prompts):
                                if len(sql_prompts) > 1:
                                    print_text(f"🔍 Processing SQL query {i+1}/{len(sql_prompts)}: '{sql_prompt}'", style="cyan")

                                # Process with verbose output (includes user interaction)
                                sql_content = query_interface.natural_language_query(sql_prompt, None, None, verbose=True)

                                if sql_content:
                                    # Merge results
                                    for db_name, entries in sql_content.items():
                                        if db_name not in combined_sql_content:
                                            combined_sql_content[db_name] = []
                                        combined_sql_content[db_name].extend(entries)

                            sql_query_content= combined_sql_content if combined_sql_content else None

                        except Exception as e:
                            print_text(f"❌ Error processing SQL query: {e}", style="red")
                            # Continue with VS query if present

                    # Step 2: Process vector search queries (if present)
                    if vs_prompts:
                        try:
                            from promaia.ai.nl_processor_wrapper import process_vector_search_to_content

                            combined_vs_content = {}
                            for i, vs_prompt in enumerate(vs_prompts):
                                if len(vs_prompts) > 1:
                                    print_text(f"🔍 Processing VS query {i+1}/{len(vs_prompts)}: '{vs_prompt}'", style="cyan")

                                # Process with verbose output (includes user interaction)
                                vs_content = process_vector_search_to_content(
                                    vs_prompt,
                                    workspace=None,
                                    verbose=True,
                                    n_results=getattr(args, 'top_k', 20),
                                    min_similarity=getattr(args, 'threshold', 0.75)
                                )

                                if vs_content:
                                    # Merge results
                                    for db_name, entries in vs_content.items():
                                        if db_name not in combined_vs_content:
                                            combined_vs_content[db_name] = []
                                        combined_vs_content[db_name].extend(entries)

                        except Exception as e:
                            print_text(f"❌ Error processing vector search query: {e}", style="red")

                    # Step 3: Launch chat with separate SQL and VS content (don't merge here)
                    from promaia.chat.interface import chat

                    # Prepare cache parameters for separate SQL and VS caching
                    combined_sql_prompt = " ".join(sql_prompts) if sql_prompts else None
                    combined_vs_prompt = " ".join(vs_prompts) if vs_prompts else None

                    chat(
                        sources=all_sources,
                        filters=all_filters,
                        workspace=original_workspace,
                        mcp_servers=mcp_servers,
                        original_browse_command=original_browse_command,
                        browse_selections=selected_sources,
                        sql_query_content=None,  # Will be set from initial_nl_content in chat()
                        sql_query_prompt=None,  # Already processed
                        is_vector_search=False,  # Already processed
                        # Pass separate SQL and VS content for independent tracking
                        initial_nl_prompt=combined_sql_prompt if sql_prompts else None,
                        initial_nl_content=combined_sql_content if sql_prompts and 'combined_sql_content' in locals() else None,
                        initial_vs_prompt=combined_vs_prompt if vs_prompts else None,
                        initial_vs_content=combined_vs_content if vs_prompts and 'combined_vs_content' in locals() else None,
                        top_k=getattr(args, 'top_k', None),
                        threshold=getattr(args, 'threshold', None),
                    )
                    return

                except Exception as e:
                    print_text(f"❌ Error in mixed command execution: {e}", style="red")
                    return

            # For -b + -sql combinations (no explicit sources), launch browser first
            elif not sources and browse_databases and sql_prompts:
                try:
                    # Determine workspace and setup browser parameters (copied from browse logic below)
                    from promaia.config.workspaces import get_workspace_manager
                    from promaia.config.databases import get_database_manager
                    workspace_manager = get_workspace_manager()
                    db_manager = get_database_manager()

                    # Detect multiple workspaces and handle accordingly
                    workspace_names_found = []

                    # First pass: collect all workspace names from browse arguments
                    for browse_spec in browse_databases:
                        # Remove day specification if present
                        base_name = browse_spec.split(':')[0] if ':' in browse_spec else browse_spec

                        # Check if this is a workspace name directly
                        if workspace_manager.validate_workspace(base_name):
                            if base_name not in workspace_names_found:
                                workspace_names_found.append(base_name)
                        # Check if this is a database name (workspace.database format)
                        elif '.' in base_name:
                            potential_workspace = base_name.split('.')[0]
                            if workspace_manager.validate_workspace(potential_workspace):
                                if potential_workspace not in workspace_names_found:
                                    workspace_names_found.append(potential_workspace)

                    # Determine workspace parameter for browser
                    if len(workspace_names_found) > 1:
                        # Multiple workspaces - use None and let browser handle via database_filter
                        original_workspace = None
                        use_workspace_expansion = False  # Don't expand to individual databases
                        print_text(f"INFO: Detected multiple workspaces: {', '.join(workspace_names_found)}", style="cyan")
                    elif len(workspace_names_found) == 1:
                        # Single workspace
                        original_workspace = workspace_names_found[0]
                        use_workspace_expansion = True  # Expand to individual databases
                    else:
                        # No workspaces found in browse args, fall back to original logic
                        if not original_workspace:
                            original_workspace = workspace_manager.get_default_workspace()
                        use_workspace_expansion = True
                    
                    # Parse browse databases
                    database_filter = []
                    default_days = None
                    
                    for browse_spec in browse_databases:
                        if ':' in browse_spec:
                            db_name, days_str = browse_spec.rsplit(':', 1)
                            try:
                                days = int(days_str)
                                if default_days is None:
                                    default_days = days
                                database_filter.append(db_name)
                            except ValueError:
                                database_filter.append(browse_spec)
                        else:
                            if workspace_manager.validate_workspace(browse_spec):
                                if use_workspace_expansion:
                                    # Single workspace - expand to all its databases
                                    workspace_databases = db_manager.get_workspace_databases(browse_spec)
                                    for db in workspace_databases:
                                        if db.browser_include:
                                            database_filter.append(db.get_qualified_name())
                                else:
                                    # Multiple workspaces - keep workspace name for browser to handle
                                    database_filter.append(browse_spec)
                            else:
                                database_filter.append(browse_spec)
                    
                    # For mixed commands, launch browser with pre-selected sources based on config and workspace
                    print_text(f"🔍 Launching browser for workspaces: {', '.join(workspace_names_found)}...", style="cyan")
                    
                    # Build pre-selected sources from config defaults and workspace context
                    preselected_sources = []
                    
                    # Get default sources from chat config
                    from promaia.utils.config import get_chat_default_sources, get_chat_default_days
                    default_chat_sources = get_chat_default_sources()
                    default_chat_days = get_chat_default_days()
                    
                    # For each workspace, add sources based on their default_include setting
                    for workspace_name in workspace_names_found:
                        workspace_databases = db_manager.get_workspace_databases(workspace_name)
                        for db in workspace_databases:
                            if db.browser_include:
                                qualified_name = db.get_qualified_name()
                                
                                # Check if this database should be pre-selected based on default_include
                                if db.default_include:
                                    # Use specified days or fall back to database/chat defaults
                                    days_to_use = default_days or db.default_days or default_chat_days
                                    if days_to_use:
                                        preselected_sources.append(f"{qualified_name}:{days_to_use}")
                                    else:
                                        preselected_sources.append(qualified_name)
                    
                    print_text(f"🎯 Pre-selecting {len(preselected_sources)} default sources based on config", style="green")
                    
                    # Launch unified browser with pre-selected sources
                    from promaia.cli.workspace_browser import launch_unified_browser
                    browser_result = launch_unified_browser(
                        original_workspace,
                        default_days,
                        database_filter,
                        preselected_sources  # Pass pre-selected sources for UI
                    )
                    selected_sources = browser_result.sources if not browser_result.cancelled else None

                    if browser_result.cancelled:
                        # User cancelled - use preselected sources as fallback
                        selected_sources = preselected_sources or []

                    # Process Discord channel sources and convert to database + filter format
                    processed_sources = []
                    processed_filters = []
                    discord_db_groups = {}

                    for source in selected_sources:
                        if '#' in source:
                            # Discord channel: trass.tg#customer-support:7
                            db_channel, days_part = source.rsplit(':', 1)
                            db_name, channel_name = db_channel.split('#', 1)
                            
                            # Group by database + days combination
                            db_key = f"{db_name}:{days_part}"
                            if db_key not in discord_db_groups:
                                discord_db_groups[db_key] = []
                            discord_db_groups[db_key].append(channel_name)
                        else:
                            # Regular database source
                            processed_sources.append(source)
                    
                    # Convert Discord groups to source + filter combinations
                    for db_key, channels in discord_db_groups.items():
                        processed_sources.append(db_key)
                        # Create a single filter for all channels in this database with source prefix
                        channel_filter = " OR ".join(f'channel:"{channel}"' for channel in channels)
                        processed_filters.append(f"{db_key}:({channel_filter})")
                    
                    # Now call chat with the selected sources and natural language prompt
                    sources = processed_sources
                    if processed_filters:
                        filters = (filters or []) + processed_filters

                except Exception as e:
                    print_text(f"❌ Error in browser launch for mixed command: {e}", style="red")
                    return
            

            # For mixed commands, process queries sequentially then merge results
            try:
                sql_query_content= None

                # Step 1: Process natural language queries (if present)
                if sql_prompts:
                    try:
                        from promaia.storage.unified_query import get_query_interface
                        query_interface = get_query_interface()

                        combined_sql_content = {}
                        for i, sql_prompt in enumerate(sql_prompts):
                            if len(sql_prompts) > 1:
                                print_text(f"🔍 Processing NL query {i+1}/{len(sql_prompts)}: '{sql_prompt}'", style="cyan")

                            # Process with verbose output (includes user interaction)
                            sql_content = query_interface.natural_language_query(sql_prompt, None, None, verbose=True)

                            if sql_content:
                                # Merge results
                                for db_name, entries in sql_content.items():
                                    if db_name not in combined_sql_content:
                                        combined_sql_content[db_name] = []
                                    combined_sql_content[db_name].extend(entries)

                        sql_query_content= combined_sql_content if combined_sql_content else None

                    except Exception as e:
                        print_text(f"❌ Error processing natural language query: {e}", style="red")
                        # Continue with VS query if present

                # Step 2: Process vector search queries (if present)
                if vs_prompts:
                    try:
                        from promaia.ai.nl_processor_wrapper import process_vector_search_to_content

                        combined_vs_content = {}
                        for i, vs_prompt in enumerate(vs_prompts):
                            if len(vs_prompts) > 1:
                                print_text(f"🔍 Processing VS query {i+1}/{len(vs_prompts)}: '{vs_prompt}'", style="cyan")

                            # Process with verbose output (includes user interaction)
                            vs_content = process_vector_search_to_content(
                                vs_prompt,
                                workspace=None,
                                verbose=True,
                                n_results=getattr(args, 'top_k', 20),
                                min_similarity=getattr(args, 'threshold', 0.75)
                            )

                            if vs_content:
                                # Merge results
                                for db_name, entries in vs_content.items():
                                    if db_name not in combined_vs_content:
                                        combined_vs_content[db_name] = []
                                    combined_vs_content[db_name].extend(entries)

                    except Exception as e:
                        print_text(f"❌ Error processing vector search query: {e}", style="red")

                # Step 3: Pass separate NL and VS content to chat (don't merge here)
                # Prepare cache parameters for separate NL and VS caching
                combined_sql_prompt = " ".join(sql_prompts) if sql_prompts else None
                combined_vs_prompt = " ".join(vs_prompts) if vs_prompts else None

                chat(
                    sources=sources,
                    filters=filters,
                    workspace=original_workspace,
                    non_interactive=getattr(args, 'non_interactive', False),
                    sql_query_content=None,  # Will be set from initial_nl_content in chat()
                    sql_query_prompt=None,  # Already processed
                    browse_databases=None,
                    original_browse_command=original_browse_command,
                    browse_selections=selected_sources,
                    mcp_servers=mcp_servers,
                    is_vector_search=False,  # Already processed
                    # Pass separate NL and VS content for independent tracking
                    initial_nl_prompt=combined_sql_prompt if sql_prompts else None,
                    initial_nl_content=combined_sql_content if sql_prompts and 'combined_sql_content' in locals() else None,
                    initial_vs_prompt=combined_vs_prompt if vs_prompts else None,
                    initial_vs_content=combined_vs_content if vs_prompts and 'combined_vs_content' in locals() else None,
                    top_k=getattr(args, 'top_k', None),
                    threshold=getattr(args, 'threshold', None),
                )
                return
            except Exception as e:
                print_text(f"❌ Error in mixed command execution: {e}", style="red")
                return


        # Handle non-mixed browse commands (existing logic)
        # If browse is provided without other sources, determine type of browse
        elif not getattr(args, 'sources', None) and not browse_args:
            # Bare -b with no arguments: use default workspace for full browser
            from promaia.config.workspaces import get_workspace_manager
            workspace_manager = get_workspace_manager()
            default_ws = getattr(args, 'workspace', None) or workspace_manager.get_default_workspace()
            if default_ws and workspace_manager.validate_workspace(default_ws):
                return chat_run_workspace_browse(args, default_ws)
            else:
                return chat_run_browse(args)  # Fallback to Discord browse
        # Check if this is a workspace browse or Discord browse
        elif browse_args is not None and len(browse_args) == 1:
            browse_target = browse_args[0]
            # Check if it's a workspace name
            from promaia.config.workspaces import get_workspace_manager
            workspace_manager = get_workspace_manager()
            if workspace_manager.validate_workspace(browse_target):
                return chat_run_workspace_browse(args, browse_target)
            else:
                # Treat as Discord database browse
                return chat_run_inline_browse(args)
        # Check if this is a multi-workspace browse
        elif browse_args is not None and len(browse_args) > 1:
            from promaia.config.workspaces import get_workspace_manager
            workspace_manager = get_workspace_manager()
            
            # Check if all arguments are workspace names
            all_workspaces = all(workspace_manager.validate_workspace(arg) for arg in browse_args)
            
            if all_workspaces:
                # Multi-workspace browse - pass all workspaces
                return chat_run_multi_workspace_browse(args, browse_args)
            else:
                # Mixed or Discord database browse
                return chat_run_inline_browse(args)
        # Otherwise, handle inline browse functionality (Discord)
        elif browse_args is not None:  # browse_args could be empty list or list with databases
            return chat_run_inline_browse(args)
    
    # Handle regular commands (no browse)
    sources = getattr(args, 'sources', None)
    filters = getattr(args, 'filters', None)
    original_workspace = getattr(args, 'workspace', None)
    mcp_servers = getattr(args, 'mcp_servers', None)
    sql_prompt = None
    
    # Handle SQL query processing
    # NOTE: This -sql parsing MUST stay in sync with:
    # 1. Edit mode parsing in promaia/chat/interface.py (lines ~2148-2163)
    # 2. safe_split_command() function in interface.py (line ~514)
    # These are two sides of one feature and must handle multiple -sql arguments identically.
    sql_prompts = []
    if hasattr(args, 'sql_query') and args.sql_query:
        # With action="append" and nargs="+", we get a list of lists
        # Each inner list contains the tokens for one -sql argument
        sql_prompts = [' '.join(sql_args) for sql_args in args.sql_query if sql_args]
        # Note: Don't print "Processing..." messages here - the processor handles output

        try:
            from promaia.storage.unified_query import get_query_interface

            # Resolve workspace first for natural language processing
            workspace_manager = get_workspace_manager()
            if original_workspace:
                if not workspace_manager.validate_workspace(original_workspace):
                    print_text(f"✗ Workspace '{original_workspace}' is not properly configured.", style="red")
                    return
                resolved_workspace = original_workspace
            else:
                resolved_workspace = workspace_manager.get_default_workspace()
                if not resolved_workspace:
                    print_text("No workspace specified and no default workspace configured.", style="red")
                    return

            # Process natural language using hybrid query interface
            query_interface = get_query_interface()

            # Process each NL query separately and combine results
            combined_sql_content = {}
            total_results = 0

            for i, sql_prompt in enumerate(sql_prompts):
                # Show query number only for multiple queries
                if len(sql_prompts) > 1:
                    print_text(f"🔍 Processing query {i+1}/{len(sql_prompts)}: '{sql_prompt}'", style="cyan")

                # Always allow cross-workspace queries for natural language
                # Workspace is just a classifier/tag, not a mandatory constraint
                # Enable verbose=True to show SQL generation steps and chain of thought
                sql_content = query_interface.natural_language_query(sql_prompt, None, None, verbose=True)

                if sql_content:
                    # Merge results from this query into combined content
                    for db_name, entries in sql_content.items():
                        if db_name not in combined_sql_content:
                            combined_sql_content[db_name] = []
                        combined_sql_content[db_name].extend(entries)

                    query_results = sum(len(entries) for entries in sql_content.values())
                    total_results += query_results
                    if len(sql_prompts) > 1:
                        print_text(f"   ✅ Query {i+1} found {query_results} results", style="green")
                else:
                    if len(sql_prompts) > 1:
                        print_text(f"   ⚠️  Query {i+1} found no results", style="yellow")

            if not combined_sql_content:
                return

            if len(sql_prompts) > 1:
                print_text(f"🎯 Combined {len(sql_prompts)} queries: {total_results} total results", style="green")
            sql_query_content= combined_sql_content

            # Keep both regular sources and natural language content
            # The chat interface will combine them
            
        except ImportError as e:
            print_text(f"Error importing natural language processor: {e}", style="red")
            return
        except Exception as e:
            print_text(f"Error processing natural language query: {e}", style="red")
            return
    else:
        sql_query_content= None
    
    # Process vector search queries (similar to natural language but uses semantic search)
    # Parse -vs queries with their per-query -tk/-th parameters from sys.argv
    vs_queries_structured = []
    if hasattr(args, 'vector_search') and args.vector_search:
        vs_queries_structured = parse_vs_queries_with_params(sys.argv)

        # Backward compatibility: extract simple query list
        vs_prompts = [q['query'] for q in vs_queries_structured]

        try:
            from promaia.ai.nl_processor_wrapper import process_vector_search_to_content
            
            # Resolve workspace first for vector search processing
            workspace_manager = get_workspace_manager()
            if original_workspace:
                if not workspace_manager.validate_workspace(original_workspace):
                    print_text(f"✗ Workspace '{original_workspace}' is not properly configured.", style="red")
                    return
                resolved_workspace = original_workspace
            else:
                resolved_workspace = workspace_manager.get_default_workspace()
                if not resolved_workspace:
                    print_text("No workspace specified and no default workspace configured.", style="red")
                    return
            
            # Process each vector search query separately and combine results
            combined_vs_content = {}
            total_results = 0
            vs_per_query_cache = {}  # Build cache for initial queries

            for i, vs_query_obj in enumerate(vs_queries_structured):
                vs_prompt = vs_query_obj['query']
                query_top_k = vs_query_obj['top_k']
                query_threshold = vs_query_obj['threshold']

                # Show query number only for multiple queries
                if len(vs_queries_structured) > 1:
                    print_text(f"🔍 Processing query {i+1}/{len(vs_queries_structured)}: '{vs_prompt}'", style="cyan")

                # Process vector search with per-query parameters
                vs_content = process_vector_search_to_content(
                    vs_prompt,
                    workspace=None,  # Allow cross-workspace searches
                    verbose=True,  # Show detailed processing steps (matching SQL mode)
                    n_results=query_top_k,
                    min_similarity=query_threshold
                )

                if vs_content:
                    # Cache this query's LOADED CONTENT with query+params as key
                    cache_key = f"{vs_prompt}|{query_top_k}|{query_threshold}"
                    vs_per_query_cache[cache_key] = vs_content

                    # Merge results from this query into combined content
                    for db_name, entries in vs_content.items():
                        if db_name not in combined_vs_content:
                            combined_vs_content[db_name] = []
                        combined_vs_content[db_name].extend(entries)

                    query_results = sum(len(entries) for entries in vs_content.values())
                    total_results += query_results
                    if len(vs_prompts) > 1:
                        print_text(f"   ✅ Query {i+1} found {query_results} results", style="green")
                else:
                    # Cache empty result with query+params as key
                    cache_key = f"{vs_prompt}|{query_top_k}|{query_threshold}"
                    vs_per_query_cache[cache_key] = {}
                    if len(vs_prompts) > 1:
                        print_text(f"   ⚠️  Query {i+1} found no results", style="yellow")
            
            if not combined_vs_content:
                print_text("❌ No content found for any vector search queries", style="red")
                return
            
            if len(vs_prompts) > 1:
                print_text(f"🎯 Combined {len(vs_prompts)} queries: {total_results} total results", style="green")
            
            # Store vector search content for passing to chat
            # IMPORTANT: DO NOT add vs_prompts to sql_prompts - they are separate query types!
            # The browser uses sql_prompts/combined_sql_prompt to create new queries, so we must keep VS separate
            if sql_query_content:
                # Merge VS results with existing NL results (content only, not prompts)
                for db_name, entries in combined_vs_content.items():
                    if db_name not in sql_query_content:
                        sql_query_content[db_name] = []
                    sql_query_content[db_name].extend(entries)
            else:
                # Just use VS content directly (content only, not prompts)
                sql_query_content= combined_vs_content
        
        except ImportError as e:
            print_text(f"Error importing vector search processor: {e}", style="red")
            return
        except Exception as e:
            print_text(f"Error processing vector search query: {e}", style="red")
            return
    
    # Non-interactive mode for the desktop app
    if not sys.stdout.isatty():
        # In non-interactive mode, the chat function is expected to 
        # initialize and then enter a loop to process messages from stdin.
        # This requires the `chat` function to be adapted for this behavior.
        # For now, we assume `chat` handles this.
        logging.info("Running in non-interactive mode.")

    try:
        # Resolve the actual workspace to use
        workspace_manager = get_workspace_manager()
        resolved_workspace = original_workspace
        
        # If no workspace is explicitly provided, try to determine from sources
        if not resolved_workspace and sources:
            for source in sources:
                if '.' in source:
                    # This is not an inference, but a direct determination from the qualified source name.
                    determined_workspace = source.split('.')[0]
                    if workspace_manager.validate_workspace(determined_workspace):
                        resolved_workspace = determined_workspace
                        print_text(f"INFO: Using workspace '{resolved_workspace}' from source '{source}'.", style="white")
                        break
        
        # If still no workspace, use the default
        if not resolved_workspace:
            resolved_workspace = workspace_manager.get_default_workspace()
            if not resolved_workspace:
                print_text("No workspace specified, none could be inferred, and no default workspace is configured.", style="red")
                return

        # Validate the final resolved workspace
        if not workspace_manager.validate_workspace(resolved_workspace):
            print_text(f"✗ Workspace '{resolved_workspace}' is not properly configured.", style="red")
            return
        
        # Save query to recents before executing (for both traditional and NL queries)
        # Skip if this is being called from browse mode (which handles its own recents saving)
        skip_recents = getattr(args, 'skip_recents_save', False)
        combined_sql_prompt = " ".join(sql_prompts) if sql_prompts else None
        if not skip_recents and (sources or filters or original_workspace or combined_sql_prompt):
            from promaia.storage.recents import RecentsManager
            recents_manager = RecentsManager()
            recents_manager.add_query(
                sources=sources,
                filters=filters,
                workspace=original_workspace,
                sql_query_prompt=combined_sql_prompt
            )
        
        # The `chat` function will now need to handle the main loop
        non_interactive = getattr(args, 'non_interactive', False) or not sys.stdout.isatty()
        
        # Check if this came from browse mode and extract browse information
        original_browse_command = getattr(args, 'original_browse_command', None)
        browse_selections = getattr(args, 'browse_selections', None)

        # Determine if this is vector search mode - check if we have vector search prompts
        is_vector_search_mode = bool(hasattr(args, 'vector_search') and args.vector_search)

        # Pass per-query cache if available (from vector search processing)
        vs_cache = vs_per_query_cache if is_vector_search_mode and 'vs_per_query_cache' in locals() else None

        chat(sources=sources, filters=filters, workspace=original_workspace, resolved_workspace=resolved_workspace, non_interactive=non_interactive, sql_query_content=sql_query_content, sql_query_prompt=combined_sql_prompt, original_browse_command=original_browse_command, browse_selections=browse_selections, mcp_servers=mcp_servers, is_vector_search=is_vector_search_mode, top_k=getattr(args, 'top_k', None), threshold=getattr(args, 'threshold', None), vector_search_queries=vs_queries_structured if is_vector_search_mode else None, initial_vs_per_query_cache=vs_cache)

    except ImportError as e:
        print_text(f"Error importing chat interface: {e}", style="red")
        print_text("Please check your dependencies.", style="red")
    except Exception as e:
        logging.error(f"An unexpected error occurred in chat_run: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")


def chat_run_recents(args):
    """Run the chat interface with recent queries — browser->query two-step flow.

    1. Opens browser pre-populated with the most recent query's sources.
    2. Ctrl+Left/Right cycles through recents (sources update + query preview).
    3. Enter accepts sources and drops to a query input prompt pre-filled with
       the recent's -sql/-vs queries.
    4. Enter from query input executes the full combination.
    5. Esc from query input returns to the browser (query preserved).
    6. Esc from browser cancels everything.
    """
    from prompt_toolkit import prompt as pt_prompt
    from promaia.storage.recents import RecentsManager
    from promaia.config.workspaces import get_workspace_manager
    from promaia.cli.workspace_browser import launch_unified_browser

    try:
        rm = RecentsManager()
        recents = rm.get_recents()

        if not recents:
            print_text("No recent queries found. Use 'maia chat' with some options first.", style="yellow")
            return

        # Determine workspace from the most recent query
        workspace_manager = get_workspace_manager()
        most_recent = recents[0]
        workspace = most_recent.workspace or workspace_manager.get_default_workspace()

        if not workspace:
            print_text("No workspace configured. Run 'maia workspace add' first.", style="red")
            return

        # Pre-populate with the most recent query's sources
        current_sources = most_recent.sources

        # Build initial query text from the most recent
        query_text = ""
        if most_recent.sql_query_prompt:
            query_text = f"-sql {most_recent.sql_query_prompt}"
        elif most_recent.original_browse_command:
            # Extract -sql/-vs parts from original command
            import re
            sql_match = re.search(r'-sql\s+"([^"]*)"|-sql\s+(\S+)', most_recent.original_browse_command)
            vs_match = re.search(r'-vs\s+"([^"]*)"|-vs\s+(\S+)', most_recent.original_browse_command)
            parts = []
            if sql_match:
                parts.append(f"-sql {sql_match.group(1) or sql_match.group(2)}")
            if vs_match:
                parts.append(f"-vs {vs_match.group(1) or vs_match.group(2)}")
            query_text = " ".join(parts)

        # --- State machine: Browser <-> Query Input ---
        while True:
            # Step 1: Launch browser in recents mode
            browser_result = launch_unified_browser(
                workspace=workspace,
                current_sources=current_sources,
                recents_mode=True,
            )

            if browser_result.cancelled:
                print_text("Recents cancelled.", style="yellow")
                return

            selected_sources = browser_result.sources

            # Grab query preview from recents cycling if available
            if hasattr(browser_result, '_recents_query_preview') and browser_result._recents_query_preview:
                query_text = browser_result._recents_query_preview

            if not selected_sources:
                print_text("No sources selected.", style="yellow")
                return

            # Step 2: Query input prompt
            print_text(f"\nSources: {len(selected_sources)} selected", style="dim green")
            try:
                edited_query = pt_prompt(
                    "Query: ",
                    default=query_text,
                    mouse_support=True,
                ).strip()
            except (KeyboardInterrupt, EOFError):
                # Esc / Ctrl+C from query input -> back to browser
                current_sources = selected_sources  # Preserve selections
                continue

            if not edited_query and not selected_sources:
                print_text("Nothing to execute.", style="yellow")
                return

            # Step 3: Execute the combination
            # Build args object for chat_run
            class RecentsArgs:
                def __init__(self):
                    self.sources = selected_sources
                    self.filters = None
                    self.workspace = workspace
                    self.recent = False
                    self.browse = None
                    self.natural_language = None
                    self.vector_search = None
                    self.sql_query = None
                    self.top_k = None
                    self.threshold = None
                    self.mcp = None

            query_args = RecentsArgs()

            # Parse the query text for -sql/-vs flags
            if edited_query:
                from promaia.chat.context_editor import parse_chat_args
                parsed = parse_chat_args(edited_query)
                if parsed.sql_queries:
                    query_args.sql_query = parsed.sql_queries
                if parsed.vector_searches:
                    query_args.vector_search = parsed.vector_searches
                if parsed.top_k:
                    query_args.top_k = parsed.top_k
                if parsed.threshold:
                    query_args.threshold = parsed.threshold

            display_cmd = f"maia chat -s {' -s '.join(selected_sources)}"
            if edited_query:
                display_cmd += f" {edited_query}"
            print_text(f"\nExecuting: {display_cmd}", style="white")

            chat_run(query_args)
            return

    except ImportError as e:
        print_text(f"Error importing recents interface: {e}", style="red")
    except Exception as e:
        logging.error(f"An unexpected error occurred in chat_run_recents: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")

def chat_run_browse(args):
    """Run the chat interface with Discord channel browser."""
    import asyncio
    from promaia.cli.discord_commands import handle_discord_browse
    from promaia.config.workspaces import get_workspace_manager
    from promaia.chat.interface import chat
    
    try:
        # Get workspace
        workspace_manager = get_workspace_manager()
        workspace = getattr(args, 'workspace', None)
        
        if not workspace:
            workspace = workspace_manager.get_default_workspace()
            if not workspace:
                print_text("No workspace specified and no default workspace configured.", style="red")
                return
        
        # Validate workspace
        if not workspace_manager.validate_workspace(workspace):
            print_text(f"✗ Workspace '{workspace}' is not properly configured.", style="red")
            return
        
        # Create args for Discord browse
        class BrowseArgs:
            def __init__(self, workspace):
                self.workspace = workspace
        
        browse_args = BrowseArgs(workspace)
        
        # Run the Discord browser and get selected channels
        print_text(f"🎮 Launching Discord channel browser for workspace '{workspace}'...", style="white")
        
        # Run the async Discord browse function
        async def run_browse():
            from promaia.cli.discord_commands import handle_discord_browse
            return await handle_discord_browse(browse_args)
        
        selected_channels = asyncio.run(run_browse())
        
        if not selected_channels:
            print_text("ℹ️  No channels selected for chat.", style="dim")
            return
        
        # Convert selected channels to chat sources format
        sources = []
        filters = []
        
        # Group channels by database
        db_channels = {}
        for db_name, channel_id, channel_name in selected_channels:
            if db_name not in db_channels:
                db_channels[db_name] = []
            db_channels[db_name].append(channel_name)
        
        # Create sources and filters
        for db_name, channels in db_channels.items():
            sources.append(db_name)  # Just the database name
            
            # Create source-prefixed channel filter for this database
            if len(channels) == 1:
                filters.append(f'{db_name}:"channel_name={channels[0]}"')
            else:
                # Multiple channels - use OR filter within the source
                channel_filter = " OR ".join([f"channel_name={ch}" for ch in channels])
                filters.append(f'{db_name}:"({channel_filter})"')
        
        print_text(f"✅ Selected {len(selected_channels)} Discord channels for chat:", style="white")
        for db_name, channels in db_channels.items():
            for channel in channels:
                print_text(f"   • {db_name} → #{channel}", style="white")
        
        # Create modified args for chat
        class ChatArgs:
            def __init__(self, sources, filters, workspace):
                self.sources = sources
                self.workspace = workspace
                self.filters = filters
                self.recent = False
                self.browse = False
                self.non_interactive = False
                self.natural_language = None
        
        chat_args = ChatArgs(sources, filters, workspace)
        
        # Start chat with selected sources
        print_text(f"\n💬 Starting chat with selected Discord channels...", style="white")
        chat_run(chat_args)
        
    except Exception as e:
        logging.error(f"An unexpected error occurred in chat_run_browse: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")


def chat_run_inline_browse(args):
    """
    Run the chat interface with unified browser for Discord databases.
    
    ⚠️  CRITICAL: TOP-LEVEL BROWSER - KEEP IN SYNC WITH EDIT MODE BROWSER ⚠️
    
    This function implements the TOP-LEVEL browser used when starting chat with -b flag.
    It has a TWIN in promaia/chat/interface.py called handle_browse_in_edit_context() and
    handle_manual_browse_edit() that implement the EDIT MODE browser (accessed via /e).
    
    IMPORTANT SYNCHRONIZATION RULES FOR AI ASSISTANTS:
    1. These two browsers MUST work identically - same arguments, same database parsing, same behavior
    2. When you modify THIS function's argument parsing or database handling, you MUST update the
       edit mode browser functions in chat/interface.py with the same logic
    3. When you modify browse-related args.browse parsing here, update it in edit mode too
    4. The context_state dict in chat/interface.py stores browse info - keep format consistent
    5. Both use launch_unified_browser() - any changes to that affect both
    
    BEFORE MODIFYING THIS FUNCTION:
    - Read handle_browse_in_edit_context() in promaia/chat/interface.py
    - Read handle_manual_browse_edit() in promaia/chat/interface.py  
    - Understand how they parse commands and launch browsers
    - Make parallel changes to keep them synchronized
    
    TEST REQUIREMENTS:
    - Test both: `maia chat -b trass.tg` AND using /e to change browse context
    - Verify workspace names work: `maia chat -b trass` AND /e with workspace
    - Verify Discord servers work: `maia chat -b trass.tg` AND /e with servers
    - Verify multi-workspace: `maia chat -b trass koii` AND /e with multiple
    
    See: promaia/chat/interface.py - handle_browse_in_edit_context()
    See: promaia/chat/interface.py - handle_manual_browse_edit()
    """
    from promaia.cli.workspace_browser import launch_unified_browser
    from promaia.config.workspaces import get_workspace_manager
    from promaia.chat.interface import chat
    
    try:
        # Get workspace
        workspace_manager = get_workspace_manager()
        original_workspace = getattr(args, 'workspace', None)
        
        # Resolve workspace
        resolved_workspace = original_workspace
        sources = getattr(args, 'sources', None) or []
        # Flatten nested lists from multiple -b flags: [['trass.tg'], ['trass']] -> ['trass.tg', 'trass']
        raw_browse = getattr(args, 'browse', [])
        browse_databases = []
        if raw_browse:
            for item in raw_browse:
                if isinstance(item, list):
                    browse_databases.extend(item)
                else:
                    browse_databases.append(item)
        
        # If no workspace, try to determine from sources or browse databases
        if not resolved_workspace and sources:
            for source in sources:
                if '.' in source:
                    determined_workspace = source.split('.')[0]
                    if workspace_manager.validate_workspace(determined_workspace):
                        resolved_workspace = determined_workspace
                        print_text(f"INFO: Using workspace '{resolved_workspace}' from source '{source}'.", style="white")
                        break
        
        if not resolved_workspace and browse_databases:
            for browse_db in browse_databases:
                db_name = browse_db.split(':')[0] if ':' in browse_db else browse_db
                if '.' in db_name:
                    determined_workspace = db_name.split('.')[0]
                    if workspace_manager.validate_workspace(determined_workspace):
                        resolved_workspace = determined_workspace
                        print_text(f"INFO: Using workspace '{resolved_workspace}' from browse database '{browse_db}'.", style="white")
                        break
        
        # If still no workspace, use the default
        if not resolved_workspace:
            resolved_workspace = workspace_manager.get_default_workspace()
            if not resolved_workspace:
                print_text("No workspace specified, none could be inferred, and no default workspace is configured.", style="red")
                return
        
        # Validate workspace
        if not workspace_manager.validate_workspace(resolved_workspace):
            print_text(f"✗ Workspace '{resolved_workspace}' is not properly configured.", style="red")
            return
        
        # Parse browse databases and expand workspace names
        database_filter = None
        default_days = None
        
        if browse_databases:
            from promaia.config.workspaces import get_workspace_manager
            from promaia.config.databases import get_database_manager
            workspace_manager = get_workspace_manager()
            db_manager = get_database_manager()
            
            database_filter = []
            for browse_spec in browse_databases:
                if ':' in browse_spec:
                    db_name, days_str = browse_spec.rsplit(':', 1)
                    try:
                        days = int(days_str)
                        if default_days is None:
                            default_days = days
                        
                        # Check if db_name is a workspace
                        if workspace_manager.validate_workspace(db_name):
                            # Expand workspace to all its databases
                            workspace_databases = db_manager.get_workspace_databases(db_name)
                            for db in workspace_databases:
                                if db.browser_include:  # Only include databases visible in browser
                                    database_filter.append(db.get_qualified_name())
                        else:
                            database_filter.append(db_name)
                    except ValueError:
                        database_filter.append(browse_spec)
                else:
                    # Check if this is a workspace name
                    if workspace_manager.validate_workspace(browse_spec):
                        # Expand workspace to all its databases
                        workspace_databases = db_manager.get_workspace_databases(browse_spec)
                        for db in workspace_databases:
                            if db.browser_include:  # Only include databases visible in browser
                                database_filter.append(db.get_qualified_name())
                    else:
                        # It's a specific database name
                        database_filter.append(browse_spec)
        
        # Show what we're browsing
        if database_filter:
            print_text(f"🔍 Launching unified browser for databases: {', '.join(database_filter)}...", style="cyan")
        else:
            print_text(f"🔍 Launching unified browser for workspace '{resolved_workspace}'...", style="cyan")
        
        # Launch unified browser
        browser_result = launch_unified_browser(resolved_workspace, default_days, database_filter)
        selected_sources = browser_result.sources if not browser_result.cancelled else None

        if browser_result.cancelled:
            print_text("ℹ️  Browser cancelled. Continuing with regular sources only.", style="dim")
            # Continue with just the regular sources
            all_sources = sources
            all_filters = getattr(args, 'filters', None) or []
        elif not selected_sources:
            print_text("🗑️  No sources selected. Context cleared.", style="yellow")
            all_sources = sources  # Keep regular -s sources
            all_filters = getattr(args, 'filters', None) or []
        else:
            print_text(f"✅ Selected {len(selected_sources)} sources from unified browser", style="green")
            
            # Process Discord channel sources and convert to database + filter format
            processed_sources = []
            processed_filters = []
            
            # Group Discord channels by database to create proper source + filter combinations
            discord_db_groups = {}
            
            for source in selected_sources:
                if '#' in source:
                    # Discord channel: trass.tg#customer-support:7
                    db_channel, days_part = source.rsplit(':', 1)
                    db_name, channel_name = db_channel.split('#', 1)
                    
                    # Group by database + days combination
                    db_key = f"{db_name}:{days_part}"
                    if db_key not in discord_db_groups:
                        discord_db_groups[db_key] = []
                    discord_db_groups[db_key].append(channel_name)
                else:
                    # Regular database source
                    processed_sources.append(source)
            
            # Convert Discord groups to source + filter combinations
            for db_spec, channels in discord_db_groups.items():
                processed_sources.append(db_spec)
                
                # Create filter for channels
                if len(channels) == 1:
                    # Single channel
                    filter_spec = f"{db_spec}:discord_channel_name={channels[0]}"
                    processed_filters.append(filter_spec)
                else:
                    # Multiple channels - use OR logic
                    channel_conditions = [f"discord_channel_name={ch}" for ch in channels]
                    combined_filter = " or ".join(channel_conditions)
                    filter_spec = f"{db_spec}:({combined_filter})"
                    processed_filters.append(filter_spec)
            
            # Combine regular sources with processed sources
            all_sources = sources + processed_sources
            
            # Combine original filters with Discord filters
            original_filters = getattr(args, 'filters', None) or []
            all_filters = original_filters + processed_filters
        
        # Build the original browse command for display
        original_command_parts = ["maia", "chat"]
        
        if sources:
            for source in sources:
                original_command_parts.extend(["-s", source])
        
        if browse_databases:
            original_command_parts.append("-b")
            for browse_spec in browse_databases:
                original_command_parts.append(browse_spec)
        
        original_filters = getattr(args, 'filters', None) or []
        for filter_expr in original_filters:
            original_command_parts.extend(["-f", f'"{filter_expr}"'])
        
        if original_workspace:
            original_command_parts.extend(["-ws", original_workspace])
        
        original_browse_command = " ".join(original_command_parts)
        
        # Store original Discord channel selections for /e context preservation
        original_discord_selections = []
        if 'selected_sources' in locals() and selected_sources:
            for source in selected_sources:
                if '#' in source:
                    original_discord_selections.append(source)
        
        # Start chat with selected sources
        print_text(f"\n💬 Starting chat with selected sources...", style="white")
        
        chat(
            sources=all_sources, 
            filters=all_filters,
            workspace=resolved_workspace,
            non_interactive=getattr(args, 'non_interactive', False),
            original_browse_command=original_browse_command,
            browse_selections=original_discord_selections  # Store for /e preservation
        )
        
    except Exception as e:
        logging.error(f"An unexpected error occurred in chat_run_inline_browse: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")


def history_run(args):
    """Run the chat history interface."""
    from promaia.chat.history_interface import HistorySelector
    from promaia.chat.interface import chat
    from promaia.config.workspaces import get_workspace_manager
    from promaia.storage.chat_history import ChatHistoryManager
    
    # Handle --clean option
    if getattr(args, 'clean', False):
        try:
            history_manager = ChatHistoryManager()
            removed_count = history_manager.clean_duplicates()
            if removed_count > 0:
                print_text(f"Cleaned up {removed_count} duplicate thread(s).", style="white")
            else:
                print_text("No duplicate threads found.", style="white")
            return
        except Exception as e:
            print_text(f"Error cleaning duplicates: {e}", style="red")
            return
    
    try:
        selector = HistorySelector()
        action, selected_thread = selector.select_thread()
        
        if action == 'quit' or not selected_thread:
            return
        
        if action == 'load':
            # Update the thread's last_accessed timestamp
            from promaia.storage.chat_history import ChatHistoryManager
            history_manager = ChatHistoryManager()
            history_manager.update_thread_access(selected_thread.id)
            
            # Reconstruct the context from the saved thread
            context = selected_thread.context
            
            # Check if this is a SQL query thread
            sql_prompt = context.get('sql_query_prompt')

            print_text(f"\nLoading conversation: {selected_thread.name}", style="white")

            if sql_prompt:
                # This is a SQL query thread - restore using SQL query
                print_text(f"Context: maia chat -sql {sql_prompt}", style="dim")

                # Use cached SQL query content if available, otherwise regenerate
                try:
                    # Check if we have cached content from the saved thread
                    sql_query_content= context.get('sql_query_content')
                    
                    if sql_query_content:
                        print_text("🔄 Using cached natural language results from history", style="dim")
                    else:
                        # Fall back to regenerating if no cached content available
                        from promaia.storage.unified_query import get_query_interface
                        
                        workspace_manager = get_workspace_manager()
                        workspace = context.get('workspace')
                        resolved_workspace = context.get('resolved_workspace')
                        actual_workspace = resolved_workspace or workspace or workspace_manager.get_default_workspace()
                        
                        if actual_workspace:
                            print_text("🤖 Regenerating context from natural language query...", style="white")
                            query_interface = get_query_interface()
                            sql_query_content= query_interface.natural_language_query(sql_prompt, actual_workspace, None)
                        else:
                            print_text("❌ No workspace available to regenerate natural language context", style="red")
                            return
                    
                    if sql_query_content:
                        # Start chat with natural language content (cached or regenerated)
                        workspace = context.get('workspace')
                        resolved_workspace = context.get('resolved_workspace')
                        chat(
                            sources=None,
                            filters=None,
                            workspace=workspace,
                            resolved_workspace=resolved_workspace,
                            non_interactive=False,
                            initial_messages=selected_thread.messages,
                            current_thread_id=selected_thread.id,
                            sql_query_content=sql_query_content,
                            sql_query_prompt=sql_prompt
                        )
                    else:
                        print_text("❌ No natural language content available", style="red")
                        return
                        
                except Exception as e:
                    print_text(f"❌ Error regenerating natural language context: {e}", style="red")
                    print_text("Falling back to empty context...", style="yellow")
                    # Fall back to basic chat without context
                    chat(
                        sources=None,
                        filters=None,
                        workspace=context.get('workspace'),
                        resolved_workspace=context.get('resolved_workspace'),
                        non_interactive=False,
                        initial_messages=selected_thread.messages,
                        current_thread_id=selected_thread.id
                    )
            else:
                # Traditional sources/filters thread
                sources = context.get('sources')
                filters = context.get('filters')
                workspace = context.get('workspace')
                resolved_workspace = context.get('resolved_workspace')
                original_query_format = context.get('original_query_format')
                browse_selections = context.get('browse_selections')
                sql_query_prompt= context.get('sql_query_prompt')
                
                # Check if this was originally a browse command
                if original_query_format and '-b ' in original_query_format:
                    print_text(f"Context: {original_query_format}", style="white")
                    
                    # Restore browse command properly by passing the original format
                    chat(
                        sources=None,  # Don't use decomposed sources for browse commands
                        filters=None,  # Don't use decomposed filters for browse commands
                        workspace=workspace,
                        resolved_workspace=resolved_workspace,
                        non_interactive=False,
                        initial_messages=selected_thread.messages,
                        current_thread_id=selected_thread.id,
                        original_browse_command=original_query_format,
                        browse_selections=browse_selections
                    )
                    return  # Early return for browse commands
                
                # Regular command or fallback
                if context.get('query_command'):
                    print_text(f"Context: {context['query_command']}", style="white")
                
                # Show warning if context might be missing
                if sources:
                    workspace_manager = get_workspace_manager()
                    actual_workspace = resolved_workspace or workspace_manager.get_default_workspace()
                    if actual_workspace:
                        from promaia.config.databases import get_database_manager
                        db_manager = get_database_manager()
                        missing_sources = []
                        for source in sources:
                            source_name = source.split(':')[0]  # Handle source:days format
                            if not db_manager.get_database(source_name):
                                missing_sources.append(source_name)
                        
                        if missing_sources:
                            print_text(f"⚠️  Warning: Some sources from this conversation are no longer available: {', '.join(missing_sources)}", style="yellow")
                            print_text("Continuing with available context...\n", style="white")
                
                # Start chat with the saved context and messages
                chat(
                    sources=sources,
                    filters=filters,
                    workspace=workspace,
                    resolved_workspace=resolved_workspace,
                    non_interactive=False,
                    initial_messages=selected_thread.messages,
                    current_thread_id=selected_thread.id,
                    sql_query_prompt=sql_query_prompt
                )
        
    except ImportError as e:
        print_text(f"Error importing history interface: {e}", style="red")
        print_text("Please check your dependencies.", style="red")
    except Exception as e:
        logging.error(f"An unexpected error occurred in history_run: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")

async def write_run_async(args):
    """Run the write blog post command."""
    try:
        from promaia.write.interface import write_blog_post
    except ImportError as e:
        print_text(f"Error importing write interface: {e}", style="red")
        print_text("Please check your dependencies or run 'pip install -r requirements.txt'", style="yellow")
        return
        
    days_to_use = args.days
    if days_to_use is None:
        try:
            days_input = input("Enter number of days to look back for journal entries (default: from settings): ").strip()
            if days_input:
                days_to_use = int(days_input)
                if days_to_use < 0: days_to_use = 0
            else:
                days_to_use = get_sync_days_setting()
        except ValueError:
            days_to_use = get_sync_days_setting()
            logger.warning(f"Invalid input. Using default: {days_to_use} days.")
    
    if days_to_use == 0:
        logger.info("INFO: Journal entries will NOT be used as reference (--days 0).")

    await write_blog_post(
        days=days_to_use,
        custom_prompt=args.prompt,
        push_to_notion=not args.no_push,
        max_entries=args.max_entries,
        force_openai=args.force_openai,
        no_journal=(days_to_use == 0)
    )

def write_run(args):
    asyncio.run(write_run_async(args))

def model_run(args):
    """Set the default AI model for chat and write commands."""
    try:
        from promaia.chat.interface import get_api_preference, save_api_preference
    except ImportError as e:
        print_text(f"Error importing chat interface: {e}", style="red")
        print_text("Please check your dependencies or run 'pip install -r requirements.txt'", style="yellow")
        return
    
    current_model = get_api_preference()
    logger.info(f"Current model: {current_model}")
    logger.info("\nAvailable models:")
    options = {"1": "anthropic", "2": "openai", "3": "gemini", "4": "llama"}
    api_keys = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "llama": "LLAMA_BASE_URL"
    }
    
    for key, name in options.items():
        if name == "llama":
            # Check if local Llama server is available
            llama_url = os.getenv("LLAMA_BASE_URL", "http://localhost:11434")
            try:
                import requests
                test_url = f"{llama_url.rstrip('/')}/api/tags" if "ollama" in llama_url or ":11434" in llama_url else f"{llama_url.rstrip('/')}/v1/models"
                response = requests.get(test_url, timeout=2)
                key_status = "Server Available" if response.status_code == 200 else "Server Not Responding"
            except Exception:
                key_status = "Server Not Available"
        else:
            key_status = "API Key Found" if os.getenv(api_keys[name]) else "API Key Missing"
        logger.info(f"{key}. {name.capitalize()} ({key_status})")
    
    # Create a more descriptive prompt showing actual model names
    model_names = [f"{key}={name.capitalize()}" for key, name in options.items()]
    prompt = f"\nSelect a model ({', '.join(model_names)}, or Enter to keep current): "
    choice_key = input(prompt).strip()
    
    if not choice_key:
        logger.info("Model selection unchanged.")
        return
    
    if choice_key in options:
        chosen_model = options[choice_key]
        if chosen_model == "llama":
            # Check if local Llama server is available
            llama_url = os.getenv("LLAMA_BASE_URL", "http://localhost:11434")
            try:
                import requests
                test_url = f"{llama_url.rstrip('/')}/api/tags" if "ollama" in llama_url or ":11434" in llama_url else f"{llama_url.rstrip('/')}/v1/models"
                response = requests.get(test_url, timeout=2)
                if response.status_code != 200:
                    logger.error(f"ERROR: Cannot switch to Local Llama: Server not responding at {llama_url}")
                    return
            except Exception as e:
                logger.error(f"ERROR: Cannot switch to Local Llama: Server not available at {llama_url} ({e})")
                return
        elif not os.getenv(api_keys[chosen_model]):
            logger.error(f"ERROR: Cannot switch to {chosen_model.capitalize()}: {api_keys[chosen_model]} environment variable not set." )
            return
        save_api_preference(chosen_model)
        logger.info(f"SUCCESS: Switched default model to: {chosen_model.capitalize()}")
    else:
        logger.error("ERROR: Invalid choice. Please select from the available options.")

def chat_run_workspace_browse(args, workspace_name):
    """Run the chat interface with unified browser for workspace."""
    from promaia.chat.interface import chat
    from promaia.config.workspaces import get_workspace_manager
    
    try:
        workspace_manager = get_workspace_manager()
        
        # Validate workspace
        if not workspace_manager.validate_workspace(workspace_name):
            print_text(f"✗ Workspace '{workspace_name}' is not properly configured.", style="red")
            return
        
        # Get other args
        sources = getattr(args, 'sources', None) or []
        filters = getattr(args, 'filters', None) or []
        mcp_servers = getattr(args, 'mcp_servers', None)
        
        # Add workspace to args so chat function can use it
        args.workspace = workspace_name
        
        # Build the original browse command for display
        original_command_parts = ["maia", "chat"]
        
        # Add browse argument
        original_command_parts.extend(["-b", workspace_name])
        
        # Add any regular sources
        if sources:
            for source in sources:
                original_command_parts.extend(["-s", source])
        
        # Add filters
        if filters:
            for filter_expr in filters:
                original_command_parts.extend(["-f", f'"{filter_expr}"'])
        
        # Add MCP servers
        if mcp_servers:
            for server in mcp_servers:
                original_command_parts.extend(["-mcp", server])
        
        original_browse_command = " ".join(original_command_parts)
        
        # Launch unified browser to get user selections
        from promaia.cli.workspace_browser import launch_unified_browser
        print_text(f"🔍 Launching unified browser for workspace '{workspace_name}'...", style="cyan")
        
        browser_result = launch_unified_browser(workspace_name)
        selected_sources = browser_result.sources if not browser_result.cancelled else None

        if browser_result.cancelled:
            print_text(f"Browser cancelled for workspace '{workspace_name}'. Only prompt, no context.", style="bold yellow")
            final_sources = sources
            final_filters = filters
            browse_selections = []
        elif not selected_sources:
            print_text(f"No sources selected from workspace '{workspace_name}'. Only prompt, no context.", style="bold yellow")
            final_sources = sources
            final_filters = filters
            browse_selections = []
        else:
            print_text(f"📦 Selected {len(selected_sources)} sources from workspace '{workspace_name}'", style="cyan")
            
            # Process browser selections to handle Discord channels correctly
            from promaia.chat.interface import process_browser_selections
            processed_sources, processed_filters = process_browser_selections(selected_sources)
            
            # Combine with any regular sources and filters
            final_sources = sources + processed_sources
            final_filters = filters + processed_filters
            browse_selections = selected_sources.copy()  # Store raw selections for /e preservation
        
        # Call the chat function with workspace and original command format
        chat(
            sources=final_sources,
            filters=final_filters,
            workspace=workspace_name,
            resolved_workspace=workspace_name,
            non_interactive=getattr(args, 'non_interactive', False),
            mcp_servers=mcp_servers,
            original_browse_command=original_browse_command,
            browse_selections=browse_selections  # Store for /e preservation
        )
        
    except Exception as e:
        logging.error(f"An unexpected error occurred in chat_run_workspace_browse: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")

def chat_run_multi_workspace_browse(args, workspace_names):
    """Run the chat interface with unified browser for multiple workspaces."""
    from promaia.chat.interface import chat
    from promaia.config.workspaces import get_workspace_manager
    
    try:
        workspace_manager = get_workspace_manager()
        
        # Validate all workspaces
        invalid_workspaces = [ws for ws in workspace_names if not workspace_manager.validate_workspace(ws)]
        if invalid_workspaces:
            print_text(f"✗ Invalid workspaces: {', '.join(invalid_workspaces)}", style="red")
            return
        
        # Get other args
        sources = getattr(args, 'sources', None) or []
        filters = getattr(args, 'filters', None) or []
        mcp_servers = getattr(args, 'mcp_servers', None)
        
        # Build the original browse command for display and recents
        original_command_parts = ["maia", "chat"]
        
        # Add browse arguments
        original_command_parts.append("-b")
        original_command_parts.extend(workspace_names)
        
        # Add any regular sources
        if sources:
            for source in sources:
                original_command_parts.extend(["-s", source])
        
        # Add filters
        if filters:
            for filter_expr in filters:
                original_command_parts.extend(["-f", f'"{filter_expr}"'])
        
        # Add MCP servers
        if mcp_servers:
            for server in mcp_servers:
                original_command_parts.extend(["-mcp", server])
        
        original_browse_command = " ".join(original_command_parts)
        
        # Launch unified browser for multiple workspaces
        from promaia.cli.workspace_browser import launch_unified_browser
        print_text(f"🔍 Launching unified browser for workspaces: {', '.join(workspace_names)}...", style="cyan")
        
        # For multi-workspace, we need to pass the workspace names to the browser
        # We'll use the first workspace as primary but show databases from all
        primary_workspace = workspace_names[0]
        
        # For multi-workspace browse, pass workspace names directly in the database_filter
        # The browser will detect workspace names and expand to show all databases
        browser_result = launch_unified_browser(None, database_filter=workspace_names)
        selected_sources = browser_result.sources if not browser_result.cancelled else None

        if browser_result.cancelled:
            print_text("ℹ️  Browser cancelled. Continuing with regular sources only.", style="dim")
            all_sources = sources
            all_filters = filters or []
        elif not selected_sources:
            print_text("🗑️  No sources selected. Context cleared.", style="yellow")
            all_sources = sources
            all_filters = filters or []
        else:
            print_text(f"✅ Selected {len(selected_sources)} sources from unified browser", style="green")
            all_sources = sources + selected_sources
            all_filters = filters or []

        # Store browse selections for /e preservation
        browse_selections = selected_sources.copy() if selected_sources else []
        
        # Start chat with the combined sources
        chat(
            sources=all_sources,
            filters=all_filters,
            workspace=primary_workspace,  # Use primary workspace for default
            mcp_servers=mcp_servers,
            original_browse_command=original_browse_command,
            browse_selections=browse_selections  # Store for /e preservation
        )
        
    except Exception as e:
        logging.error(f"An unexpected error occurred in chat_run_multi_workspace_browse: {e}", exc_info=True)
        print_text(f"An unexpected error occurred: {e}", style="red")

# ==================== MAIN ENTRY POINT ====================

def create_parser():
    """
    Create and configure the CLI parser.

    This is separated from main() so it can be reused by the TUI's
    CommandExecutor to execute inline commands.

    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(description="Promaia CLI - Your personal AI assistant")
    parser.add_argument('--debug', action='store_true', help='Enable debug logging.')
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Let database_commands module add its own subparsers
    add_database_commands(subparsers)
    
    # Add 'db' alias for database commands
    db_parser = subparsers.add_parser('db', help='Manage databases (alias for database)')
    db_subparsers = db_parser.add_subparsers(dest='database_command', help='Database commands')
    add_database_commands_to_existing_parser(db_parser, db_subparsers)

    # Let workspace_commands module add its own subparsers
    add_workspace_commands(subparsers)
    
    # Add 'ws' alias for workspace commands  
    ws_parser = subparsers.add_parser('ws', help='Manage workspaces (alias for workspace)')
    ws_subparsers = ws_parser.add_subparsers(dest='workspace_command', help='Workspace commands')
    add_workspace_commands_to_existing_parser(ws_parser, ws_subparsers)
    
    # Add migration commands
    add_migration_commands(subparsers)
    
    # Add 'mig' alias for migration commands
    mig_parser = subparsers.add_parser('mig', help='Data migration commands (alias for migration)')
    mig_subparsers = mig_parser.add_subparsers(dest='migration_command', help='Migration commands')
    add_migration_commands_to_existing_parser(mig_parser, mig_subparsers)
    
    # Add hybrid architecture commands
    from promaia.cli.hybrid_commands import add_hybrid_commands, add_hybrid_commands_to_existing_parser
    add_hybrid_commands(subparsers)
    
    # Add 'hyb' alias for hybrid commands
    hyb_parser = subparsers.add_parser('hyb', help='Hybrid architecture commands (alias for hybrid)')
    hyb_subparsers = hyb_parser.add_subparsers(dest='hybrid_command', help='Hybrid commands')
    add_hybrid_commands_to_existing_parser(hyb_parser, hyb_subparsers)
    
    # Add mail commands
    add_mail_commands(subparsers)
    
    # Add Gmail commands
    add_gmail_commands(subparsers)

    # Add prompt sync commands
    add_prompt_commands(subparsers)

    # Add team management commands
    add_team_commands(subparsers)

    # Add OCR commands
    from promaia.cli.ocr_commands import register_ocr_commands
    register_ocr_commands(subparsers)

    # Add conversation management commands
    add_conversation_commands(subparsers)

    # Add 'conv' alias for conversation commands
    conv_parser = subparsers.add_parser('conv', help='Manage conversations (alias for conversation)')
    conv_subparsers = conv_parser.add_subparsers(dest='conversation_command', help='Conversation commands')
    add_conversation_commands_to_existing_parser(conv_parser, conv_subparsers)

    # Add agent commands (both external and scheduled)
    agent_subparsers = add_agent_commands(subparsers, include_scheduled=True)
    if agent_subparsers:
        # Lazy import to keep module loading cleaner
        from promaia.cli.scheduled_agent_commands import add_scheduled_agent_commands
        add_scheduled_agent_commands(agent_subparsers)

    # Add daemon commands for 24/7 operation
    add_daemon_commands(subparsers)

    # Add feed commands for live agent activity
    add_feed_commands(subparsers)

    # Log viewer command
    log_parser = subparsers.add_parser('log', help='Tail the central Promaia log (or a specific process log)')
    log_parser.add_argument('name', nargs='?', default=None,
                            help='Process log name (e.g. "daemon", "agent-grace-1234"). Omit for central log.')
    log_parser.add_argument('-n', '--lines', type=int, default=50,
                            help='Number of previous lines to show (default: 50)')
    log_parser.add_argument('--list', '-l', action='store_true',
                            help='List available log files')

    # Add TUI commands for unified interface
    add_tui_commands(subparsers)

    # Add service management commands
    add_service_commands(subparsers)

    # Add setup wizard command
    add_setup_commands(subparsers)

    # Add auth management commands
    add_auth_commands(subparsers)

    # Help alias (so `maia help` doesn't error)
    subparsers.add_parser('help', help='Show this help message')

    # Add top-level sync command (alias for database sync)
    sync_parser = subparsers.add_parser('sync', help='Sync databases (alias for database sync)')
    sync_parser.add_argument('--source', '-s', dest='sources', action='append',
                            help='Source specifications (e.g., journal:30, trass.stories:7). Can be used multiple times.')
    sync_parser.add_argument('--browse', '-b', action='append', nargs='*', help='Browse and select Discord channels to sync. Optionally specify databases (e.g., -b trass.discord trass.yeeps_discord)')
    sync_parser.add_argument('--workspace', '-ws', help='Workspace to sync (expands to all enabled databases in workspace with default days)')
    sync_parser.add_argument('--days', type=int, help='Number of days to sync')
    sync_parser.add_argument('--force', action='store_true', help='Force update all files')
    sync_parser.set_defaults(func=handle_database_sync)



    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Interactive chat with multi-source support")
    chat_parser.add_argument(
        "--source", "-s",
        action="append", 
        dest="sources",
        help="Load data from specific database with day filter: 'database_name:days' (e.g., 'journal:7', 'awakenings:all'). Repeat for multiple sources."
    )
    chat_parser.add_argument(
        "--filter", "-f",
        action="append",
        dest="filters",
        help="Add property filters in format 'property_name=value' or '\"Property Name\"=value'. Use quotes for properties with spaces. Can be used multiple times. Examples: 'status=published', '\"Reference\"=true', '\"Blog Status\"=live and created_time>2025-03-01'"
    )
    chat_parser.add_argument(
        "--workspace", "-ws",
        help="Specify which workspace to use for chat (defaults to default workspace)"
    )
    chat_parser.add_argument(
        "--recent", "-r",
        action="store_true",
        help="Show recent queries for selection and re-execution"
    )
    chat_parser.add_argument(
        "--browse", "-b",
        action="append",
        nargs="*",
        help="Launch interactive browser to select sources. For workspaces: '-b workspace_name' (e.g., -b trass). For Discord channels: '-b discord' or specific databases (e.g., -b trass.yp). Without arguments, shows all available sources."
    )
    chat_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run in non-interactive mode for testing"
    )
    chat_parser.add_argument(
        "--sql-query", "-sql",
        action="append",
        nargs="+",
        dest="sql_query",
        help="Use SQL-based natural language queries to search content. Can be used multiple times for separate queries. Example: maia chat -sql 'emails about avask' -sql 'stories about canada'"
    )
    # Deprecated: Keep -nl as an alias for backward compatibility
    chat_parser.add_argument(
        "--natural-language", "-nl",
        action="append",
        nargs="+",
        dest="sql_query",
        help=argparse.SUPPRESS  # Hide from help, deprecated in favor of -sql
    )
    # NOTE: This -sql argument definition MUST stay in sync with:
    # 1. Edit mode parsing in promaia/chat/interface.py (lines ~2148-2163)
    # 2. Top-level processing above (lines ~1507-1520)
    # These are two sides of one feature and must handle multiple -sql arguments identically.
    chat_parser.add_argument(
        "--vector-search", "-vs",
        action="append",
        nargs="+",
        help="Use semantic vector search to find similar content. Can be used multiple times for separate queries. IMPORTANT: Quote queries with special characters like parentheses. Examples: maia chat -vs \"story with (Shared) in title\" -tk 1 or maia chat -vs 'international launch stories' -vs 'product planning discussions'"
    )
    chat_parser.add_argument(
        "--top-k", "-tk",
        type=int,
        default=20,
        help="Maximum number of results to return from vector search (default: 20)"
    )
    chat_parser.add_argument(
        "--threshold", "-th",
        type=float,
        default=0.2,
        help="Minimum similarity threshold for vector search results, 0-1 scale (default: 0.2)"
    )
    chat_parser.add_argument(
        "--mcp", "-mcp",
        action="append",
        dest="mcp_servers",
        help="Include MCP (Model Context Protocol) servers in chat context. Specify server names from mcp_servers.json. Can be used multiple times. Example: maia chat -mcp filesystem -mcp git"
    )
    chat_parser.add_argument(
        "-dc", "--draft-context",
        action="store_true",
        dest="draft_context",
        help="Enable draft context in draft chat (includes email thread and related context)"
    )
    chat_parser.set_defaults(func=chat_run)
    
    # Add 'r' alias for chat with recents
    r_parser = subparsers.add_parser("r", help="Recent chat queries (alias for 'chat --recent')")
    r_parser.add_argument(
        "--workspace", "-ws",
        help="Specify which workspace to use for chat (defaults to default workspace)"
    )
    r_parser.set_defaults(func=lambda args: chat_run_recents(args))

    # History command
    history_parser = subparsers.add_parser("history", help="Browse and load saved chat conversations")
    history_parser.add_argument("--clean", action="store_true", help="Clean up duplicate threads")
    history_parser.set_defaults(func=history_run)
    
    # Add 'h' alias for history
    h_parser = subparsers.add_parser("h", help="Browse and load saved chat conversations (alias for 'history')")
    h_parser.add_argument("--clean", action="store_true", help="Clean up duplicate threads")
    h_parser.set_defaults(func=history_run)

    # Write command
    write_parser = subparsers.add_parser("write", help="Generate blog content using AI")
    write_parser.add_argument("--days", type=int, help="Number of past days of journal entries to use as context (0 to skip journal).")
    write_parser.add_argument("--prompt", type=str, help="Custom prompt/instructions for the blog post generation.")
    write_parser.add_argument("--no-push", action="store_true", help="Save generated post to local 'drafts/' dir instead of pushing to Notion CMS.")
    write_parser.add_argument("--max-entries", type=int, help="Maximum number of journal entries to include in the prompt context.")
    write_parser.add_argument("--force-openai", action="store_true", help="Force using OpenAI for generation, overriding default model.")
    write_parser.set_defaults(func=write_run)

    # Model command
    model_parser = subparsers.add_parser("model", help="Set the default AI model for chat and writing tasks")
    model_parser.set_defaults(func=model_run)

    # CMS command (legacy support for blog/newsletter workflow)
    cms_parser = subparsers.add_parser("cms", help="Content management system operations")
    cms_subparsers = cms_parser.add_subparsers(dest="cms_action", required=True, help="CMS action to perform")
    
    cms_pull_parser = cms_subparsers.add_parser("pull", help="Pull CMS entries for KOii chat context")
    cms_pull_parser.add_argument("--days", type=lambda x: x.lower() if x.lower() == 'all' else int(x), default=30, help="Number of days to look back (default: 30)")
    cms_pull_parser.add_argument("--force", action="store_true", default=False, help="Force pull ignoring last sync time.")
    cms_pull_parser.set_defaults(func=handle_cms_pull)
    
    cms_sync_parser = cms_subparsers.add_parser("sync", help="Sync CMS content to Webflow")
    cms_sync_parser.add_argument("--collection", help="Webflow collection ID (or use WEBFLOW_COLLECTION_ID env var)")
    cms_sync_parser.add_argument("--blog-status-property", default="Blog Status", help="Notion property name for blog status (default: 'Blog Status')")
    cms_sync_parser.add_argument("--force-update", action="store_true", help="Force update even if already synced")
    cms_sync_parser.set_defaults(func=handle_cms_sync)
    
    # Newsletter command
    newsletter_parser = subparsers.add_parser("newsletter", help="Newsletter operations")
    newsletter_subparsers = newsletter_parser.add_subparsers(dest="newsletter_action", required=True, help="Newsletter action")
    
    newsletter_send_parser = newsletter_subparsers.add_parser("send", help="Send newsletters via Resend for eligible CMS pages")
    newsletter_send_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt (use with caution)")
    newsletter_send_parser.set_defaults(func=newsletter_sync_command)
    
    newsletter_test_parser = newsletter_subparsers.add_parser("test", help="Test newsletter generation without sending")
    newsletter_test_parser.add_argument("--email", action="append", help="Email address to send test to (can be used multiple times)")
    newsletter_test_parser.set_defaults(func=newsletter_test_command)

    # Add 'news' alias for newsletter
    news_parser = subparsers.add_parser("news", help="Newsletter operations (alias for newsletter)")
    news_subparsers = news_parser.add_subparsers(dest="newsletter_action", required=True, help="Newsletter action")

    news_send_parser = news_subparsers.add_parser("send", help="Send newsletters via Resend for eligible CMS pages")
    news_send_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt (use with caution)")
    news_send_parser.set_defaults(func=newsletter_sync_command)

    news_test_parser = news_subparsers.add_parser("test", help="Test newsletter generation without sending")
    news_test_parser.add_argument("--email", action="append", help="Email address to send test to (can be used multiple times)")
    news_test_parser.set_defaults(func=newsletter_test_command)
    


    # Add conversion commands
    add_conversion_commands(subparsers)

    # Add Discord commands (optional)
    try:
        from promaia.cli.discord_commands import setup_discord_commands
        setup_discord_commands(subparsers)
    except ImportError:
        pass  # Discord commands not available

    # Add edit command group
    edit_parser = subparsers.add_parser("edit", help="Commands for editing local JSON files and syncing with Notion")
    edit_subparsers = edit_parser.add_subparsers(dest="edit_action", required=True, help="Edit action to perform")
    
    # Edit list-pages command
    edit_list_parser = edit_subparsers.add_parser("list-pages", help="List pages available for editing")
    edit_list_parser.add_argument("content_type", help="Content type to list pages for")
    edit_list_parser.add_argument("--page-id", help="Specific page ID to filter")
    edit_list_parser.add_argument("--title-filter", help="Filter pages by title")
    edit_list_parser.set_defaults(func=handle_edit_list_pages)
    
    # Edit update command
    edit_update_parser = edit_subparsers.add_parser("update", help="Update a page's properties and content")
    edit_update_parser.add_argument("content_type", help="Content type of the page")
    edit_update_parser.add_argument("page_id", help="Page ID to update")
    edit_update_parser.add_argument("--title", help="New title for the page")
    edit_update_parser.add_argument("--property", action="append", help="Update property: --property 'Name=Value'")
    edit_update_parser.add_argument("--add-paragraph", help="Add a paragraph with this text")
    edit_update_parser.add_argument("--add-heading", help="Add a heading with this text")
    edit_update_parser.add_argument("--heading-level", type=int, default=1, help="Heading level (1-3)")
    edit_update_parser.add_argument("--no-backup", action="store_true", help="Don't create backup before editing")
    edit_update_parser.set_defaults(func=handle_edit_update)
    
    # Edit show command
    edit_show_parser = edit_subparsers.add_parser("show", help="Show detailed information about a page")
    edit_show_parser.add_argument("content_type", help="Content type of the page")
    edit_show_parser.add_argument("page_id", help="Page ID to show")
    edit_show_parser.set_defaults(func=handle_edit_show)
    
    # Edit sync command
    edit_sync_parser = edit_subparsers.add_parser("sync", help="Sync local changes back to Notion")
    edit_sync_parser.add_argument("content_type", nargs="?", help="Content type to sync (optional)")
    edit_sync_parser.add_argument("--page-id", help="Sync specific page only")
    edit_sync_parser.add_argument("--force", action="store_true", help="Force sync even if conflicts detected")
    edit_sync_parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without actually syncing")
    edit_sync_parser.set_defaults(func=handle_edit_sync)
    
    # Edit status command
    edit_status_parser = edit_subparsers.add_parser("status", help="Show sync status of local pages")
    edit_status_parser.add_argument("content_type", nargs="?", help="Content type to check status for (optional)")
    edit_status_parser.set_defaults(func=handle_edit_status)

    # Discord bot command
    discord_parser = subparsers.add_parser("discord-bot", help="Start Promaia Discord bot")
    discord_parser.add_argument("--workspace", "-w", default="koii", help="Workspace to use for bot configuration")
    discord_parser.add_argument("--token", help="Discord bot token (optional, will use credentials file if not provided)")
    discord_parser.set_defaults(func=handle_discord_bot)

    return parser


def main():
    """Main function for the Maia CLI."""
    parser = create_parser()
    args = parser.parse_args()

    # Configure logging
    # Check both CLI flag and environment variable for debug mode
    debug_mode = args.debug or os.getenv("MAIA_DEBUG", "0") == "1"
    log_level = logging.DEBUG if debug_mode else logging.WARNING  # Use WARNING to suppress INFO messages
    
    # In non-interactive mode (like in the app), send logs to a file
    # and only critical errors to stderr.
    if not sys.stdout.isatty():
        logging.basicConfig(level=log_level,
                            format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
                            filename='maia_desktop.log',
                            filemode='w')
        # Also log critical errors to stderr for the app to see
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(stderr_handler)
    else:
        # Standard terminal logging
        logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    
    # Suppress noisy HTTP request logging unless in debug mode
    if not debug_mode:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("notion_client").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)

    # Ensure MAIA_DEBUG environment variable matches the determined debug mode
    if debug_mode:
        os.environ["MAIA_DEBUG"] = "1"
        logger.info("Maia Debug Mode Enabled")
    else:
        os.environ["MAIA_DEBUG"] = "0"

    # Legacy registry validation disabled - hybrid database system handles this now
    # The hybrid storage architecture provides all necessary validation and functionality

    # If no command specified, launch chat in agent mode
    if args.command is None:
        from promaia.chat.interface import chat
        chat()
        return

    if args.command == "help":
        parser.print_help()
        return

    # Handle commands
    if args.command in ["chat", "model", "write", "r", "history", "h"]:
        args.func(args)
    elif args.command == "cms":
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            cms_parser.print_help()
    elif args.command in ["newsletter", "news"]:
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            newsletter_parser.print_help()
    elif args.command in ["database", "db"]:
        if hasattr(args, 'database_command') and args.database_command:
            if args.database_command in ["list", "ls"]:
                asyncio.run(handle_database_list(args))
            elif args.database_command == "add":
                asyncio.run(handle_database_add(args))
            elif args.database_command in ["remove", "rm"]:
                asyncio.run(handle_database_remove(args))
            elif args.database_command == "test":
                asyncio.run(handle_database_test(args))
            elif args.database_command == "sync":
                asyncio.run(handle_database_sync(args))
            elif args.database_command == "info":
                asyncio.run(handle_database_info(args))
            elif args.database_command == "push":
                asyncio.run(handle_database_push(args))
            elif args.database_command in ["status", "st"]:
                asyncio.run(handle_database_status(args))
            elif args.database_command in ["list-sources", "sources"]:
                asyncio.run(handle_database_list_sources(args))
            elif args.database_command == "register-markdown-files":
                asyncio.run(handle_register_markdown_files(args))
            elif args.database_command == "validate-registry":
                asyncio.run(handle_validate_registry(args))
            elif args.database_command == "add-channels":
                asyncio.run(handle_database_add_channels(args))
            elif args.database_command == "remove-channels":
                asyncio.run(handle_database_remove_channels(args))
            elif args.database_command == "purge":
                asyncio.run(handle_database_remove_with_data_purge(args))
            elif args.database_command in ["remove-interactive", "rmi"]:
                asyncio.run(handle_database_remove_interactive(args))
            elif args.database_command in ["remove-channels-interactive", "rmci"]:
                asyncio.run(handle_channel_remove_interactive(args))
            elif args.database_command == "edit":
                asyncio.run(handle_database_edit(args))
            elif args.database_command == "channel":
                channel_cmd = getattr(args, 'channel_command', None)
                if channel_cmd == "add":
                    asyncio.run(handle_database_add_channels(args))
                elif channel_cmd == "remove":
                    asyncio.run(handle_database_remove_channels(args))
                elif channel_cmd == "list":
                    asyncio.run(handle_database_channel_list(args))
                elif channel_cmd == "rmi":
                    asyncio.run(handle_channel_remove_interactive(args))
                else:
                    print_text("Channel command requires a subcommand: add, remove, list, rmi", style="red")
            else:
                print_text(f"Unknown database command: {args.database_command}", style="red")
        else:
            print_text("Database command requires a subcommand. Use 'maia database --help' for options.", style="red")
    elif args.command in ["workspace", "ws"]:
        # Handle workspace commands (NEW) - use func attribute for dynamic routing
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            print_text("Workspace command requires a subcommand. Use 'maia workspace --help' for options.", style="red")
    elif args.command in ["migration", "mig"]:
        # Handle migration commands
        if hasattr(args, 'migration_command') and args.migration_command:
            if hasattr(args, 'func'):
                args.func(args)
            else:
                print_text(f"No function assigned to migration command: {args.migration_command}", style="red")
        else:
            print_text("Migration command requires a subcommand. Use 'maia migration --help' for options.", style="red")
    elif args.command in ["hybrid", "hyb"]:
        # Handle hybrid architecture commands
        if hasattr(args, 'hybrid_command') and args.hybrid_command:
            if hasattr(args, 'func'):
                args.func(args)
            else:
                print_text(f"No function assigned to hybrid command: {args.hybrid_command}", style="red")
        else:
            print_text("Hybrid command requires a subcommand. Use 'maia hybrid --help' for options.", style="red")
    elif args.command == "sync":
        # Handle top-level sync command (alias for database sync)
        asyncio.run(handle_database_sync(args))
    elif args.command in ["convert", "list-formats", "cleanup"]:
        # Handle conversion commands
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            print_text(f"No function assigned to command: {args.command}", style="red")
    elif args.command == "edit":
        # Handle edit command
        if hasattr(args, 'func'):
            args.func(args)
        else:
            edit_parser.print_help()
    elif args.command == "mail":
        # Handle mail commands
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            print_text("Mail command error: no function assigned", style="red")
    elif args.command == "gmail":
        # Handle Gmail commands
        if hasattr(args, 'gmail_command') and args.gmail_command:
            if hasattr(args, 'func'):
                asyncio.run(args.func(args))
            else:
                print_text(f"No function assigned to gmail command: {args.gmail_command}", style="red")
        else:
            print_text("Gmail command requires a subcommand. Use 'maia gmail --help' for options.", style="red")
    elif args.command == "prompt":
        # Handle prompt commands
        if hasattr(args, 'prompt_action') and args.prompt_action:
            if hasattr(args, 'func'):
                args.func(args)
            else:
                print_text(f"No function assigned to prompt command: {args.prompt_action}", style="red")
        else:
            print_text("Prompt command requires a subcommand. Use 'maia prompt --help' for options.", style="red")
    elif args.command == "discord":
        # Handle Discord commands
        if hasattr(args, 'discord_command') and args.discord_command:
            if hasattr(args, 'func'):
                asyncio.run(args.func(args))
            else:
                print_text(f"No function assigned to discord command: {args.discord_command}", style="red")
        else:
            print_text("Discord command requires a subcommand. Use 'maia discord --help' for options.", style="red")
    elif args.command == "team":
        # Handle team commands
        if hasattr(args, 'team_command') and args.team_command:
            if hasattr(args, 'func'):
                asyncio.run(args.func(args))
            else:
                print_text(f"No function assigned to team command: {args.team_command}", style="red")
        else:
            print_text("Team command requires a subcommand. Use 'maia team --help' for options.", style="red")
    elif args.command == "ocr":
        # Handle OCR commands
        if hasattr(args, 'ocr_command') and args.ocr_command:
            if hasattr(args, 'func'):
                asyncio.run(args.func(args))
            else:
                print_text(f"No function assigned to OCR command: {args.ocr_command}", style="red")
        else:
            print_text("OCR command requires a subcommand. Use 'maia ocr --help' for options.", style="red")
    elif args.command == "agent":
        # Handle agent commands
        if hasattr(args, 'agent_command') and args.agent_command:
            if hasattr(args, 'func'):
                try:
                    asyncio.run(args.func(args))
                except KeyboardInterrupt:
                    pass
            else:
                print_text(f"No function assigned to agent command: {args.agent_command}", style="red")
        else:
            print_text("Agent command requires a subcommand. Use 'maia agent --help' for options.", style="red")
    elif args.command == "setup":
        # Handle setup wizard
        if hasattr(args, 'func'):
            args.func(args)
    elif args.command == "auth":
        # Handle auth management commands
        if hasattr(args, 'func'):
            args.func(args)
        else:
            print_text("Auth command requires a subcommand. Use 'maia auth --help' for options.", style="red")
    elif args.command == "services":
        # Handle service management commands
        if hasattr(args, 'func'):
            args.func(args)
        else:
            print_text("Services command requires a subcommand. Use 'maia services --help' for options.", style="red")
    elif args.command == "daemon":
        # Handle daemon commands
        if hasattr(args, 'func'):
            args.func(args)
        else:
            print_text("Daemon command requires a subcommand. Use 'maia daemon --help' for options.", style="red")
    elif args.command == "feed":
        # Handle feed commands
        if hasattr(args, 'func'):
            try:
                asyncio.run(args.func(args))
            except KeyboardInterrupt:
                pass
        else:
            print_text("Feed command requires subcommand. Use 'maia feed --help' for options.", style="red")
    elif args.command == "log":
        # Handle 'maia log clear' to remove context logs
        log_name = getattr(args, 'name', None)
        if log_name == 'clear':
            from promaia.utils.env_writer import get_data_dir
            import shutil
            context_log_dir = get_data_dir() / "context_logs"
            if not context_log_dir.exists():
                print("No context logs found.")
            else:
                # Show what will be cleared
                total_size = 0
                total_files = 0
                for subdir in sorted(context_log_dir.iterdir()):
                    if subdir.is_dir():
                        files = list(subdir.rglob('*'))
                        file_count = sum(1 for f in files if f.is_file())
                        dir_size = sum(f.stat().st_size for f in files if f.is_file())
                        total_size += dir_size
                        total_files += file_count
                        if file_count > 0:
                            print(f"  {subdir.name}: {file_count} files ({dir_size / 1024 / 1024:.1f} MB)")
                if total_files == 0:
                    print("No context log files found.")
                else:
                    print(f"\nTotal: {total_files} files ({total_size / 1024 / 1024:.1f} MB)")
                    try:
                        confirm = input("Delete all context logs? [y/N] ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        confirm = ''
                    if confirm == 'y':
                        shutil.rmtree(context_log_dir)
                        context_log_dir.mkdir(parents=True, exist_ok=True)
                        print("Context logs cleared.")
                    else:
                        print("Cancelled.")
            sys.exit(0)

        # Tail a log file
        from promaia.agents.feed_watchers import LOGS_DIR, CENTRAL_LOG_PATH
        if getattr(args, 'list', False):
            # List available logs
            if LOGS_DIR.exists():
                log_files = sorted(LOGS_DIR.glob('*.log'))
                if log_files:
                    print_text(f"\n📋 Log files in {LOGS_DIR}/\n", style="bold cyan")
                    for lf in log_files:
                        size = lf.stat().st_size
                        label = " (central)" if lf.name == "promaia.log" else ""
                        print(f"  {lf.name}{label}  ({size:,} bytes)")
                    print()
                else:
                    print("No log files found yet.")
            else:
                print(f"Log directory does not exist yet: {LOGS_DIR}")
        else:
            log_name = getattr(args, 'name', None)
            if log_name:
                log_path = LOGS_DIR / f"{log_name}.log"
                if not log_path.exists():
                    # Try with .log already appended
                    log_path = LOGS_DIR / log_name
                if not log_path.exists():
                    print(f"Log file not found: {log_path}")
                    print(f"Use 'maia log --list' to see available logs.")
                    sys.exit(1)
            else:
                log_path = CENTRAL_LOG_PATH
                if not log_path.exists():
                    print(f"No log file yet at {log_path}")
                    print("Run an agent first to generate logs.")
                    sys.exit(1)
            lines = getattr(args, 'lines', 50)
            try:
                import subprocess as _sp
                _sp.run(['tail', '-n', str(lines), '-f', str(log_path)])
            except KeyboardInterrupt:
                pass
    elif args.command == "tui":
        # Handle TUI command
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            print_text("TUI command not properly configured", style="red")
    elif args.command == "discord-bot":
        # Handle Discord bot command
        if hasattr(args, 'func'):
            asyncio.run(args.func(args))
        else:
            print_text("Discord bot command not properly configured", style="red")
    else:
        parser.print_help()

if __name__ == "__main__":
    main() 