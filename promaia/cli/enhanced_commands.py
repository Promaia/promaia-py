"""
Enhanced commands that support multi-source queries and the new database system.
"""
import asyncio
import argparse
import logging
from typing import List, Dict, Any, Optional

from promaia.config.databases import get_database_manager, get_database_config
from promaia.connectors import ConnectorRegistry
from promaia.connectors.base import QueryFilter, DateRangeFilter
from promaia.cli.database_commands import parse_source_specs, build_filters, build_date_filter

logger = logging.getLogger(__name__)

async def handle_enhanced_chat(args):
    """Handle enhanced chat command with multi-source support."""
    from promaia.chat.interface import chat
    
    # If sources are specified, sync them first
    if hasattr(args, 'sources') and args.sources:
        print("Syncing specified sources for chat context...")
        await sync_sources_for_chat(args.sources, args)
    
    # Run the chat interface
    chat(args)

async def handle_enhanced_write(args):
    """Handle enhanced write command with multi-source support."""
    from promaia.write.interface import write_blog_post
    
    # If sources are specified, sync them first
    if hasattr(args, 'sources') and args.sources:
        print("Syncing specified sources for writing context...")
        await sync_sources_for_write(args.sources, args)
    
    # Run the write interface
    await write_blog_post(args)

async def sync_sources_for_chat(sources: List[str], args):
    """Sync specified sources for chat context."""
    db_manager = get_database_manager()
    source_specs = parse_source_specs(sources)

    # Also sync Notion-backed prompts if any exist
    try:
        from promaia.cli.notion_prompt_manager import sync_all_notion_prompts
        prompt_results = await sync_all_notion_prompts()
        if prompt_results['synced']:
            print(f"✓ Synced {len(prompt_results['synced'])} Notion-backed prompt(s)")
    except Exception as e:
        logger.debug(f"Could not sync Notion prompts: {e}")

    for source_spec in source_specs:
        db_name = source_spec["name"]
        db_config = db_manager.get_database(db_name)

        if not db_config:
            print(f"⚠ Database '{db_name}' not found, skipping")
            continue

        try:
            connector = ConnectorRegistry.get_connector(db_config.source_type, db_config.to_dict())
            if not connector:
                print(f"⚠ No connector for {db_config.source_type}, skipping {db_name}")
                continue

            # Build filters
            filters = build_filters(source_spec, db_config)
            date_filter = build_date_filter(source_spec, db_config, args)

            # Sync to local storage
            result = await connector.sync_to_local(
                output_directory=db_config.output_directory,
                filters=filters,
                date_filter=date_filter,
                include_properties=db_config.include_properties,
                force_update=getattr(args, 'force', False),
                excluded_properties=db_config.excluded_properties
            )

            print(f"✓ {db_name}: {result.pages_saved} pages synced")

        except Exception as e:
            print(f"✗ Failed to sync {db_name}: {e}")

async def sync_sources_for_write(sources: List[str], args):
    """Sync specified sources for writing context."""
    # Similar to chat but might have different requirements
    await sync_sources_for_chat(sources, args)

def add_enhanced_commands(subparsers):
    """Add enhanced commands that support multi-source queries."""
    
    # Enhanced chat command
    chat_parser = subparsers.add_parser('chat', help='Interactive chat with multi-source support')
    chat_parser.add_argument('--sources', nargs='+', 
                           help='Source specifications (e.g., journal[date>-30d] cms[status=published])')
    chat_parser.add_argument('--days', type=int, help='Default days for sources without specific filters')
    chat_parser.add_argument('--force', action='store_true', help='Force sync all sources')
    chat_parser.set_defaults(func=handle_enhanced_chat)
    
    # Enhanced write command  
    write_parser = subparsers.add_parser('write', help='Generate content with multi-source support')
    write_parser.add_argument('--sources', nargs='+',
                            help='Source specifications (e.g., journal[date>-30d] cms[status=published])')
    write_parser.add_argument('--days', type=int, help='Default days for sources without specific filters')
    write_parser.add_argument('--prompt', type=str, help='Custom prompt for content generation')
    write_parser.add_argument('--no-push', action='store_true', help='Save to drafts instead of pushing to CMS')
    write_parser.add_argument('--force', action='store_true', help='Force sync all sources')
    write_parser.set_defaults(func=handle_enhanced_write)

# Example usage functions for the new syntax
async def example_multi_source_queries():
    """Examples of how the new multi-source system works."""
    
    # Example 1: Chat with last month from journal and last week from CMS
    # maia chat --sources journal[date>-30d] cms[date>-7d,status=published]
    
    # Example 2: Write using specific date ranges from multiple sources
    # maia write --sources journal[date>2024-01-01,date<2024-01-31] stories[team=alpha]
    
    # Example 3: Sync specific databases with filters
    # maia database sync --sources journal[date>-7d] cms[status=to_push]
    
    pass 