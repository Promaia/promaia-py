"""
CLI commands for migrating data to the new directory structure.
"""
import argparse
import json
from typing import Dict, Any
from pathlib import Path

from promaia.storage.unified_storage import get_unified_storage
from promaia.storage.hybrid_storage import get_hybrid_registry

def handle_migration_migrate(args):
    """
    Migrate existing data to the new directory structure.
    
    This command will:
    - Move markdown files from old structure to: data/md/notion/{workspace}/{database}/
    - Move JSON files to flat structure: data/json/
    - Register JSON files in the new SQLite registry
    """
    dry_run = not args.execute
        
    print(f"🔄 {'DRY RUN: ' if dry_run else ''}Migrating data to new directory structure...")
    print()
    
    try:
        storage = get_unified_storage()
        report = storage.migrate_existing_data(dry_run=dry_run)
        
        # Display migration report
        if report['markdown_migrations']:
            print(f"📁 Markdown file migrations ({len(report['markdown_migrations'])}):")
            for migration in report['markdown_migrations']:
                status = "→" if dry_run else "✓"
                print(f"  {status} {migration['database']}: {migration['from']} → {migration['to']}")
            print()
        
        if report['json_migrations']:
            print(f"📄 JSON file migrations ({len(report['json_migrations'])}):")
            for migration in report['json_migrations']:
                status = "→" if dry_run else "✓"
                print(f"  {status} {migration['database']}: {migration['from']} → {migration['to']}")
            print()
        
        if report['errors']:
            print(f"❌ Errors ({len(report['errors'])}):")
            for error in report['errors']:
                print(f"  • {error}")
            print()
        
        # Summary
        total_migrations = len(report['markdown_migrations']) + len(report['json_migrations'])
        if total_migrations == 0:
            print("✅ No files need migration - data structure is already up to date!")
        elif dry_run:
            print(f"📋 Migration plan: {total_migrations} files would be migrated")
            print("   Run with --execute to perform the actual migration")
        else:
            print(f"✅ Migration complete: {total_migrations} files migrated successfully")
            
            # Show registry stats after migration
            registry_stats = get_hybrid_registry().get_stats()
            print(f"📊 JSON registry now contains {registry_stats['total_content']} entries")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        raise

def handle_migration_show_current(args):
    """Show the current data directory structure."""
    print("📂 Current data directory structure:")
    print()
    
    from promaia.utils.env_writer import get_data_subdir
    data_dir = get_data_subdir()
    if not data_dir.exists():
        print("   No data directory found")
        return
    
    def show_tree(path: Path, prefix: str = "", max_depth: int = 4, current_depth: int = 0):
        if current_depth >= max_depth:
            return
            
        items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name))
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            current_prefix = "└── " if is_last else "├── "
            
            if item.is_dir():
                print(f"{prefix}{current_prefix}{item.name}/")
                next_prefix = prefix + ("    " if is_last else "│   ")
                show_tree(item, next_prefix, max_depth, current_depth + 1)
            else:
                print(f"{prefix}{current_prefix}{item.name}")
    
    show_tree(data_dir)

def handle_migration_show_target(args):
    """Show the target directory structure after migration."""
    print("🎯 Target directory structure:")
    print()
    print("data/")
    print("├── md/")
    print("│   └── notion/")
    print("│       ├── acme/")
    print("│       │   ├── journal/")
    print("│       │   ├── stories/")
    print("│       │   ├── epics/")
    print("│       │   ├── projects/")
    print("│       │   ├── cms/")
    print("│       │   └── awakenings/")
    print("│       └── other/")
    print("│           ├── journal/")
    print("│           └── stories/")
    print("├── json/")
    print("│   └── [flat structure with all JSON files]")
    print("└── hybrid_metadata.db")
    print("    └── [SQLite hybrid registry with optimized tables]")

def handle_migration_registry_stats(args):
    """Show JSON content registry statistics."""
    try:
        registry = get_hybrid_registry()
        stats = registry.get_stats()
        
        print("📊 JSON Content Registry Statistics:")
        print()
        print(f"Total content entries: {stats['total_content']}")
        print(f"Recent syncs (24h): {stats['recent_syncs']}")
        print(f"Registry database: {stats['db_path']}")
        print()
        
        if stats['by_workspace']:
            print("By workspace:")
            for workspace, count in stats['by_workspace'].items():
                print(f"  {workspace}: {count} entries")
            print()
        
        if stats['by_database']:
            print("By database:")
            for workspace, databases in stats['by_database'].items():
                print(f"  {workspace}:")
                for db_name, count in databases.items():
                    print(f"    {db_name}: {count} entries")
            print()
            
    except Exception as e:
        print(f"❌ Error getting registry stats: {e}")

def handle_migration_list_content(args):
    """List content from the JSON registry."""
    try:
        registry = get_hybrid_registry()
        content_list = registry.list_content(
            workspace=args.workspace,
            database_name=args.database,
            limit=args.limit
        )
        
        if not content_list:
            print("No content found matching the criteria")
            return
        
        print(f"📄 Content entries ({len(content_list)} shown):")
        print()
        
        for item in content_list:
            print(f"• {item['title']}")
            print(f"  ID: {item['page_id']}")
            print(f"  Workspace: {item['workspace']}")
            print(f"  Database: {item['database_name']}")
            print(f"  Synced: {item['synced_time']}")
            print(f"  File: {item['file_path']}")
            print()
            
    except Exception as e:
        print(f"❌ Error listing content: {e}")

def handle_migration_cleanup(args):
    """Clean up orphaned entries in the JSON registry."""
    try:
        registry = get_hybrid_registry()
        removed_count = registry.cleanup_orphaned_entries()
        
        if removed_count > 0:
            print(f"✅ Cleaned up {removed_count} orphaned registry entries")
        else:
            print("✅ No orphaned entries found - registry is clean")
            
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")

def handle_file_rename_migration(args):
    """Rename existing markdown files to use date prefixes."""
    import os
    import re
    import json
    from datetime import datetime
    from pathlib import Path
    from promaia.config.databases import get_database_manager
    from promaia.storage.hybrid_storage import get_hybrid_registry
    
    print("🔄 File Rename Migration: Adding date prefixes to markdown files")
    print()
    
    dry_run = args.dry_run
    if dry_run:
        print("📋 DRY RUN MODE - No files will be actually renamed")
        print()
    
    try:
        db_manager = get_database_manager()
        registry = get_hybrid_registry()
        
        renamed_count = 0
        skipped_count = 0
        error_count = 0
        
        # Process each database
        for db_name, db_config in db_manager.databases.items():
            # Skip if specific database requested and this isn't it
            if args.database and db_name != args.database:
                continue
                
            # Skip if specific workspace requested and this isn't it  
            if args.workspace and db_config.workspace != args.workspace:
                continue
            
            print(f"📁 Processing database: {db_name} ({db_config.workspace})")
            
            # Check markdown directory
            md_dir = db_config.markdown_directory
            if not os.path.exists(md_dir):
                print(f"   ⚠️  Markdown directory not found: {md_dir}")
                continue
            
            # Find markdown files without date prefixes
            md_files = list(Path(md_dir).glob("*.md"))
            files_to_rename = []
            
            for md_file in md_files:
                filename = md_file.name
                
                # Check if file already has date prefix (YYYY-MM-DD at start)
                if re.match(r'^\d{4}-\d{2}-\d{2}\s', filename):
                    continue  # Already has date prefix
                
                # Extract page ID from filename  
                page_id_match = re.search(r'([a-f0-9-]{36})\.md$', filename)
                if not page_id_match:
                    print(f"   ⚠️  No page ID found in filename: {filename}")
                    skipped_count += 1
                    continue
                
                page_id = page_id_match.group(1)
                
                # Look for date information
                date_prefix = None
                date_source = None
                
                # Try to get date from JSON registry first
                content_info = registry.get_content_info(page_id)
                if content_info:
                    created_time_str = content_info.get('created_time')
                    if created_time_str:
                        try:
                            created_dt = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
                            date_prefix = created_dt.strftime("%Y-%m-%d")
                            date_source = "registry"
                        except ValueError:
                            pass
                
                # Fallback: try to find corresponding JSON file
                if not date_prefix:
                    json_pattern = f"*{page_id}*.json"
                    from promaia.utils.env_writer import get_data_subdir
                    json_files = list(get_data_subdir().joinpath("json").glob(json_pattern))
                    if json_files:
                        try:
                            with open(json_files[0], 'r', encoding='utf-8') as f:
                                json_data = json.load(f)
                            
                            # Try various date fields
                            for date_field in ['created_time', 'date', 'saved_at']:
                                if date_field in json_data and json_data[date_field]:
                                    try:
                                        date_str = json_data[date_field]
                                        created_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                                        date_prefix = created_dt.strftime("%Y-%m-%d")
                                        date_source = f"json.{date_field}"
                                        break
                                    except ValueError:
                                        continue
                        except Exception as e:
                            print(f"   ⚠️  Error reading JSON for {page_id}: {e}")
                
                # Last resort: use file modification time
                if not date_prefix:
                    try:
                        mtime = md_file.stat().st_mtime
                        created_dt = datetime.fromtimestamp(mtime)
                        date_prefix = created_dt.strftime("%Y-%m-%d")
                        date_source = "file_mtime"
                    except Exception:
                        print(f"   ❌ Could not determine date for: {filename}")
                        error_count += 1
                        continue
                
                # Generate new filename
                # Extract title part (everything before the page ID)
                title_part = filename.replace(f" {page_id}.md", "").replace(f"{page_id}.md", "")
                if not title_part:
                    title_part = "untitled"
                
                new_filename = f"{date_prefix} {title_part} {page_id}.md"
                
                files_to_rename.append({
                    'old_path': md_file,
                    'new_filename': new_filename,
                    'date_prefix': date_prefix,
                    'date_source': date_source,
                    'page_id': page_id
                })
            
            # Show what will be renamed for this database
            if files_to_rename:
                print(f"   📄 Found {len(files_to_rename)} files to rename:")
                for file_info in files_to_rename:
                    status = "→" if dry_run else "✓"
                    print(f"   {status} {file_info['old_path'].name}")
                    print(f"     → {file_info['new_filename']} (date from {file_info['date_source']})")
                
                # Perform the renames
                if not dry_run:
                    for file_info in files_to_rename:
                        try:
                            old_path = file_info['old_path']
                            new_path = old_path.parent / file_info['new_filename']
                            
                            # Check if target already exists
                            if new_path.exists():
                                print(f"   ⚠️  Target already exists, skipping: {file_info['new_filename']}")
                                skipped_count += 1
                                continue
                            
                            # Rename the file
                            old_path.rename(new_path)
                            renamed_count += 1
                            
                        except Exception as e:
                            print(f"   ❌ Error renaming {file_info['old_path'].name}: {e}")
                            error_count += 1
                else:
                    renamed_count += len(files_to_rename)
                
                print()
            else:
                print(f"   ✅ No files need renaming")
                print()
        
        # Summary
        print("📊 Migration Summary:")
        if dry_run:
            print(f"   • {renamed_count} files would be renamed")
        else:
            print(f"   • {renamed_count} files renamed successfully")
        print(f"   • {skipped_count} files skipped")
        print(f"   • {error_count} errors encountered")
        
        if dry_run and renamed_count > 0:
            print()
            print("💡 Run without --dry-run to perform the actual file renames")
        elif renamed_count > 0:
            print()
            print("✅ File rename migration completed successfully!")
            print("   New files synced will automatically use date prefixes.")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        raise

def add_migration_commands(subparsers):
    """Add migration commands to the CLI."""
    migration_parser = subparsers.add_parser('migration', help='Data migration commands')
    migration_subparsers = migration_parser.add_subparsers(dest='migration_command', help='Migration commands')
    add_migration_commands_to_existing_parser(migration_parser, migration_subparsers)

def add_migration_commands_to_existing_parser(parent_parser, subparsers):
    """Helper function to add migration subcommands to any parser with aliases."""
    
    # migrate command
    migrate_parser = subparsers.add_parser('migrate', help='Migrate data to new directory structure')
    migrate_parser.add_argument('--execute', action='store_true', 
                               help='Actually perform the migration (default is dry-run)')
    migrate_parser.set_defaults(func=handle_migration_migrate)
    
    # show-current command
    current_parser = subparsers.add_parser('show-current', help='Show current data structure')
    current_parser.set_defaults(func=handle_migration_show_current)
    
    # show-target command
    target_parser = subparsers.add_parser('show-target', help='Show target data structure')
    target_parser.set_defaults(func=handle_migration_show_target)
    
    # registry-stats command
    stats_parser = subparsers.add_parser('registry-stats', help='Show JSON registry statistics')
    stats_parser.set_defaults(func=handle_migration_registry_stats)
    
    # list-content command
    list_parser = subparsers.add_parser('list-content', help='List content from JSON registry')
    list_parser.add_argument('--workspace', help='Filter by workspace')
    list_parser.add_argument('--database', help='Filter by database name')
    list_parser.add_argument('--limit', type=int, default=10, help='Limit number of results')
    list_parser.set_defaults(func=handle_migration_list_content)
    
    # Add 'ls' alias for list-content
    ls_parser = subparsers.add_parser('ls', help='List content from JSON registry (alias for list-content)')
    ls_parser.add_argument('--workspace', help='Filter by workspace')
    ls_parser.add_argument('--database', help='Filter by database name')
    ls_parser.add_argument('--limit', type=int, default=10, help='Limit number of results')
    ls_parser.set_defaults(func=handle_migration_list_content)
    
    # cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up orphaned registry entries')
    cleanup_parser.set_defaults(func=handle_migration_cleanup)

    # file-rename command
    file_rename_parser = subparsers.add_parser('file-rename', help='Rename markdown files to use date prefixes')
    file_rename_parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no actual renaming)')
    file_rename_parser.add_argument('--database', help='Migrate files for specific database only')
    file_rename_parser.add_argument('--workspace', help='Migrate files for specific workspace only')
    file_rename_parser.set_defaults(func=handle_file_rename_migration) 