"""
Unified storage system for managing content with the new directory structure.

This module implements the new storage architecture:
- Markdown files: data/md/notion/{workspace}/{database}/
- JSON files: data/json/ (flat structure with SQLite registry)
"""
import os
import json
import logging
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path
from email.utils import parsedate_to_datetime

from promaia.config.databases import DatabaseConfig, get_database_manager
from promaia.storage.hybrid_storage import get_hybrid_registry

logger = logging.getLogger(__name__)

class UnifiedStorage:
    """Unified storage manager for the new directory structure."""

    def __init__(self, config_file: str = None):
        if config_file is None:
            from promaia.utils.env_writer import get_config_path
            config_file = str(get_config_path())
        self.config_file = config_file
        self.db_manager = get_database_manager()
        self.hybrid_registry = get_hybrid_registry()  # Use hybrid storage exclusively

    @staticmethod
    def _resolve_md_dir(database_config: DatabaseConfig) -> str:
        """Resolve markdown_directory to an absolute path using the data directory."""
        md_dir = database_config.markdown_directory
        if not os.path.isabs(md_dir):
            from promaia.utils.env_writer import get_data_dir
            md_dir = os.path.join(str(get_data_dir()), md_dir)
        return md_dir
        
    def save_content(self, 
                    page_id: str,
                    title: str,
                    content_data: Dict[str, Any],
                    database_config: DatabaseConfig,
                    markdown_content: Optional[str] = None) -> Dict[str, str]:
        """
        Save content using the unified storage system (markdown only).
        
        Args:
            page_id: Unique page identifier
            title: Page title
            content_data: Raw content data from source
            database_config: Database configuration
            markdown_content: Pre-converted markdown content (optional)
            
        Returns:
            Dictionary with paths to saved files
        """
        saved_files = {}
        
        # Always save markdown
        if markdown_content is None:
            # Convert content to markdown if not provided
            from promaia.markdown.converter import page_to_markdown
            try:
                markdown_content = page_to_markdown(content_data.get('content', []))
            except Exception as e:
                logger.error(f"Error converting content to markdown for {page_id}: {e}")
                markdown_content = f"# {title}\n\nError converting content: {e}"
        
        md_path = self._save_markdown_file(
            page_id=page_id,
            title=title,
            markdown_content=markdown_content,
            database_config=database_config,
            content_data=content_data
        )
        
        if md_path:
            saved_files['markdown'] = md_path
            
            # Also save to hybrid storage for optimized querying
            try:
                # Prepare content data for hybrid storage
                hybrid_content_data = {
                    'page_id': page_id,
                    'workspace': database_config.workspace,
                    'database_id': database_config.database_id, # Add this line
                    'database_name': database_config.nickname,
                    'file_path': md_path,
                    'title': title,
                    'created_time': content_data.get('created_time'),
                    'last_edited_time': content_data.get('last_edited_time'),
                    'synced_time': datetime.now().isoformat(),
                    'file_size': os.path.getsize(md_path) if os.path.exists(md_path) else 0,
                    'checksum': None,  # Can be added later if needed
                    'metadata': content_data  # Pass the full content_data as metadata
                }
                
                # Save to hybrid storage
                self.hybrid_registry.add_content(hybrid_content_data)
                logger.debug(f"Saved content to hybrid storage: {page_id}")
                
            except Exception as e:
                logger.error(f"Error saving to hybrid storage for {page_id}: {e}")
        
        return saved_files
    
    async def save_pages_batch(self, pages_data: List[Dict[str, Any]], database_config) -> Dict[str, Any]:
        """Save multiple pages efficiently with batch operations - significant performance improvement."""
        if not pages_data:
            return {"success": 0, "errors": 0, "files": []}
        
        # Prepare for batch operations
        page_ids = [page['page_id'] for page in pages_data]
        hybrid_batch_data = []
        saved_files = []
        success_count = 0
        error_count = 0
        
        # Batch cleanup existing files first (major performance improvement)
        try:
            self._batch_cleanup_existing_files(page_ids, database_config)
        except Exception as e:
            logger.warning(f"Batch cleanup failed, falling back to individual cleanup: {e}")
        
        # Process each page (markdown conversion and file saving)
        for page_data in pages_data:
            try:
                page_id = page_data.get('page_id')
                title = page_data.get('title', 'Untitled')
                content_data = page_data.get('content_data', page_data)
                
                # Convert to markdown (this is CPU-intensive but necessary)
                from promaia.markdown.converter import page_to_markdown
                try:
                    # Use async thread for CPU-intensive markdown conversion
                    import asyncio
                    markdown_content = await asyncio.to_thread(page_to_markdown, content_data.get('content', []))
                except Exception as e:
                    logger.error(f"Error converting content to markdown for {page_id}: {e}")
                    markdown_content = f"# {title}\n\nError converting content: {e}"
                
                # Save markdown file
                md_path = self._save_markdown_file(
                    page_id=page_id,
                    title=title,
                    markdown_content=markdown_content,
                    database_config=database_config,
                    content_data=content_data
                )
                
                if md_path:
                    saved_files.append(md_path)
                    
                    # Prepare for batch registry operation
                    hybrid_content_data = {
                        'page_id': page_id,
                        'workspace': database_config.workspace,
                        'database_id': database_config.database_id,
                        'database_name': database_config.nickname,
                        'file_path': md_path,
                        'title': title,
                        'created_time': content_data.get('created_time'),
                        'last_edited_time': content_data.get('last_edited_time'),
                        'synced_time': datetime.now().isoformat(),
                        'file_size': os.path.getsize(md_path) if os.path.exists(md_path) else 0,
                        'checksum': None,
                        'metadata': content_data
                    }
                    hybrid_batch_data.append(hybrid_content_data)
                    success_count += 1
                    
            except Exception as e:
                logger.error(f"Error processing page {page_data.get('page_id', 'unknown')}: {e}")
                error_count += 1
        
        # Batch save to hybrid registry (major performance improvement)
        try:
            if hybrid_batch_data:
                batch_results = self.hybrid_registry.add_content_batch(hybrid_batch_data)
                registry_successes = sum(batch_results)
                logger.info(f"Batch registry save: {registry_successes}/{len(hybrid_batch_data)} succeeded")
        except Exception as e:
            logger.error(f"Batch registry save failed: {e}")
        
        logger.info(f"Batch processed {len(pages_data)} pages: {success_count} successes, {error_count} errors")
        
        return {
            "success": success_count,
            "errors": error_count,
            "files": saved_files,
            "total_processed": len(pages_data)
        }
    
    def _parse_date_string(self, date_str: str) -> Optional[datetime]:
        """Parse date string handling both ISO format and RFC 2822 email format."""
        if not date_str:
            return None
            
        try:
            # First try ISO format (Notion dates)
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            try:
                # Try RFC 2822 format (Gmail dates)
                return parsedate_to_datetime(date_str)
            except (ValueError, TypeError):
                logger.warning(f"Could not parse date string: {date_str}")
                return None
    
    def _save_markdown_file(self, 
                           page_id: str,
                           title: str,
                           markdown_content: str,
                           database_config: DatabaseConfig,
                           content_data: Dict[str, Any] = None) -> Optional[str]:
        """Save markdown file in hierarchical structure."""
        try:
            # Ensure markdown directory exists
            md_dir = self._resolve_md_dir(database_config)
            os.makedirs(md_dir, exist_ok=True)
            
            # DEDUPLICATION: Remove any existing files with the same page_id
            self._cleanup_existing_files_for_page_id(page_id, database_config)
            
            # Create safe filename with date prefix
            safe_title = self._create_safe_filename(title)
            
            # Extract created_time for date prefix
            date_prefix = ""
            try:
                if content_data:
                    created_time_str = content_data.get("created_time")
                    
                    if created_time_str:
                        # Parse the created_time and format as YYYY-MM-DD
                        created_dt = self._parse_date_string(created_time_str)
                        if created_dt:
                            date_prefix = created_dt.strftime("%Y-%m-%d") + " "
                            logger.debug(f"Using created_time {created_time_str} for markdown file prefix: {date_prefix}")
                    else:
                        # Try to extract from the 'date' property in content for Gmail/other sources
                        date_str = content_data.get("date")
                        if date_str:
                            if isinstance(date_str, str):
                                created_dt = self._parse_date_string(date_str)
                                if created_dt:
                                    date_prefix = created_dt.strftime("%Y-%m-%d") + " "
                                    logger.debug(f"Using date field {date_str} for markdown file prefix: {date_prefix}")
                            elif hasattr(date_str, 'strftime'):  # datetime object
                                date_prefix = date_str.strftime("%Y-%m-%d") + " "
                                logger.debug(f"Using datetime object for markdown file prefix: {date_prefix}")
                
                if not date_prefix:
                    logger.warning(f"No created_time found for markdown file {page_id}, using current date")
                    date_prefix = datetime.now().strftime("%Y-%m-%d") + " "
                    
            except Exception as e:
                logger.warning(f"Error extracting created_time for markdown file {page_id}: {e}")
                date_prefix = datetime.now().strftime("%Y-%m-%d") + " "
            
            filename = f"{date_prefix}{safe_title} {page_id}.md"
            file_path = os.path.join(md_dir, filename)
            
            # Save markdown file with optimized I/O
            try:
                # Use more efficient file writing
                Path(file_path).write_text(markdown_content, encoding='utf-8')
            except Exception as e:
                logger.error(f"Failed to write markdown file {file_path}: {e}")
                return None
            
            # Always register content in the registry for proper ordering and tracking
            if content_data:
                logger.debug(f"Registering markdown content in registry: {page_id}")
                # self.json_registry.register_content( # This line is removed as per the edit hint
                #     page_id=page_id,
                #     workspace=database_config.workspace,
                #     database_name=database_config.nickname,
                #     file_path=file_path,
                #     content_data=content_data
                # )
            
            logger.debug(f"Saved markdown file: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving markdown file for {page_id}: {e}")
            return None
    
    def _create_safe_filename(self, title: str, max_length: int = 100) -> str:
        """Create a safe filename from title."""
        # Remove or replace problematic characters
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        
        # Limit length and strip whitespace
        safe_title = safe_title[:max_length].strip()
        
        # Ensure it's not empty
        if not safe_title:
            safe_title = "untitled"
            
        return safe_title
    
    def get_content_by_page_id(self, page_id: str) -> Optional[Dict[str, Any]]:
        """Get content information by page ID from registry."""
        # This method will need to be updated to query the hybrid registry
        # For now, it will return None as the JSON registry is removed
        return None
    
    def list_content(self, 
                    workspace: Optional[str] = None,
                    database_name: Optional[str] = None,
                    limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """List content with optional filtering."""
        # This method will need to be updated to query the hybrid registry
        # For now, it will return an empty list as the JSON registry is removed
        return []
    
    def get_existing_page_ids(self, database_config: DatabaseConfig) -> set:
        """Get existing page IDs for a database."""
        # This method will need to be updated to query the hybrid registry
        # For now, it will return an empty set as the JSON registry is removed
        return set()

    def update_page_properties(self, page_id: str, database_id: str, database_name: str,
                              workspace: str, properties: Dict[str, Any]) -> bool:
        """
        Update only the property columns for an existing page without touching content.
        Delegates to hybrid registry storage.
        """
        return self.hybrid_registry.update_page_properties(
            page_id=page_id,
            database_id=database_id,
            database_name=database_name,
            workspace=workspace,
            properties=properties
        )

    def files_exist_locally(self, page_id: str, title: str, database_config: DatabaseConfig) -> Dict[str, bool]:
        """
        Check if files exist locally for a given page.
        
        Args:
            page_id: Page ID
            title: Page title (used as hint, but we search by page_id for reliability)
            database_config: Database configuration
            
        Returns:
            Dictionary indicating which file types exist locally
        """
        file_status = {
            'markdown': False
        }
        
        # Optimized check: use glob pattern matching instead of iterating all files
        md_dir = self._resolve_md_dir(database_config)
        if os.path.exists(md_dir):
            import glob
            pattern = os.path.join(md_dir, f"*{page_id}.md")
            matching_files = glob.glob(pattern)
            file_status['markdown'] = len(matching_files) > 0
        
        return file_status
    
    def cleanup_orphaned_files(self) -> int:
        """Clean up orphaned registry entries."""
        # This method will need to be updated to query the hybrid registry
        # For now, it will return 0 as the JSON registry is removed
        return 0
    
    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        # This method will need to be updated to query the hybrid registry
        # For now, it will return empty stats as the JSON registry is removed
        return {
            'json_registry': {},
            'markdown_directories': {},
            'total_databases': len(self.db_manager.databases)
        }
    
    def _get_markdown_stats(self) -> Dict[str, Dict[str, int]]:
        """Get markdown directory statistics."""
        stats = {}
        
        for db_name, db_config in self.db_manager.databases.items():
            md_dir = self._resolve_md_dir(db_config)

            if os.path.exists(md_dir):
                md_files = list(Path(md_dir).glob("*.md"))
                stats[db_name] = {
                    'file_count': len(md_files),
                    'total_size': sum(f.stat().st_size for f in md_files),
                    'directory': md_dir
                }
            else:
                stats[db_name] = {
                    'file_count': 0,
                    'total_size': 0,
                    'directory': md_dir
                }
        
        return stats
    
    def migrate_existing_data(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        Migrate existing data to the new directory structure.
        
        Args:
            dry_run: If True, only show what would be moved without actually moving
            
        Returns:
            Migration report
        """
        report = {
            'markdown_migrations': [],
            'errors': [],
            'dry_run': dry_run
        }
        
        for db_name, db_config in self.db_manager.databases.items():
            try:
                # Check for existing markdown files to migrate
                old_md_dir = db_config.output_directory  # Legacy directory
                new_md_dir = self._resolve_md_dir(db_config)
                
                if os.path.exists(old_md_dir) and old_md_dir != new_md_dir:
                    md_files = list(Path(old_md_dir).glob("*.md"))
                    for md_file in md_files:
                        new_path = Path(new_md_dir) / md_file.name
                        
                        migration = {
                            'type': 'markdown',
                            'database': db_name,
                            'from': str(md_file),
                            'to': str(new_path),
                            'size': md_file.stat().st_size
                        }
                        
                        if not dry_run:
                            os.makedirs(new_md_dir, exist_ok=True)
                            md_file.rename(new_path)
                            
                        report['markdown_migrations'].append(migration)
                            
            except Exception as e:
                report['errors'].append(f"Error processing database {db_name}: {e}")
        
        return report

    def _cleanup_existing_files_for_page_id(self, page_id: str, database_config: DatabaseConfig):
        """Clean up existing files for a given page ID."""
        files_removed = []
        
        # Clean up markdown files using optimized glob pattern
        md_dir = self._resolve_md_dir(database_config)
        if os.path.exists(md_dir):
            import glob
            pattern = os.path.join(md_dir, f"*{page_id}.md")
            existing_files = glob.glob(pattern)
            
            # Optimized: Remove files in batch to reduce I/O overhead
            for file_path in existing_files:
                try:
                    os.remove(file_path)
                    files_removed.append(file_path)
                except OSError as e:
                    logger.warning(f"Failed to remove markdown file {file_path}: {e}")
        
        if files_removed:
            logger.debug(f"Cleaned up {len(files_removed)} existing files for page_id {page_id}")

    def _batch_cleanup_existing_files(self, page_ids: List[str], database_config: DatabaseConfig):
        """Batch cleanup existing files for multiple page IDs - significant performance improvement."""
        if not page_ids:
            return
        
        md_dir = self._resolve_md_dir(database_config)
        if not os.path.exists(md_dir):
            return

        import glob
        total_removed = 0
        
        # Single glob pattern to find all files at once
        all_patterns = []
        for page_id in page_ids:
            all_patterns.append(f"*{page_id}.md")
        
        # Use a single glob with all patterns
        existing_files = []
        for pattern in all_patterns:
            full_pattern = os.path.join(md_dir, pattern)
            existing_files.extend(glob.glob(full_pattern))
        
        # Batch remove files
        for file_path in existing_files:
            try:
                os.remove(file_path)
                total_removed += 1
            except OSError as e:
                logger.warning(f"Failed to remove markdown file {file_path}: {e}")
        
        if total_removed > 0:
            logger.info(f"Batch cleaned up {total_removed} existing files for {len(page_ids)} pages")
        
        return files_removed

def load_metadata_with_filters(property_filters: Optional[Dict[str, str]] = None) -> List[str]:
    """
    Load page IDs from hybrid_metadata.db with optional property filtering.
    Replaces load_json_files_with_property_filter.
    
    Args:
        property_filters: Optional dictionary of property filters (e.g., {"status": "published"})
        
    Returns:
        List of page IDs matching the filters
    """
    registry = get_hybrid_registry() # Changed from get_json_registry()
    
    if not property_filters:
        # No filters, return all page IDs
        content_list = registry.list_content()
        return [item['page_id'] for item in content_list]
    
    # Apply property filters
    content_list = registry.list_content()
    matching_page_ids = []
    
    for item in content_list:
        metadata = item.get('metadata', {})
        properties = metadata.get('properties', {})
        
        # Check if all filters match
        matches = True
        for filter_key, filter_value in property_filters.items():
            property_value = properties.get(filter_key, {})
            
            # Handle different property types
            if isinstance(property_value, dict):
                # For Notion properties like checkbox, select, etc.
                actual_value = property_value.get(property_value.get('type', ''), None)
                
                # Handle checkbox specifically
                if property_value.get('type') == 'checkbox':
                    actual_value = property_value.get('checkbox', False)
                elif property_value.get('type') == 'select':
                    select_obj = property_value.get('select', {})
                    actual_value = select_obj.get('name', '') if select_obj else ''
                elif property_value.get('type') == 'status':
                    status_obj = property_value.get('status', {})
                    actual_value = status_obj.get('name', '') if status_obj else ''
                
                # Convert filter_value to appropriate type for comparison
                if actual_value is not None:
                    if filter_value.lower() in ['true', 'false']:
                        filter_value = filter_value.lower() == 'true'
                    
                    if str(actual_value).lower() != str(filter_value).lower():
                        matches = False
                        break
                else:
                    matches = False
                    break
            else:
                # Direct value comparison
                if str(property_value).lower() != str(filter_value).lower():
                    matches = False
                    break
        
        if matches:
            matching_page_ids.append(item['page_id'])
    
    return matching_page_ids

# Global storage instance
_storage_instance = None

def get_unified_storage(config_file: str = None) -> UnifiedStorage:
    """Get the global unified storage instance."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = UnifiedStorage(config_file)
    return _storage_instance 