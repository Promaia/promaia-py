"""
Database registry synchronization utilities.

This module provides functionality to update registry entries when database
configurations change, particularly for Discord databases where nicknames
might be updated.
"""
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from promaia.config.databases import DatabaseConfig, get_database_manager
from promaia.storage.hybrid_storage import get_hybrid_registry

logger = logging.getLogger(__name__)

class DatabaseRegistrySync:
    """Manages synchronization between database configs and registry entries."""
    
    def __init__(self):
        self.db_manager = get_database_manager()
        self.hybrid_registry = get_hybrid_registry()
    
    def find_orphaned_registry_entries(self) -> Dict[str, List[Dict[str, Any]]]:
        """Find registry entries that don't match any current database configuration."""
        orphaned_entries = {}
        
        try:
            import sqlite3
            
            with sqlite3.connect(self.hybrid_registry.db_path) as conn:
                cursor = conn.cursor()
                
                # Get all unique database names from the unified view
                query = "SELECT DISTINCT database_name FROM unified_content"
                cursor.execute(query)
                registry_db_names = [row[0] for row in cursor.fetchall()]
                
                # Check each registry database name against current configs
                for registry_db_name in registry_db_names:
                    if not self.db_manager.get_database_by_qualified_name(registry_db_name):
                        # This database name doesn't exist in current config
                        # Get the entries for this database
                        entry_query = """
                            SELECT page_id, database_name, file_path, title, created_time
                            FROM unified_content 
                            WHERE database_name = ?
                            LIMIT 10
                        """
                        cursor.execute(entry_query, (registry_db_name,))
                        entries = cursor.fetchall()
                        
                        if entries:
                            orphaned_entries[registry_db_name] = [
                                {
                                    "page_id": entry[0],
                                    "database_name": entry[1], 
                                    "file_path": entry[2],
                                    "title": entry[3],
                                    "created_time": entry[4]
                                }
                                for entry in entries
                            ]
        
        except Exception as e:
            logger.error(f"Error finding orphaned registry entries: {e}")
        
        return orphaned_entries
    
    def suggest_database_mappings(self, orphaned_entries: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
        """Suggest mappings from orphaned database names to current database configs."""
        suggestions = {}
        
        for orphaned_db_name in orphaned_entries.keys():
            # Try to find a matching current database
            
            # 1. Check if it's a Discord database by looking for server ID patterns
            possible_discord_match = None
            if orphaned_db_name.startswith(('trass.', 'koii.')):
                workspace, old_name = orphaned_db_name.split('.', 1)
                
                # Look for Discord databases in the same workspace
                for db_config in self.db_manager.databases.values():
                    if (db_config.source_type == "discord" and 
                        db_config.workspace == workspace):
                        possible_discord_match = db_config.get_qualified_name()
                        break
            
            # 2. Check for similar names
            for db_config in self.db_manager.databases.values():
                current_name = db_config.get_qualified_name()
                
                # Check for partial matches
                if (orphaned_db_name.endswith(db_config.nickname) or
                    current_name.endswith(orphaned_db_name.split('.')[-1])):
                    suggestions[orphaned_db_name] = current_name
                    break
            
            # 3. Use Discord match if found and no other suggestion
            if orphaned_db_name not in suggestions and possible_discord_match:
                suggestions[orphaned_db_name] = possible_discord_match
        
        return suggestions
    
    def update_registry_database_name(self, old_name: str, new_name: str) -> Dict[str, Any]:
        """Update registry entries to use a new database name."""
        result = {
            "old_name": old_name,
            "new_name": new_name,
            "entries_updated": 0,
            "success": False,
            "error": None
        }
        
        try:
            import sqlite3
            
            with sqlite3.connect(self.hybrid_registry.db_path) as conn:
                cursor = conn.cursor()
                
                # The hybrid registry uses separate tables, so we need to update each one
                # that might contain the old database name
                tables_to_update = [
                    'notion_journal', 'notion_stories', 'notion_cms', 'generic_content'
                ]
                
                total_updated = 0
                
                for table in tables_to_update:
                    # Check if table exists and has database_name column
                    check_query = f"""
                        SELECT COUNT(*) FROM {table} WHERE database_name = ?
                    """
                    try:
                        cursor.execute(check_query, (old_name,))
                        count_result = cursor.fetchone()
                        count = count_result[0] if count_result else 0
                        
                        if count > 0:
                            # Update entries in this table
                            update_query = f"""
                                UPDATE {table} 
                                SET database_name = ? 
                                WHERE database_name = ?
                            """
                            cursor.execute(update_query, (new_name, old_name))
                            updated_count = cursor.rowcount
                            total_updated += updated_count
                            
                            if updated_count > 0:
                                logger.info(f"Updated {updated_count} entries in {table}")
                    
                    except sqlite3.OperationalError:
                        # Table might not exist or have database_name column, skip
                        continue
                
                if total_updated == 0:
                    result["error"] = f"No registry entries found for database '{old_name}'"
                    return result
                
                conn.commit()
                result["entries_updated"] = total_updated
                result["success"] = True
                logger.info(f"Updated {total_updated} total registry entries from '{old_name}' to '{new_name}'")
            
        except Exception as e:
            error_msg = f"Failed to update registry entries: {e}"
            result["error"] = error_msg
            logger.error(error_msg)
        
        return result
    
    def sync_all_suggested_mappings(self, mappings: Dict[str, str], dry_run: bool = True) -> Dict[str, Any]:
        """Apply all suggested database name mappings to the registry."""
        results = {
            "total_mappings": len(mappings),
            "successful_updates": 0,
            "failed_updates": 0,
            "total_entries_updated": 0,
            "dry_run": dry_run,
            "update_results": [],
            "errors": []
        }
        
        for old_name, new_name in mappings.items():
            if dry_run:
                # Just count what would be updated
                try:
                    import sqlite3
                    
                    with sqlite3.connect(self.hybrid_registry.db_path) as conn:
                        cursor = conn.cursor()
                        count_query = "SELECT COUNT(*) FROM unified_content WHERE database_name = ?"
                        cursor.execute(count_query, (old_name,))
                        count_result = cursor.fetchone()
                        entry_count = count_result[0] if count_result else 0
                    
                    results["update_results"].append({
                        "old_name": old_name,
                        "new_name": new_name,
                        "entries_would_update": entry_count,
                        "success": True
                    })
                    
                    if entry_count > 0:
                        results["successful_updates"] += 1
                        results["total_entries_updated"] += entry_count
                
                except Exception as e:
                    results["failed_updates"] += 1
                    results["errors"].append(f"Failed to count entries for {old_name}: {e}")
            
            else:
                # Actually perform the update
                update_result = self.update_registry_database_name(old_name, new_name)
                results["update_results"].append(update_result)
                
                if update_result["success"]:
                    results["successful_updates"] += 1
                    results["total_entries_updated"] += update_result["entries_updated"]
                else:
                    results["failed_updates"] += 1
                    if update_result["error"]:
                        results["errors"].append(update_result["error"])
        
        return results


def get_database_registry_sync() -> DatabaseRegistrySync:
    """Get a database registry sync instance."""
    return DatabaseRegistrySync() 