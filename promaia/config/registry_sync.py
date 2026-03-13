"""
Config-driven registry synchronization for Maia.

This module provides automatic synchronization between the configuration file
and the hybrid metadata database registry, ensuring the registry is always the 
authoritative source for file locations.
"""
import os
import json
import hashlib
import logging
from typing import Dict, Any, Optional, Set, List
from datetime import datetime
from pathlib import Path

from promaia.config.databases import get_database_manager, DatabaseConfig
from promaia.storage.hybrid_storage import get_hybrid_registry

logger = logging.getLogger(__name__)

class ConfigRegistrySync:
    """Manages synchronization between config file and hybrid metadata registry."""
    
    def __init__(self, config_file: str = None):
        if config_file is None:
            from promaia.utils.env_writer import get_config_path
            config_file = str(get_config_path())
        self.config_file = config_file
        self.db_manager = get_database_manager()
        self.registry = get_hybrid_registry()
        self._last_config_hash = None
        
    def get_config_hash(self) -> Optional[str]:
        """Get MD5 hash of current config file."""
        if not os.path.exists(self.config_file):
            return None
            
        try:
            with open(self.config_file, 'r') as f:
                content = f.read()
            return hashlib.md5(content.encode()).hexdigest()
        except Exception as e:
            logger.error(f"Error reading config file for hashing: {e}")
            return None
    
    def has_config_changed(self) -> bool:
        """Check if config file has changed since last check."""
        current_hash = self.get_config_hash()
        if current_hash != self._last_config_hash:
            self._last_config_hash = current_hash
            return True
        return False
    
    def validate_registry_sync(self) -> Dict[str, Any]:
        """
        Validate that hybrid registry is in sync with config.
        
        Returns:
            Dict with validation results and recommended actions
        """
        results = {
            'in_sync': True,
            'issues': [],
            'recommendations': [],
            'databases_checked': 0,
            'missing_registrations': [],
            'orphaned_entries': []
        }
        
        try:
            # Check each database in config
            for db_name, db_config in self.db_manager.databases.items():
                results['databases_checked'] += 1
                
                # Skip databases that don't save markdown
                if not getattr(db_config, 'save_markdown', True):
                    continue
                
                # Check if markdown directory exists
                md_dir = db_config.markdown_directory
                if not os.path.exists(md_dir):
                    continue
                
                # Get registry entries for this database from hybrid storage
                registry_entries = self.registry.query_content(
                    workspace=db_config.workspace,
                    database_name=db_config.nickname
                )
                registry_page_ids = {entry['page_id'] for entry in registry_entries}
                
                # Find markdown files in directory
                import glob
                md_files = glob.glob(os.path.join(md_dir, "*.md"))
                file_page_ids = set()
                
                for md_file in md_files:
                    # Extract page ID from filename (handle both UUID and Gmail thread ID formats)
                    import re
                    page_id_match = re.search(r'([a-f0-9-]{36}|thread_[a-f0-9]{16})\.md$', os.path.basename(md_file))
                    if page_id_match:
                        file_page_ids.add(page_id_match.group(1))
                
                # Find missing registrations (files not in registry)
                missing = file_page_ids - registry_page_ids
                if missing:
                    results['in_sync'] = False
                    results['missing_registrations'].extend([
                        {
                            'database': f"{db_config.workspace}.{db_config.nickname}",
                            'page_ids': list(missing),
                            'count': len(missing)
                        }
                    ])
                    results['issues'].append(
                        f"{len(missing)} markdown files not registered for {db_config.workspace}.{db_config.nickname}"
                    )
                
                # Find orphaned entries (registry entries without files)
                orphaned = registry_page_ids - file_page_ids
                if orphaned:
                    results['orphaned_entries'].extend([
                        {
                            'database': f"{db_config.workspace}.{db_config.nickname}",
                            'page_ids': list(orphaned),
                            'count': len(orphaned)
                        }
                    ])
                    results['issues'].append(
                        f"{len(orphaned)} registry entries without files for {db_config.workspace}.{db_config.nickname}"
                    )
            
            # Generate recommendations
            if results['missing_registrations']:
                results['recommendations'].append(
                    "Run 'maia database register-markdown-files' to register missing files"
                )
            
            if results['orphaned_entries']:
                results['recommendations'].append(
                    "Consider cleaning up orphaned registry entries"
                )
                
        except Exception as e:
            logger.error(f"Error during registry validation: {e}")
            results['issues'].append(f"Validation error: {e}")
            results['in_sync'] = False
        
        return results
    
    def auto_register_missing_files(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Automatically register missing markdown files in hybrid storage.
        
        Args:
            dry_run: If True, only report what would be done
            
        Returns:
            Dict with registration results
        """
        results = {
            'success': True,
            'registered_count': 0,
            'errors': [],
            'databases_processed': []
        }
        
        try:
            validation = self.validate_registry_sync()
            
            for missing_info in validation['missing_registrations']:
                db_name = missing_info['database']
                page_ids = missing_info['page_ids']
                
                # Get database config
                workspace, nickname = db_name.split('.', 1)
                db_config = self.db_manager.get_database(nickname, workspace)
                
                if not db_config:
                    results['errors'].append(f"Database config not found for {db_name}")
                    continue
                
                results['databases_processed'].append(db_name)
                registered_in_db = 0
                
                # Register each missing file in hybrid storage
                for page_id in page_ids:
                    try:
                        # Find the markdown file
                        import glob
                        md_files = glob.glob(os.path.join(db_config.markdown_directory, f"*{page_id}*.md"))
                        
                        if not md_files:
                            results['errors'].append(f"File not found for page_id {page_id}")
                            continue
                        
                        md_file = md_files[0]
                        filename = os.path.basename(md_file)
                        
                        if dry_run:
                            logger.info(f"Would register: {filename}")
                            registered_in_db += 1
                            continue
                        
                        # Extract metadata from filename
                        import re
                        title_match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(.+?)\s+(?:[a-f0-9-]{36}|thread_[a-f0-9]{16})\.md$', filename)
                        if title_match:
                            date_str = title_match.group(1)
                            title = title_match.group(2)
                            created_time = f"{date_str}T00:00:00Z"
                        else:
                            title = filename.replace('.md', '').replace(page_id, '').strip()
                            file_mtime = datetime.fromtimestamp(os.path.getmtime(md_file))
                            created_time = file_mtime.isoformat() + "Z"
                        
                        # Prepare content data for hybrid storage
                        content_data = {
                            'page_id': page_id,
                            'workspace': db_config.workspace,
                            'database_name': db_config.nickname,
                            'file_path': md_file,
                            'title': title,
                            'created_time': created_time,
                            'last_edited_time': created_time,
                            'synced_time': datetime.now().isoformat(),
                            'file_size': os.path.getsize(md_file) if os.path.exists(md_file) else 0,
                            'metadata': {'source': 'auto_registration'}
                        }
                        
                        # Register in hybrid storage
                        success = self.registry.add_content(content_data)
                        
                        if success:
                            registered_in_db += 1
                        else:
                            results['errors'].append(f"Failed to register {page_id}")
                            
                    except Exception as e:
                        results['errors'].append(f"Error registering {page_id}: {e}")
                
                logger.info(f"{'Would register' if dry_run else 'Registered'} {registered_in_db} files for {db_name}")
                results['registered_count'] += registered_in_db
            
        except Exception as e:
            logger.error(f"Error during auto-registration: {e}")
            results['success'] = False
            results['errors'].append(str(e))
        
        return results
    
    def startup_validation(self, auto_fix: bool = True) -> bool:
        """
        Perform startup validation and optionally auto-fix issues.
        
        Args:
            auto_fix: If True, automatically register missing files
            
        Returns:
            True if registry is in sync or was successfully fixed
        """
        logger.info("Performing startup registry validation...")
        
        validation = self.validate_registry_sync()
        
        if validation['in_sync']:
            logger.info("✓ Registry is in sync with configuration")
            return True
        
        logger.warning("⚠ Registry is not in sync with configuration:")
        for issue in validation['issues']:
            logger.warning(f"  - {issue}")
        
        if auto_fix and validation['missing_registrations']:
            logger.info("Attempting to auto-fix missing registrations...")
            fix_results = self.auto_register_missing_files(dry_run=False)
            
            if fix_results['success'] and fix_results['registered_count'] > 0:
                logger.info(f"✓ Auto-registered {fix_results['registered_count']} missing files")
                return True
            else:
                logger.error("✗ Auto-fix failed")
                for error in fix_results['errors']:
                    logger.error(f"  - {error}")
        
        # Provide recommendations
        logger.info("Recommendations:")
        for rec in validation['recommendations']:
            logger.info(f"  - {rec}")
        
        return False

# Global sync manager instance
_sync_manager = None

def get_config_registry_sync(config_file: str = None) -> ConfigRegistrySync:
    """Get the global config-registry sync manager instance."""
    global _sync_manager
    if _sync_manager is None:
        _sync_manager = ConfigRegistrySync(config_file)
    return _sync_manager

def validate_startup_registry(auto_fix: bool = True) -> bool:
    """Validate registry at startup and optionally auto-fix."""
    sync_manager = get_config_registry_sync()
    return sync_manager.startup_validation(auto_fix=auto_fix)

def check_config_changes_and_sync() -> bool:
    """Check for config changes and sync registry if needed."""
    sync_manager = get_config_registry_sync()
    if sync_manager.has_config_changed():
        logger.info("Configuration file changed, validating registry...")
        return sync_manager.startup_validation(auto_fix=True)
    return True 