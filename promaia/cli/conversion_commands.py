"""
Format conversion commands for the Maia CLI.
"""
import asyncio
import argparse
import logging
from typing import List, Dict, Any, Optional

from promaia.config.databases import get_database_manager
from promaia.storage.converters import (
    convert_json_to_markdown, convert_markdown_to_json,
    list_available_formats, cleanup_converted_files
)

logger = logging.getLogger(__name__)

async def handle_convert_command(args):
    """Handle 'maia convert' command."""
    db_manager = get_database_manager()
    database_name = args.database
    
    # Get database config
    db_config = db_manager.get_database(database_name)
    if not db_config:
        print(f"✗ Database '{database_name}' not found")
        print(f"Available databases: {', '.join(db_manager.list_databases())}")
        return
    
    content_type = db_config.nickname
    from_format = args.from_format
    to_format = args.to_format
    days = getattr(args, 'days', None)
    files = getattr(args, 'files', None)
    include_properties = getattr(args, 'include_properties', True)
    
    print(f"Converting {database_name} from {from_format} to {to_format}")
    if days:
        print(f"  - Converting last {days} days")
    if files:
        print(f"  - Converting specific files: {files}")
    
    try:
        if from_format == "json" and to_format == "markdown":
            created_files = await convert_json_to_markdown(
                content_type=content_type,
                days=days,
                specific_files=files,
                include_properties=include_properties
            )
            
            if created_files:
                print(f"✓ Successfully converted {len(created_files)} files")
                print(f"  Markdown files saved to: data/md/{content_type}/")
                if len(created_files) <= 5:  # Show files if not too many
                    for file_path in created_files:
                        print(f"    - {file_path}")
                else:
                    print(f"    - ... and {len(created_files) - 3} more files")
            else:
                print("✗ No files were converted")
                
        elif from_format == "markdown" and to_format == "json":
            created_files = convert_markdown_to_json(
                content_type=content_type,
                days=days,
                specific_files=files
            )
            print("✗ Markdown to JSON conversion not yet implemented")
            
        else:
            print(f"✗ Conversion from {from_format} to {to_format} not supported")
            print("Supported conversions:")
            print("  - json → markdown")
            print("  - markdown → json (coming soon)")
            
    except Exception as e:
        print(f"✗ Conversion failed: {e}")
        logger.error(f"Conversion error: {e}")

async def handle_list_formats_command(args):
    """Handle 'maia list-formats' command."""
    db_manager = get_database_manager()
    database_name = args.database
    
    # Get database config
    db_config = db_manager.get_database(database_name)
    if not db_config:
        print(f"✗ Database '{database_name}' not found")
        print(f"Available databases: {', '.join(db_manager.list_databases())}")
        return
    
    content_type = db_config.nickname
    print(f"Available formats for database '{database_name}' ({content_type}):")
    
    try:
        formats = list_available_formats(content_type)
        
        if not formats:
            print("  No formats found. Run a sync first to populate data.")
            return
        
        for format_name, info in formats.items():
            print(f"\n  {format_name.upper()}:")
            print(f"    Directory: {info['directory']}")
            print(f"    File count: {info['file_count']}")
            if info['latest_file']:
                print(f"    Latest file: {info['latest_file']}")
            else:
                print(f"    Latest file: None")
                
    except Exception as e:
        print(f"✗ Failed to list formats: {e}")
        logger.error(f"List formats error: {e}")

async def handle_cleanup_command(args):
    """Handle 'maia cleanup' command."""
    db_manager = get_database_manager()
    database_name = args.database
    format_type = args.format
    days = args.days
    
    # Get database config
    db_config = db_manager.get_database(database_name)
    if not db_config:
        print(f"✗ Database '{database_name}' not found")
        print(f"Available databases: {', '.join(db_manager.list_databases())}")
        return
    
    content_type = db_config.nickname
    print(f"Cleaning up {format_type} files older than {days} days for {database_name}")
    
    try:
        removed_count = cleanup_converted_files(
            content_type=content_type,
            format_type=format_type,
            days=days
        )
        
        if removed_count > 0:
            print(f"✓ Removed {removed_count} old {format_type} files")
        else:
            print(f"No old {format_type} files found to remove")
            
    except Exception as e:
        print(f"✗ Cleanup failed: {e}")
        logger.error(f"Cleanup error: {e}")

def add_conversion_commands(subparsers):
    """Add conversion-related commands to the CLI."""
    
    # Convert command
    convert_parser = subparsers.add_parser(
        'convert',
        help='Convert between storage formats',
        description='Convert files between JSON and markdown formats'
    )
    convert_parser.add_argument(
        'database',
        help='Database name to convert'
    )
    convert_parser.add_argument(
        '--from',
        dest='from_format',
        choices=['json', 'markdown'],
        required=True,
        help='Source format'
    )
    convert_parser.add_argument(
        '--to',
        dest='to_format', 
        choices=['json', 'markdown'],
        required=True,
        help='Target format'
    )
    convert_parser.add_argument(
        '--days',
        type=int,
        help='Convert files from the last N days (default: all files)'
    )
    convert_parser.add_argument(
        '--files',
        nargs='+',
        help='Specific files to convert (full paths)'
    )
    convert_parser.add_argument(
        '--no-properties',
        dest='include_properties',
        action='store_false',
        help='Exclude properties from markdown conversion'
    )
    convert_parser.set_defaults(func=handle_convert_command)
    
    # List formats command
    list_formats_parser = subparsers.add_parser(
        'list-formats',
        help='List available storage formats for a database',
        description='Show what formats are available and file counts'
    )
    list_formats_parser.add_argument(
        'database',
        help='Database name to check'
    )
    list_formats_parser.set_defaults(func=handle_list_formats_command)
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser(
        'cleanup',
        help='Clean up old converted files',
        description='Remove old converted files to save space'
    )
    cleanup_parser.add_argument(
        'database',
        help='Database name to clean up'
    )
    cleanup_parser.add_argument(
        '--format',
        choices=['md', 'markdown'],
        default='md',
        help='Format to clean up (default: md)'
    )
    cleanup_parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Remove files older than N days (default: 30)'
    )
    cleanup_parser.set_defaults(func=handle_cleanup_command)