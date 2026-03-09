#!/usr/bin/env python3
"""
Clean up orphaned database entries where markdown files no longer exist.
"""
import sqlite3
import os
from pathlib import Path

def cleanup_orphaned_entries(db_path="data/hybrid_metadata.db", dry_run=True):
    """Remove database entries where markdown files don't exist."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all entries with file paths
    # ONLY check Notion content - exclude everything else (gmail, discord, slack, etc.)
    # Notion content has content_type starting with 'notion' or in generic_content
    cursor.execute("""
        SELECT page_id, title, file_path, database_name, workspace, content_type
        FROM unified_content
        WHERE file_path IS NOT NULL
        AND file_path LIKE '%/notion/%'
        AND (content_type LIKE 'notion%' OR content_type = 'generic_content')
    """)

    entries = cursor.fetchall()
    orphaned = []

    print(f"Checking {len(entries)} Notion entries for missing markdown files...")
    print("(Excluding Gmail, Discord, Slack - those are stored in database only)")

    for page_id, title, file_path, db_name, workspace, content_type in entries:
        # Check if file exists
        if file_path:
            full_path = file_path if os.path.isabs(file_path) else os.path.join(os.getcwd(), file_path)
            if not os.path.exists(full_path):
                orphaned.append((page_id, title, file_path, db_name, workspace))

    print(f"\nFound {len(orphaned)} orphaned entries (database entry exists but markdown file missing)")

    if orphaned:
        print("\nOrphaned entries:")
        for page_id, title, file_path, db_name, workspace in orphaned:
            print(f"  • {workspace}.{db_name}: {title or page_id[:8]}")
            print(f"    Missing file: {file_path}")

    if not dry_run and orphaned:
        print(f"\n🗑️  Deleting {len(orphaned)} orphaned entries from database...")

        from promaia.storage.hybrid_storage import get_hybrid_registry
        registry = get_hybrid_registry(db_path)

        for page_id, title, _, db_name, workspace in orphaned:
            try:
                registry.delete_page(page_id)
                print(f"  ✅ Deleted: {workspace}.{db_name}: {title or page_id[:8]}")
            except Exception as e:
                print(f"  ❌ Failed to delete {page_id}: {e}")

        print(f"\n✨ Cleanup complete! Removed {len(orphaned)} orphaned entries.")
    elif dry_run:
        print("\n💡 This was a DRY RUN. To actually delete these entries, run:")
        print("   python scripts/cleanup_orphaned_entries.py --no-dry-run")

    conn.close()
    return len(orphaned)

if __name__ == "__main__":
    import sys
    dry_run = "--no-dry-run" not in sys.argv

    print("🧹 Orphaned Database Entry Cleanup")
    print("=" * 50)
    if dry_run:
        print("🔍 Running in DRY RUN mode (no changes will be made)")
    else:
        print("⚠️  Running in LIVE mode (entries will be deleted)")
    print("=" * 50)
    print()

    count = cleanup_orphaned_entries(dry_run=dry_run)

    if count == 0:
        print("✅ No orphaned entries found! Database is clean.")
