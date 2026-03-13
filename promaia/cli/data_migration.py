"""
Data migration utility for Maia's new directory structure.

Migrates from old structure:
- data/md/notion/{workspace}/{database}/
- data/md/gmail/{username}/

To new structure:
- data/{app}/{workspace}/
"""
import os
import shutil
import logging
from typing import Dict, List, Tuple
from pathlib import Path

from promaia.utils.env_writer import get_data_subdir

logger = logging.getLogger(__name__)

class DataMigrator:
    """Handles migration from old to new data directory structure."""
    
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.migrations = []
        self.errors = []
        
    def analyze_current_structure(self) -> Dict[str, List[str]]:
        """Analyze the current data structure and identify migration needs."""
        analysis = {
            "old_notion_dirs": [],
            "old_gmail_dirs": [],
            "new_structure_dirs": [],
            "needs_migration": False
        }
        
        data_dir = get_data_subdir()
        if not data_dir.exists():
            return analysis
        
        # Check for old structure
        old_md_dir = data_dir / "md"
        if old_md_dir.exists():
            # Check for old Notion structure: data/md/notion/
            old_notion_dir = old_md_dir / "notion"
            if old_notion_dir.exists():
                for workspace_dir in old_notion_dir.iterdir():
                    if workspace_dir.is_dir():
                        for db_dir in workspace_dir.iterdir():
                            if db_dir.is_dir():
                                analysis["old_notion_dirs"].append(str(db_dir))
                                analysis["needs_migration"] = True
            
            # Check for old Gmail structure: data/md/gmail/
            old_gmail_dir = old_md_dir / "gmail"
            if old_gmail_dir.exists():
                for user_dir in old_gmail_dir.iterdir():
                    if user_dir.is_dir():
                        analysis["old_gmail_dirs"].append(str(user_dir))
                        analysis["needs_migration"] = True
        
        # Check for new structure
        for potential_app in ["notion", "gmail", "discord"]:
            app_dir = data_dir / potential_app
            if app_dir.exists():
                analysis["new_structure_dirs"].append(str(app_dir))
        
        return analysis
    
    def plan_migration(self) -> List[Dict[str, str]]:
        """Plan the migration steps."""
        analysis = self.analyze_current_structure()
        migration_plan = []
        
        # Plan Notion migrations: data/md/notion/{workspace}/{database}/ -> data/notion/{workspace}/
        for old_notion_path in analysis["old_notion_dirs"]:
            old_path = Path(old_notion_path)
            # Extract workspace and database from path
            parts = old_path.parts
            if len(parts) >= 4 and parts[-3] == "notion":
                workspace = parts[-2]
                database = parts[-1]
                
                new_path = get_data_subdir() / "notion" / workspace / database
                
                migration_plan.append({
                    "type": "notion",
                    "source": str(old_path),
                    "target": str(new_path),
                    "workspace": workspace,
                    "database": database
                })
        
        # Plan Gmail migrations: data/md/gmail/{username}/ -> data/gmail/{workspace}/
        for old_gmail_path in analysis["old_gmail_dirs"]:
            old_path = Path(old_gmail_path)
            # Extract username from path
            parts = old_path.parts
            if len(parts) >= 4 and parts[-2] == "gmail":
                username = parts[-1]
                # For Gmail, we'll use 'koii' as default workspace since we can't determine it from path
                workspace = "koii"  # Default workspace for Gmail migration
                
                new_path = get_data_subdir() / "gmail" / workspace
                
                migration_plan.append({
                    "type": "gmail",
                    "source": str(old_path),
                    "target": str(new_path),
                    "workspace": workspace,
                    "username": username
                })
        
        return migration_plan
    
    def execute_migration(self, migration_plan: List[Dict[str, str]]) -> Dict[str, int]:
        """Execute the migration plan."""
        results = {
            "migrated": 0,
            "skipped": 0,
            "errors": 0
        }
        
        for migration in migration_plan:
            try:
                source_path = Path(migration["source"])
                target_path = Path(migration["target"])
                
                if not source_path.exists():
                    logger.warning(f"Source path no longer exists: {source_path}")
                    results["skipped"] += 1
                    continue
                
                if target_path.exists():
                    logger.warning(f"Target path already exists: {target_path}")
                    results["skipped"] += 1
                    continue
                
                if self.dry_run:
                    logger.info(f"DRY RUN: Would migrate {source_path} -> {target_path}")
                    results["migrated"] += 1
                else:
                    # Create target directory structure
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Move the directory
                    shutil.move(str(source_path), str(target_path))
                    logger.info(f"Migrated {source_path} -> {target_path}")
                    results["migrated"] += 1
                    
            except Exception as e:
                error_msg = f"Error migrating {migration['source']}: {e}"
                logger.error(error_msg)
                self.errors.append(error_msg)
                results["errors"] += 1
        
        return results
    
    def cleanup_old_structure(self) -> Dict[str, int]:
        """Clean up empty directories from old structure."""
        cleanup_results = {
            "removed_dirs": 0,
            "errors": 0
        }
        
        data_dir = get_data_subdir()
        old_md_dir = data_dir / "md"
        
        if not old_md_dir.exists():
            return cleanup_results
        
        try:
            # Remove empty directories in reverse order (deepest first)
            for root, dirs, files in os.walk(str(old_md_dir), topdown=False):
                root_path = Path(root)
                
                # Only remove if directory is empty
                if not any(root_path.iterdir()):
                    if self.dry_run:
                        logger.info(f"DRY RUN: Would remove empty directory {root_path}")
                        cleanup_results["removed_dirs"] += 1
                    else:
                        root_path.rmdir()
                        logger.info(f"Removed empty directory {root_path}")
                        cleanup_results["removed_dirs"] += 1
                        
        except Exception as e:
            error_msg = f"Error during cleanup: {e}"
            logger.error(error_msg)
            self.errors.append(error_msg)
            cleanup_results["errors"] += 1
        
        return cleanup_results
    
    def migrate(self) -> Dict[str, any]:
        """Perform complete migration."""
        logger.info(f"Starting data migration (dry_run={self.dry_run})")
        
        # Analyze current structure
        analysis = self.analyze_current_structure()
        
        if not analysis["needs_migration"]:
            logger.info("No migration needed - data already uses new structure")
            return {
                "needed": False,
                "analysis": analysis,
                "migration_results": {},
                "cleanup_results": {},
                "errors": self.errors
            }
        
        # Plan migration
        migration_plan = self.plan_migration()
        logger.info(f"Planned {len(migration_plan)} migrations")
        
        # Execute migration
        migration_results = self.execute_migration(migration_plan)
        
        # Cleanup old structure (only if not dry run and no errors)
        cleanup_results = {}
        if not self.dry_run and migration_results["errors"] == 0:
            cleanup_results = self.cleanup_old_structure()
        
        return {
            "needed": True,
            "analysis": analysis,
            "migration_plan": migration_plan,
            "migration_results": migration_results,
            "cleanup_results": cleanup_results,
            "errors": self.errors
        }

def migrate_data_structure(dry_run: bool = True) -> Dict[str, any]:
    """Convenience function to migrate data structure."""
    migrator = DataMigrator(dry_run=dry_run)
    return migrator.migrate() 