#!/usr/bin/env python3
"""
Migration script to convert Gmail dates from email format to ISO format.

This script:
1. Finds all gmail_content entries with non-ISO dates
2. Parses the email-formatted dates
3. Updates them to ISO format
4. Updates the unified_content view accordingly

Run with: python migrate_dates_to_iso.py [--dry-run]
"""

import sqlite3
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def is_iso_format(date_str):
    """Check if a date string is in ISO format."""
    if not date_str:
        return False
    try:
        # ISO format should parse with fromisoformat
        datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return True
    except (ValueError, AttributeError):
        return False


def parse_email_date(date_str):
    """Parse email-formatted date to ISO format."""
    try:
        date_obj = parsedate_to_datetime(date_str)
        if date_obj.tzinfo is None:
            date_obj = date_obj.replace(tzinfo=timezone.utc)
        return date_obj.isoformat()
    except Exception as e:
        print(f"  ⚠️  Failed to parse date '{date_str}': {e}")
        return None


def migrate_dates(db_path='data/hybrid_metadata.db', dry_run=False):
    """Migrate email-formatted dates to ISO format."""
    
    print(f"\n{'🔍 DRY RUN MODE' if dry_run else '🔄 MIGRATION MODE'}")
    print(f"Database: {db_path}\n")
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # Get all Gmail content with dates
        cursor.execute("""
            SELECT page_id, created_time, last_edited_time, email_date
            FROM gmail_content
        """)
        
        all_rows = cursor.fetchall()
        print(f"📊 Total Gmail entries: {len(all_rows)}")
        
        # Find non-ISO dates
        to_migrate = []
        for page_id, created_time, last_edited_time, email_date in all_rows:
            needs_update = False
            new_created = created_time
            new_edited = last_edited_time
            new_email = email_date
            
            if created_time and not is_iso_format(created_time):
                new_created = parse_email_date(created_time)
                needs_update = True
            
            if last_edited_time and not is_iso_format(last_edited_time):
                new_edited = parse_email_date(last_edited_time)
                needs_update = True
            
            if email_date and not is_iso_format(email_date):
                new_email = parse_email_date(email_date)
                needs_update = True
            
            if needs_update:
                to_migrate.append((
                    page_id,
                    created_time, new_created,
                    last_edited_time, new_edited,
                    email_date, new_email
                ))
        
        print(f"🔍 Entries needing migration: {len(to_migrate)}\n")
        
        if not to_migrate:
            print("✅ All dates are already in ISO format!")
            return
        
        # Show examples
        print("📝 Example conversions (first 3):")
        for i, (page_id, old_created, new_created, old_edited, new_edited, old_email, new_email) in enumerate(to_migrate[:3]):
            print(f"\n  Entry {i+1} ({page_id}):")
            if old_created != new_created:
                print(f"    created_time:")
                print(f"      FROM: {old_created}")
                print(f"      TO:   {new_created}")
            if old_email != new_email:
                print(f"    email_date:")
                print(f"      FROM: {old_email}")
                print(f"      TO:   {new_email}")
        
        if dry_run:
            print(f"\n🔍 DRY RUN: Would update {len(to_migrate)} entries")
            print("   Run without --dry-run to apply changes")
            return
        
        # Confirm migration
        print(f"\n⚠️  About to update {len(to_migrate)} entries")
        response = input("Continue? [y/N]: ").strip().lower()
        
        if response != 'y':
            print("❌ Migration cancelled")
            return
        
        # Perform migration
        print("\n🔄 Migrating dates...")
        updated_count = 0
        failed_count = 0
        
        for page_id, old_created, new_created, old_edited, new_edited, old_email, new_email in to_migrate:
            try:
                cursor.execute("""
                    UPDATE gmail_content
                    SET created_time = ?,
                        last_edited_time = ?,
                        email_date = ?
                    WHERE page_id = ?
                """, (new_created, new_edited, new_email, page_id))
                updated_count += 1
                
                if updated_count % 100 == 0:
                    print(f"  ✓ Processed {updated_count}/{len(to_migrate)}...")
            
            except Exception as e:
                print(f"  ❌ Failed to update {page_id}: {e}")
                failed_count += 1
        
        conn.commit()
        
        print(f"\n✅ Migration complete!")
        print(f"   Updated: {updated_count}")
        print(f"   Failed: {failed_count}")
        
        # Verify
        cursor.execute("""
            SELECT COUNT(*) FROM gmail_content
            WHERE created_time NOT LIKE '____-__-__%'
               OR email_date NOT LIKE '____-__-__%'
        """)
        remaining = cursor.fetchone()[0]
        
        if remaining > 0:
            print(f"\n⚠️  Warning: {remaining} entries still have non-ISO dates")
        else:
            print("\n✅ All Gmail dates are now in ISO format!")


if __name__ == "__main__":
    dry_run = '--dry-run' in sys.argv
    db_path = 'data/hybrid_metadata.db'
    
    # Check if custom db path provided
    for arg in sys.argv[1:]:
        if not arg.startswith('--'):
            db_path = arg
            break
    
    try:
        migrate_dates(db_path, dry_run)
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

