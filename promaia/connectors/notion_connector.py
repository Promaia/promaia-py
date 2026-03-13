"""
Notion database connector implementation.
"""
import os
import asyncio
import json
import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult
from promaia.notion.client import notion_client
from promaia.notion.pages import (
    get_database_properties, query_database, get_block_content,
    get_page_title, get_pages_by_date
)
from promaia.markdown.converter import page_to_markdown
from promaia.storage.files import save_page_to_file
from promaia.storage.json_files import save_page_to_json
from promaia.storage.markdown_files import save_page_to_markdown_file
from promaia.utils.timezone_utils import now_utc, days_ago_utc, to_utc

# MONITORING: Add HTTP status code constants for better error tracking
RATE_LIMIT_STATUS_CODES = [429]
SERVER_ERROR_STATUS_CODES = [500, 502, 503, 504]

class NotionConnector(BaseConnector):
    """Connector for Notion databases."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Get client via unified auth module
        workspace = config.get('workspace')
        from promaia.notion.client import get_client, ensure_default_client
        self.client = get_client(workspace) if workspace else ensure_default_client()
    
    async def _monitored_api_call(self, api_func, result: Optional[SyncResult] = None, *args, **kwargs):
        """
        MONITORING: Wrapper for API calls to track performance and errors.
        """
        if result:
            result.add_api_call()
        
        try:
            return await api_func(*args, **kwargs)
        except Exception as e:
            if result:
                result.add_api_error()
                
                # Check for rate limiting specifically
                if hasattr(e, 'status') and e.status in RATE_LIMIT_STATUS_CODES:
                    result.add_rate_limit_hit()
                    self.logger.warning(f"Rate limit hit (429): {e}")
                elif hasattr(e, 'code') and str(e.code) == '429':
                    result.add_rate_limit_hit() 
                    self.logger.warning(f"Rate limit hit (429): {e}")
                elif '429' in str(e):
                    result.add_rate_limit_hit()
                    self.logger.warning(f"Rate limit detected in error message: {e}")
                else:
                    self.logger.error(f"API error: {e}")
            raise
    
    async def connect(self) -> bool:
        """Establish connection to Notion."""
        try:
            # Test connection by trying to retrieve database info
            if self.database_id:
                await self.client.databases.retrieve(database_id=self.database_id)
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Notion: {e}")
            return False
    
    async def test_connection(self) -> bool:
        """Test if the Notion connection is working."""
        return await self.connect()
    
    async def get_database_schema(self) -> Dict[str, Any]:
        """Get the schema/properties of the Notion database."""
        try:
            # Use our own client instead of the global function
            database = await self.client.databases.retrieve(database_id=self.database_id)
            return database.get("properties", {})
        except Exception as e:
            self.logger.error(f"Failed to get database schema: {e}")
            return {}

    async def sync_property_metadata(self, db_path: str = None):
        """
        Sync property and option metadata from Notion to local database.

        Extracts property IDs, option IDs, and relation metadata from Notion's schema
        and stores them in the local database for ID-based resolution.

        Args:
            db_path: Path to the hybrid metadata database
        """
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        try:
            # Get full database response (not just properties)
            database = await self.client.databases.retrieve(database_id=self.database_id)
            properties = database.get("properties", {})
            database_name = self.config.get('name', 'unknown')

            current_time = datetime.now(timezone.utc).isoformat()

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()

                for prop_name, prop_config in properties.items():
                    property_id = prop_config.get("id")
                    property_type = prop_config.get("type")

                    if not property_id:
                        self.logger.warning(f"Property {prop_name} has no ID, skipping")
                        continue

                    # Update or insert property info in notion_property_schema
                    # Try to find existing property by ID first, then by name
                    cursor.execute("""
                        SELECT id FROM notion_property_schema
                        WHERE database_id = ? AND (property_id = ? OR property_name = ?)
                    """, (self.database_id, property_id, prop_name))

                    existing = cursor.fetchone()

                    if existing:
                        # Update existing property (set property_id if it was missing)
                        cursor.execute("""
                            UPDATE notion_property_schema
                            SET property_name = ?,
                                property_id = ?,
                                notion_type = ?,
                                last_seen = ?,
                                is_active = TRUE
                            WHERE database_id = ? AND (property_id = ? OR property_name = ?)
                        """, (prop_name, property_id, property_type, current_time,
                              self.database_id, property_id, prop_name))
                        self.logger.debug(f"Updated property: {prop_name} ({property_id})")
                    else:
                        # Property doesn't exist yet - this shouldn't happen in normal operation
                        # since properties are created during sync, but we'll log it
                        self.logger.warning(f"Property {prop_name} ({property_id}) not in schema table - may need database sync")

                    # Handle select/multi-select/status options
                    if property_type in ('select', 'multi_select', 'status'):
                        options = prop_config.get(property_type, {}).get("options", [])

                        for option in options:
                            option_id = option.get("id")
                            option_name = option.get("name")
                            option_color = option.get("color")

                            if not option_id:
                                self.logger.warning(f"Option {option_name} has no ID, skipping")
                                continue

                            # Check if option exists
                            cursor.execute("""
                                SELECT id FROM notion_select_options
                                WHERE database_id = ? AND property_id = ? AND option_id = ?
                            """, (self.database_id, property_id, option_id))

                            existing_option = cursor.fetchone()

                            if existing_option:
                                # Update existing option
                                cursor.execute("""
                                    UPDATE notion_select_options
                                    SET option_name = ?,
                                        option_color = ?,
                                        property_name = ?,
                                        last_seen = ?,
                                        is_active = TRUE
                                    WHERE database_id = ? AND property_id = ? AND option_id = ?
                                """, (option_name, option_color, prop_name, current_time,
                                      self.database_id, property_id, option_id))

                                self.logger.debug(f"Updated option: {prop_name}.{option_name} ({option_id})")
                            else:
                                # Insert new option
                                cursor.execute("""
                                    INSERT INTO notion_select_options
                                    (database_id, property_id, property_name, option_id, option_name,
                                     option_color, property_type, first_seen, last_seen, is_active)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
                                """, (self.database_id, property_id, prop_name, option_id, option_name,
                                      option_color, property_type, current_time, current_time))

                                self.logger.info(f"Added new option: {prop_name}.{option_name} ({option_id})")

                    # Handle relation properties
                    elif property_type == 'relation':
                        relation_config = prop_config.get('relation', {})
                        target_database_id = relation_config.get('database_id')
                        relation_type = relation_config.get('type')  # 'single_property' or 'dual_property'
                        synced_property_id = relation_config.get('synced_property_id')
                        synced_property_name = relation_config.get('synced_property_name')

                        if target_database_id:
                            # Check if relation exists
                            cursor.execute("""
                                SELECT id FROM notion_relations
                                WHERE database_id = ? AND property_id = ?
                            """, (self.database_id, property_id))

                            existing_relation = cursor.fetchone()

                            if existing_relation:
                                # Update existing relation
                                cursor.execute("""
                                    UPDATE notion_relations
                                    SET property_name = ?,
                                        target_database_id = ?,
                                        relation_type = ?,
                                        synced_property_id = ?,
                                        synced_property_name = ?,
                                        last_seen = ?,
                                        is_active = TRUE
                                    WHERE database_id = ? AND property_id = ?
                                """, (prop_name, target_database_id, relation_type,
                                      synced_property_id, synced_property_name, current_time,
                                      self.database_id, property_id))

                                self.logger.debug(f"Updated relation: {prop_name} -> {target_database_id}")
                            else:
                                # Insert new relation
                                cursor.execute("""
                                    INSERT INTO notion_relations
                                    (database_id, property_id, property_name, target_database_id,
                                     relation_type, synced_property_id, synced_property_name,
                                     first_seen, last_seen, is_active)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
                                """, (self.database_id, property_id, prop_name, target_database_id,
                                      relation_type, synced_property_id, synced_property_name,
                                      current_time, current_time))

                                self.logger.info(f"Added new relation: {prop_name} -> {target_database_id}")

                # Mark properties/options not seen as inactive
                cursor.execute("""
                    UPDATE notion_property_schema
                    SET is_active = FALSE
                    WHERE database_id = ? AND last_seen < ?
                """, (self.database_id, current_time))

                cursor.execute("""
                    UPDATE notion_select_options
                    SET is_active = FALSE
                    WHERE database_id = ? AND last_seen < ?
                """, (self.database_id, current_time))

                cursor.execute("""
                    UPDATE notion_relations
                    SET is_active = FALSE
                    WHERE database_id = ? AND last_seen < ?
                """, (self.database_id, current_time))

                conn.commit()

            self.logger.info(f"Successfully synced property metadata for database {database_name}")

        except Exception as e:
            self.logger.error(f"Failed to sync property metadata: {e}", exc_info=True)
            raise
    
    async def query_pages(self, 
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query pages from the Notion database with proper pagination."""
        try:
            # Special handling for page_id filter - fetch specific page directly
            if filters:
                page_id_filter = None
                other_filters = []
                
                for filter_obj in filters:
                    if filter_obj.property_name == "page_id" and filter_obj.operator == "eq":
                        page_id_filter = filter_obj.value
                    else:
                        other_filters.append(filter_obj)
                
                if page_id_filter:
                    self.logger.debug(f"Fetching specific page by ID: {page_id_filter}")
                    try:
                        page = await self.client.pages.retrieve(page_id=page_id_filter)
                        self.logger.info(f"Successfully fetched page {page_id_filter}: {page.get('properties', {}).get('Name', {}).get('title', [{}])[0].get('plain_text', 'No title')}")
                        return [page]
                    except Exception as e:
                        self.logger.error(f"Failed to fetch page {page_id_filter}: {e}")
                        return []
                
                # Use remaining filters for normal database query
                filters = other_filters if other_filters else None
            
            # Build Notion filter
            notion_filter = self._build_notion_filter(filters, date_filter)
            
            # Build sort condition
            sort_condition = None
            if sort_by:
                sort_condition = [{
                    "property": sort_by,
                    "direction": "descending" if sort_direction == "desc" else "ascending"
                }]
            
            # Use pagination to get all results
            all_pages = []
            start_cursor = None
            page_count = 0
            
            while True:
                # Use our own client instead of the global function
                query_params = {
                    "database_id": self.database_id,
                    "page_size": min(100, limit - len(all_pages) if limit else 100)  # Respect user limit
                }
                
                if notion_filter:
                    query_params["filter"] = notion_filter
                
                if sort_condition:
                    query_params["sorts"] = sort_condition
                
                if start_cursor:
                    query_params["start_cursor"] = start_cursor
                
                response = await self.client.databases.query(**query_params)
                page_results = response.get("results", [])
                all_pages.extend(page_results)
                page_count += 1
                
                # Log pagination progress for large syncs
                if page_count > 1:
                    self.logger.debug(f"Pagination: Retrieved page {page_count} with {len(page_results)} results (total: {len(all_pages)})")
                
                # Stop conditions
                if not response.get("has_more", False):
                    break
                if limit and len(all_pages) >= limit:
                    break
                
                start_cursor = response.get("next_cursor")
                
                # Minimal delay between paginated requests to stay within rate limits
                await asyncio.sleep(0.05)
            
            if page_count > 1:
                self.logger.info(f"Retrieved {len(all_pages)} total pages across {page_count} paginated requests")
            
            return all_pages
            
        except Exception as e:
            self.logger.error(f"Failed to query pages: {e}")
            return []
    
    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full content of a specific Notion page."""
        try:
            # Get page info
            page = await self.client.pages.retrieve(page_id=page_id)

            # Get page content blocks using our own client
            content = await self._get_block_content(page_id)

            # Enhance blocks with meeting content if any meeting blocks are present
            try:
                from promaia.notion.meeting_blocks import enhance_blocks_with_meeting_content
                content = await enhance_blocks_with_meeting_content(content)
            except Exception as meeting_error:
                self.logger.warning(f"Could not enhance meeting blocks for {page_id}: {meeting_error}")
                # Continue with unenhanced content

            result = {
                "id": page_id,
                "page": page,
                "content": content
            }

            if include_properties:
                result["properties"] = page.get("properties", {})

            return result

        except Exception as e:
            self.logger.error(f"Failed to get page content for {page_id}: {e}")
            return {}
    
    async def _get_block_content(self, block_id: str) -> List[Dict[str, Any]]:
        """Get content blocks for a page or block using our own client."""
        try:
            blocks = []
            start_cursor = None
            
            while True:
                params = {"block_id": block_id, "page_size": 100}
                if start_cursor:
                    params["start_cursor"] = start_cursor

                try:
                    response = await self.client.blocks.children.list(**params)
                    blocks.extend(response.get("results", []))

                    if not response.get("has_more", False):
                        break
                    start_cursor = response.get("next_cursor")
                except Exception as api_error:
                    # If we can't fetch this block's children, log and return what we have so far
                    error_msg = str(api_error)
                    if "Could not find block" in error_msg or "Make sure the relevant pages" in error_msg:
                        self.logger.warning(f"Block {block_id} or its children are inaccessible (may be deleted or not shared): {api_error}")
                    else:
                        self.logger.error(f"API error fetching block {block_id}: {api_error}")
                    # Return what we have so far instead of crashing
                    break
            
            # Recursively get child blocks
            for block in blocks:
                if block.get("has_children", False):
                    try:
                        child_blocks = await self._get_block_content(block["id"])
                        block["children"] = child_blocks
                    except Exception as child_error:
                        # Log the error but don't crash - just skip this block's children
                        self.logger.warning(f"Skipping children of block {block.get('id', 'unknown')}: {child_error}")
                        block["children"] = []

            return blocks

        except Exception as e:
            self.logger.error(f"Failed to get block content for {block_id}: {e}")
            return []
    
    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get properties of a specific Notion page."""
        try:
            page = await self.client.pages.retrieve(page_id=page_id)
            return page.get("properties", {})
        except Exception as e:
            self.logger.error(f"Failed to get page properties for {page_id}: {e}")
            return {}
    
    async def sync_to_local(self,
                           output_directory: str,
                           filters: Optional[List[QueryFilter]] = None,
                           date_filter: Optional[DateRangeFilter] = None,
                           include_properties: bool = False,  # Don't write properties to markdown (stored in SQLite)
                           force_update: bool = False,
                           excluded_properties: List[str] = None) -> SyncResult:
        """Sync Notion database content to local storage."""
        result = SyncResult()
        result.start_time = now_utc()
        
        try:
            # Ensure output directory exists
            os.makedirs(output_directory, exist_ok=True)
            
            # Cache the database schema for proper filter type handling
            try:
                self._cached_schema = await self.get_database_schema()
            except Exception as e:
                self.logger.warning(f"Could not cache database schema: {e}")
                self._cached_schema = {}

            # Synchronize database schema with hybrid storage tables
            try:
                from promaia.storage.hybrid_storage import get_hybrid_registry
                registry = get_hybrid_registry()

                database_name = self.config.get('nickname', 'unknown')

                # Sync schema if we have valid schema data
                if self._cached_schema:
                    workspace = self.config.get('workspace')
                    sync_success = registry.sync_table_schema_with_properties(
                        database_id=self.database_id,
                        database_name=database_name,
                        properties=self._cached_schema,
                        workspace=workspace,
                        remove_columns=False  # Default: don't remove columns for safety
                    )

                    if sync_success:
                        self.logger.info(f"Schema synchronized for database '{database_name}'")
                    else:
                        self.logger.warning(f"Schema synchronization had issues for '{database_name}'")
            except Exception as e:
                self.logger.warning(f"Could not synchronize database schema: {e}")

            # Build Notion API filter
            notion_filter = self._build_notion_filter(filters, date_filter)
            
            # Query pages from the database
            pages = await self.query_pages(
                filters=filters,
                date_filter=date_filter,
                sort_by=None,  # Don't sort by last_edited_time as it's not a valid sort property
                sort_direction="desc"
            )
            
            result.pages_fetched = len(pages)
            self.logger.info(f"Found {len(pages)} pages to sync")
            
            # Process each page
            for i, page in enumerate(pages):
                try:
                    page_id = page["id"]
                    page_last_edited_time_str = page.get("last_edited_time")

                    # Attempt to get a display title from page properties (if available from query)
                    # This title will be used for logging and filename generation.
                    title_for_filename = page_id # Default to ID
                    try:
                        # Find the title property by type instead of hardcoding the name
                        properties = page.get("properties", {})
                        for prop_name, prop_data in properties.items():
                            if prop_data.get("type") == "title" and prop_data.get("title"):
                                title_for_filename = prop_data["title"][0].get("plain_text", page_id)
                                break
                        # If title couldn't be extracted here, it will be fetched later if the page is processed.
                        # For logging purposes, we use what we have or the ID.
                    except Exception as title_exc:
                        self.logger.debug(f"Error extracting title for log/filename for page {page_id} from query data: {title_exc}")
                        # title_for_filename remains page_id

                    self.logger.debug(f"Processing page {i+1}: '{title_for_filename}' ({page_id})")

                    # Determine prospective local file path
                    # Check for any file with this page_id in the filename, regardless of title
                    import glob
                    pattern = os.path.join(output_directory, f"* {page_id}.md")
                    matching_files = glob.glob(pattern)
                    page_exists_locally = len(matching_files) > 0
                    local_file_path = matching_files[0] if matching_files else os.path.join(output_directory, f"* {page_id}.md")
                    self.logger.debug(f"Page {page_id}: Checked pattern: {pattern}, Found {len(matching_files)} files, Exists: {page_exists_locally}")

                    # Conditional skipping logic
                    should_skip = False
                    if not force_update and page_exists_locally:
                        db_last_sync_time_str = self.config.get("last_sync_time")
                        self.logger.debug(f"Page {page_id}: force_update is False and page exists locally.")
                        self.logger.debug(f"Page {page_id}: Retrieved page_last_edited_time: {page_last_edited_time_str}, db_last_sync_time: {db_last_sync_time_str}")
                        if page_last_edited_time_str and db_last_sync_time_str:
                            try:
                                page_dt = datetime.fromisoformat(page_last_edited_time_str.replace("Z", "+00:00"))
                                sync_dt = datetime.fromisoformat(db_last_sync_time_str.replace("Z", "+00:00"))
                                
                                if sync_dt.tzinfo is None or sync_dt.tzinfo.utcoffset(sync_dt) is None:
                                    sync_dt = to_utc(sync_dt)
                                    self.logger.debug(f"Page {page_id}: Made naive sync_dt timezone-aware (UTC): {sync_dt}")

                                self.logger.debug(f"Page {page_id}: Comparing page_dt: {page_dt} with sync_dt: {sync_dt} (with 1s tolerance for sync_dt)")
                                if page_dt <= (sync_dt + timedelta(seconds=1)):
                                    should_skip = True
                                    self.logger.info(f"Skipping page {i+1} of {len(pages)}: '{title_for_filename}' ({page_id}). Exists locally and up-to-date (last edit: {page_dt}, last sync: {sync_dt}).")
                                else:
                                    self.logger.info(f"Page {page_id} ('{title_for_filename}') exists locally but is outdated. Will re-sync.")
                            except ValueError as ve:
                                self.logger.warning(f"Could not compare dates for page {page_id} ('{title_for_filename}') (page_last_edit: {page_last_edited_time_str}, db_sync: {db_last_sync_time_str}): {ve}. Proceeding with sync.")
                        else:
                            self.logger.debug(f"Page {page_id} ('{title_for_filename}'): Missing page_last_edited_time or db_last_sync_time. Proceeding with sync.")
                    elif not force_update and not page_exists_locally:
                        self.logger.info(f"Page {page_id} ('{title_for_filename}') does not exist locally. Will sync.")
                    elif force_update:
                        self.logger.info(f"Page {page_id} ('{title_for_filename}'): force_update is True. Will sync.")

                    if should_skip:
                        result.add_skip()
                        continue # Skip to the next page

                    # Get full page content (this is where the actual page data including properties is fetched)
                    page_content = await self.get_page_content(page_id, include_properties)
                    
                    if not page_content:
                        result.add_error(f"Failed to get content for page {page_id}")
                        continue
                    
                    # Get page title for filename (use the one from full page_content now for accuracy)
                    final_title = title_for_filename # Default to title from query
                    try:
                        # Attempt to get a more definitive title from the full page object
                        # Find the title property by type instead of hardcoding the name
                        properties = page_content.get("page", {}).get("properties", {})
                        for prop_name, prop_data in properties.items():
                            if prop_data.get("type") == "title" and prop_data.get("title"):
                                final_title = prop_data["title"][0].get("plain_text", title_for_filename)
                                break
                        # If we still only have page_id after trying the full page, try get_page_title as last resort
                        if final_title == page_id:
                             fetched_title = await get_page_title(page_id) # This makes an API call
                             if fetched_title:
                                 final_title = fetched_title
                    except Exception as e:
                        self.logger.warning(f"Could not reliably determine final title for page {page_id}, using '{final_title}'. Error: {e}")

                    if not final_title or final_title == page_id: # Ensure we don't just use page_id if a better title was expected
                        final_title = f"untitled_{page_id[:8]}"
                    
                    # Construct qualified content_type based on workspace
                    nickname = self.config.get("nickname", "unknown")
                    workspace = self.config.get("workspace", "koii")
                    if workspace == "koii":
                        content_type = nickname
                    else:
                        content_type = f"{workspace}.{nickname}"
                    
                    # Always save in markdown format
                    # Create a safe filename from the title
                    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in final_title)

                    # Extract created_time from page data and prepend to filename
                    date_prefix = ""
                    try:
                        page_obj = page_content.get("page", {})
                        created_time_str = page_obj.get("created_time") or page_content.get("created_time")
                        
                        if created_time_str:
                            # Parse the created_time and format as YYYY-MM-DD
                            created_dt = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
                            date_prefix = created_dt.strftime("%Y-%m-%d") + " "
                            self.logger.debug(f"Using created_time {created_time_str} for date prefix: {date_prefix}")
                        else:
                            # Fallback: try to extract from properties if available
                            properties = page_content.get("properties", {})
                            for prop_name, prop_data in properties.items():
                                if prop_data.get("type") == "date" and prop_name.lower() in ["date", "created", "created_time"]:
                                    date_value = prop_data.get("date", {})
                                    if date_value and date_value.get("start"):
                                        date_str = date_value.get("start")
                                        created_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                                        date_prefix = created_dt.strftime("%Y-%m-%d") + " "
                                        self.logger.debug(f"Using property {prop_name} for date prefix: {date_prefix}")
                                        break
                            
                            if not date_prefix:
                                self.logger.warning(f"No created_time found for page {page_id}, using current date")
                                date_prefix = now_utc().strftime("%Y-%m-%d") + " "
                            
                    except Exception as e:
                        self.logger.warning(f"Error extracting created_time for page {page_id}: {e}, using current date")
                        date_prefix = now_utc().strftime("%Y-%m-%d") + " "

                    filename = f"{date_prefix}{safe_title} {page_id}.md"
                    file_path = os.path.join(output_directory, filename)
                    
                    # Convert to markdown
                    markdown_content = page_to_markdown(
                        page_content["content"], 
                        properties=page_content.get("properties") if include_properties else None,
                        include_properties=include_properties,
                        excluded_properties=excluded_properties
                    )
                    
                    # Save the markdown content to the file directly
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(markdown_content)
                    
                    result.add_success(file_path)
                    self.logger.debug(f"Saved page to markdown: {file_path}")
                    
                    self.logger.debug(f"Processed page: {final_title}")
                    
                except Exception as e:
                    error_msg = f"Failed to process page {page.get('id', 'unknown')}: {e}"
                    result.add_error(error_msg)
                    self.logger.error(error_msg)
            
        except Exception as e:
            error_msg = f"Sync operation failed: {e}"
            result.add_error(error_msg)
            self.logger.error(error_msg)
        
        result.end_time = now_utc()
        return result
    
    def _build_notion_filter(self, filters: Optional[List[QueryFilter]] = None,
                            date_filter: Optional[DateRangeFilter] = None) -> Optional[Dict[str, Any]]:
        """Build Notion API filter from QueryFilter and DateRangeFilter objects."""
        filter_conditions = []
        
        # Add property filters
        if filters:
            for f in filters:
                condition = self._query_filter_to_notion(f)
                if condition:
                    filter_conditions.append(condition)
                    self.logger.debug(f"Added property filter condition: {condition}")
        
        # Add date filter
        if date_filter:
            date_condition = self._date_filter_to_notion(date_filter)
            if date_condition:
                filter_conditions.append(date_condition)
                self.logger.debug(f"Added date filter condition: {date_condition}")
        
        # Return combined filter
        final_filter = None
        if not filter_conditions:
            final_filter = None
        elif len(filter_conditions) == 1:
            final_filter = filter_conditions[0]
        else:
            final_filter = {
                "and": filter_conditions
            }
        
        self.logger.debug(f"Final Notion filter: {final_filter}")
        return final_filter
    
    def _query_filter_to_notion(self, query_filter: QueryFilter) -> Optional[Dict[str, Any]]:
        """Convert QueryFilter to Notion API filter format."""
        prop_name = query_filter.property_name
        operator = query_filter.operator
        value = query_filter.value
        
        # Try to get the property type from the cached schema if available
        # This will help us use the correct filter format
        prop_type = None
        if hasattr(self, '_cached_schema') and self._cached_schema:
            schema_prop = self._cached_schema.get(prop_name)
            if schema_prop:
                prop_type = schema_prop.get("type")

        # Map operators to Notion filter format
        if operator == "eq":
            # Use the correct filter type based on property type
            if prop_type == "status":
                return {
                    "property": prop_name,
                    "status": {"equals": value}
                }
            elif prop_type == "checkbox":
                return {
                    "property": prop_name,
                    "checkbox": {"equals": value}
                }
            elif isinstance(value, bool) or prop_name.lower() in ["sync", "reference", "done", "koii chat", "koii_chat"]:
                # Assume checkbox for boolean values or known checkbox property names
                return {
                    "property": prop_name,
                    "checkbox": {"equals": value}
                }
            else:
                # Default to select for backward compatibility
                return {
                    "property": prop_name,
                    "select": {"equals": value}
                }
        elif operator == "ne":
            if prop_type == "status":
                return {
                    "property": prop_name,
                    "status": {"does_not_equal": value}
                }
            else:
                return {
                    "property": prop_name,
                    "select": {"does_not_equal": value}
                }
        elif operator == "in":
            # For multi-select or multiple values
            if isinstance(value, list):
                conditions = []
                for v in value:
                    if prop_type == "status":
                        conditions.append({
                            "property": prop_name,
                            "status": {"equals": v}
                        })
                    elif prop_type == "multi_select":
                        conditions.append({
                            "property": prop_name,
                            "multi_select": {"contains": v}
                        })
                    else:
                        conditions.append({
                            "property": prop_name,
                            "select": {"equals": v}
                        })
                return {"or": conditions}
            else:
                if prop_type == "status":
                    return {
                        "property": prop_name,
                        "status": {"equals": value}
                    }
                elif prop_type == "multi_select":
                    return {
                        "property": prop_name,
                        "multi_select": {"contains": value}
                    }
                else:
                    return {
                        "property": prop_name,
                        "select": {"equals": value}
                    }
        elif operator == "contains":
            return {
                "property": prop_name,
                "rich_text": {"contains": value}
            }
        
        # Add more operators as needed
        return None
    
    def _date_filter_to_notion(self, date_filter: DateRangeFilter) -> Optional[Dict[str, Any]]:
        """Convert DateRangeFilter to Notion API filter format."""
        prop_name = date_filter.property_name
        
        # Check if this is a built-in timestamp property
        is_timestamp_property = prop_name in ["created_time", "last_edited_time"]
        
        if is_timestamp_property:
            # For timestamp properties, combine start and end dates in a single condition
            condition = {
                "timestamp": prop_name,
                prop_name: {}
            }
            
            if date_filter.start_date:
                condition[prop_name]["after"] = date_filter.start_date.isoformat()
            
            if date_filter.end_date:
                condition[prop_name]["before"] = date_filter.end_date.isoformat()
            
            if condition[prop_name]:  # Only return if we have at least one date condition
                return condition
            else:
                return None
        else:
            # For regular date properties, use separate conditions if needed
            conditions = []
            
            if date_filter.start_date:
                conditions.append({
                    "property": prop_name,
                    "date": {"on_or_after": date_filter.start_date.isoformat()}
                })
            
            if date_filter.end_date:
                conditions.append({
                    "property": prop_name,
                    "date": {"on_or_before": date_filter.end_date.isoformat()}
                })
            
            if not conditions:
                return None
            elif len(conditions) == 1:
                return conditions[0]
            else:
                return {"and": conditions}
    
    def _extract_property_value(self, property_data: Dict[str, Any]) -> Any:
        """Extract the actual value from a Notion property data structure."""
        prop_type = property_data.get("type")
        
        if prop_type == "title" and property_data.get("title"):
            return "".join([t.get("plain_text", "") for t in property_data["title"]])
        elif prop_type == "rich_text" and property_data.get("rich_text"):
            return "".join([t.get("plain_text", "") for t in property_data["rich_text"]])
        elif prop_type == "select" and property_data.get("select"):
            return property_data["select"].get("name")
        elif prop_type == "status" and property_data.get("status"):
            return property_data["status"].get("name")
        elif prop_type == "multi_select" and property_data.get("multi_select"):
            return [item.get("name") for item in property_data["multi_select"]]
        elif prop_type == "date" and property_data.get("date"):
            return property_data["date"].get("start")
        elif prop_type == "checkbox":
            return property_data.get("checkbox", False)
        elif prop_type == "number":
            return property_data.get("number")
        elif prop_type == "url":
            return property_data.get("url")
        elif prop_type == "email":
            return property_data.get("email")
        elif prop_type == "phone_number":
            return property_data.get("phone_number")
        elif prop_type == "created_time":
            return property_data.get("created_time")
        elif prop_type == "last_edited_time":
            return property_data.get("last_edited_time")
        
        return None
    
    async def sync_to_local_unified(self,
                                   storage,
                                   db_config,
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None,
                                   properties_only: bool = False) -> SyncResult:
        """Sync Notion database content to local storage using the unified storage system."""
        result = SyncResult()
        result.start_time = now_utc()
        # MONITORING: Set database name for tracking
        result.database_name = getattr(db_config, 'name', 'unknown')
        
        try:
            # Cache the database schema for proper filter type handling
            try:
                result.add_api_call()  # MONITORING: Track API call
                self._cached_schema = await self.get_database_schema()

                # Update property schema in hybrid registry for property embeddings
                if self._cached_schema:
                    try:
                        database_id = getattr(db_config, 'database_id', None)
                        database_name = getattr(db_config, 'nickname', None)  # Use nickname to avoid qualified names

                        if database_id and database_name:
                            # Get workspace for table name determination
                            workspace = self.config.get('workspace')

                            # Sync property schema and create columns
                            from promaia.storage.hybrid_storage import get_hybrid_registry
                            registry = get_hybrid_registry()
                            registry.sync_table_schema_with_properties(
                                database_id=database_id,
                                database_name=database_name,
                                properties=self._cached_schema,  # _cached_schema IS the properties dict
                                workspace=workspace,
                                remove_columns=False  # Don't remove columns for safety
                            )

                            # Determine table name for logging
                            if workspace and database_name:
                                table_name = f"notion_{workspace}_{database_name}"
                            else:
                                table_name = 'generic_content'

                            self.logger.debug(f"✅ Synced property schema and columns for {database_name} (table: {table_name})")
                    except Exception as schema_update_error:
                        self.logger.warning(f"Failed to update property schema: {schema_update_error}")
            except Exception as e:
                result.add_api_error()  # MONITORING: Track API error
                self.logger.warning(f"Could not cache database schema: {e}")
                self._cached_schema = {}
            
            # Special handling for page_id filter - fetch specific page directly
            page_id_filter = None
            remaining_filters = []
            
            if filters:
                self.logger.debug(f"Processing {len(filters)} filters: {[(f.property_name, f.operator, f.value) for f in filters]}")
                for filter_obj in filters:
                    if filter_obj.property_name == "page_id" and filter_obj.operator == "eq":
                        page_id_filter = filter_obj.value
                        self.logger.debug(f"Found page_id filter: {page_id_filter}")
                    else:
                        remaining_filters.append(filter_obj)
            else:
                self.logger.debug("No filters provided")
            
            if page_id_filter:
                self.logger.info(f"Fetching specific page by ID: {page_id_filter}")
                try:
                    result.add_api_call()  # MONITORING: Track API call
                    page = await self.client.pages.retrieve(page_id=page_id_filter)
                    pages = [page]
                    self.logger.info(f"Successfully fetched page {page_id_filter}")
                except Exception as e:
                    result.add_api_error()  # MONITORING: Track API error
                    self.logger.error(f"Failed to fetch page {page_id_filter}: {e}")
                    pages = []
            else:
                # Normal database query with remaining filters
                result.add_api_call()  # MONITORING: Track API call
                pages = await self.query_pages(
                    filters=remaining_filters if remaining_filters else None,
                    date_filter=date_filter,
                    sort_by=None,
                    sort_direction="desc"
                )
            
            result.pages_fetched = len(pages)

            if properties_only:
                self.logger.info(f"Found {len(pages)} pages for property-only sync")
            else:
                self.logger.info(f"Found {len(pages)} pages to sync")

            # OPTIMIZATION: Process pages in batches for improved performance
            if pages:
                # Use property-only batch processing if requested (much faster)
                if properties_only:
                    batch_results = await self._process_properties_only_batch(
                        pages, storage, db_config
                    )
                else:
                    # Use full batch processing for complete sync
                    batch_results = await self._process_page_batch(
                        pages, storage, db_config, include_properties, force_update, excluded_properties
                    )

                # Process results and update counters (with clean progress)
                saved_count = 0
                skipped_count = 0
                error_count = 0
                
                for page_result in batch_results:
                    if isinstance(page_result, Exception):
                        result.errors.append(f"Failed to process page: {page_result}")
                        error_count += 1
                        continue
                        
                    if page_result["status"] == "saved":
                        result.pages_saved += 1
                        saved_count += 1
                        # Only log at debug level for individual pages
                        self.logger.debug(f"Processed page: {page_result['title']}")
                    elif page_result["status"] == "skipped":
                        result.pages_skipped += 1
                        skipped_count += 1
                    elif page_result["status"] == "error":
                        result.errors.append(page_result.get("error", "Unknown error"))
                        error_count += 1
                
                # Individual processing messages removed for clean 3-line output per database
                if properties_only:
                    self.logger.info(f"Property sync completed: {saved_count} updated, {skipped_count} skipped, {error_count} failed")
                else:
                    self.logger.info(f"Batch processing completed: {saved_count} saved, {skipped_count} skipped, {error_count} failed")

                # Update push tracker baseline hashes after successful pull
                if saved_count > 0:
                    self._update_push_tracker_after_pull(db_config, batch_results)

                # Handle deletion detection - remove local pages that no longer exist in Notion
                deleted_count = await self._detect_and_remove_deleted_pages(
                    storage, db_config, pages, date_filter
                )
                if deleted_count > 0:
                    result.pages_deleted = deleted_count
                    self.logger.info(f"Removed {deleted_count} deleted pages from local storage")

        except Exception as e:
            self.logger.error(f"Sync failed: {e}")
            result.errors.append(f"Sync failed: {e}")
        
        result.end_time = now_utc()
        return result

    def _update_push_tracker_after_pull(self, db_config, batch_results: List[Dict[str, Any]]):
        """Update push tracker baseline hashes after successful pull sync."""
        try:
            from promaia.storage.notion_push import NotionPushTracker
            from pathlib import Path

            # Initialize push tracker
            from promaia.utils.env_writer import get_data_dir
            tracker = NotionPushTracker(
                get_data_dir() / "notion_push_cache.json"
            )

            # Get markdown directory
            markdown_dir = Path(db_config.markdown_directory)
            if not markdown_dir.exists():
                return

            # Update baseline for each successfully saved page
            for page_result in batch_results:
                if isinstance(page_result, dict) and page_result.get("status") == "saved":
                    page_id = page_result.get("page_id")
                    title = page_result.get("title")

                    if not page_id or not title:
                        continue

                    # Find the markdown file
                    # Try exact match first
                    file_path = markdown_dir / f"{title}.md"
                    if not file_path.exists():
                        # Try finding by glob (handles sanitized filenames)
                        matches = list(markdown_dir.glob(f"{title}*.md"))
                        if matches:
                            file_path = matches[0]
                        else:
                            continue

                    # Calculate hash of current file content
                    if file_path.exists():
                        file_hash = tracker.get_file_hash(file_path)
                        # Update baseline - after pull, local matches Notion
                        tracker.update_last_pulled_hash(file_path, file_hash)
                        self.logger.debug(f"Updated push baseline for {file_path.name}")

        except Exception as e:
            self.logger.warning(f"Failed to update push tracker: {e}")

    async def _detect_and_remove_deleted_pages(self, storage, db_config,
                                               synced_pages: List[Dict[str, Any]],
                                               date_filter: Optional[Any] = None) -> int:
        """
        Detect and remove local pages that no longer exist in Notion.

        Compares pages synced from Notion (within the given filter/time window) against
        local pages (within the same filter/time window) and deletes any local pages
        that don't exist in Notion.

        Args:
            storage: The storage instance
            db_config: Database configuration
            synced_pages: List of pages that were just synced from Notion (within filter criteria)
            date_filter: Optional date filter - deletion detection respects the same time window

        Returns:
            Number of pages deleted
        """
        try:
            import sqlite3
            import os
            from pathlib import Path
            from promaia.storage.hybrid_storage import get_hybrid_registry

            # Get the set of page_ids that exist in Notion (from the sync)
            # These are all pages within the specified filter/time window
            notion_page_ids = {page['id'] for page in synced_pages}
            self.logger.debug(f"Deletion detection: {len(notion_page_ids)} pages from Notion")

            # Query local database for all page_ids for this database/workspace
            registry = get_hybrid_registry()

            with sqlite3.connect(registry.db_path) as conn:
                cursor = conn.cursor()

                # Build query to get local pages for this database
                query = """
                    SELECT page_id, file_path, title
                    FROM unified_content
                    WHERE workspace = ? AND database_id = ?
                """
                params = [db_config.workspace, db_config.database_id]

                # If there's a date filter, only check pages within that range
                # This prevents deleting old pages when doing a date-filtered sync
                if date_filter and hasattr(date_filter, 'start_date') and date_filter.start_date:
                    query += " AND last_edited_time >= ?"
                    params.append(date_filter.start_date.isoformat())
                    self.logger.debug(f"Deletion detection: Using date filter >= {date_filter.start_date.isoformat()}")

                cursor.execute(query, params)
                local_pages = cursor.fetchall()
                self.logger.debug(f"Deletion detection: {len(local_pages)} pages in local database")

            # Find pages that exist locally but not in Notion
            pages_to_delete = []
            for page_id, file_path, title in local_pages:
                if page_id not in notion_page_ids:
                    pages_to_delete.append((page_id, file_path, title))

            if not pages_to_delete:
                self.logger.debug("Deletion detection: No pages to delete")
                return 0

            self.logger.info(f"Deletion detection: Found {len(pages_to_delete)} pages to delete")

            self.logger.info(f"Found {len(pages_to_delete)} deleted pages to remove from local storage")

            # Delete each page
            deleted_count = 0
            for page_id, file_path, title in pages_to_delete:
                try:
                    # Delete from unified_content and related tables
                    registry.delete_page(page_id)

                    # Delete markdown file if it exists
                    if file_path:
                        full_path = file_path if os.path.isabs(file_path) else os.path.join(
                            os.getcwd(), file_path
                        )
                        if os.path.exists(full_path):
                            os.remove(full_path)
                            self.logger.debug(f"Deleted markdown file: {full_path}")

                    deleted_count += 1
                    self.logger.debug(f"Deleted page: {title or page_id}")

                except Exception as e:
                    self.logger.warning(f"Failed to delete page {page_id}: {e}")

            return deleted_count

        except Exception as e:
            self.logger.error(f"Error in deletion detection: {e}")
            return 0

    async def _process_page_batch(self, pages_batch: List[Dict[str, Any]], storage, db_config, 
                                include_properties: bool, force_update: bool, 
                                excluded_properties: List[str] = None) -> List[Dict[str, Any]]:
        """Process a batch of pages concurrently for better performance."""
        
        async def process_single_page(page, page_index):
            """Process a single page and return the result."""
            page_id = page["id"]
            
            # Get page title for logging and filename
            title_for_filename = page_id # Default to ID
            try:
                # Find the title property by type instead of hardcoding the name
                properties = page.get("properties", {})
                for prop_name, prop_data in properties.items():
                    if prop_data.get("type") == "title" and prop_data.get("title"):
                        title_for_filename = prop_data["title"][0].get("plain_text", page_id)
                        break
            except Exception as title_exc:
                self.logger.debug(f"Error extracting title for log/filename for page {page_id} from query data: {title_exc}")
            
            # Only log individual page processing at debug level
            self.logger.debug(f"Processing page {page_index+1}: '{title_for_filename}' ({page_id})")
            
            # Check if we need to update this page
            should_update = force_update
            
            if not should_update:
                # First check if files exist locally
                file_status = storage.files_exist_locally(page_id, title_for_filename, db_config)
                files_missing = False
                
                # Check if markdown file is missing
                if not file_status['markdown']:
                    files_missing = True
                    self.logger.debug(f"Page {page_id} ('{title_for_filename}'): Markdown file missing locally. Will sync.")
                
                if files_missing:
                    should_update = True
                else:
                    # Files exist locally, check modification times
                    page_last_edited_time_str = page.get("last_edited_time")
                    db_last_sync_time_str = db_config.last_sync_time
                    
                    if page_last_edited_time_str and db_last_sync_time_str:
                        try:
                            page_dt = datetime.fromisoformat(page_last_edited_time_str.replace("Z", "+00:00"))
                            sync_dt = datetime.fromisoformat(db_last_sync_time_str.replace("Z", "+00:00"))
                            # Use same 1-second buffer as date filter to avoid inconsistency
                            should_update = page_dt > (sync_dt + timedelta(seconds=1))
                            
                            if not should_update:
                                self.logger.debug(f"Page {page_id} ('{title_for_filename}') exists locally and has not been modified since last sync (with 1s buffer). Skipping.")
                                return {"status": "skipped", "page_id": page_id, "title": title_for_filename}
                        except ValueError as ve:
                            self.logger.warning(f"Could not parse times for page {page_id}: {ve}. Proceeding with sync.")
                            should_update = True
                    else:
                        should_update = True
            
            if not should_update:
                return {"status": "skipped", "page_id": page_id, "title": title_for_filename}
            
            try:
                # Get full page content - always include properties for JSON storage
                # Pass result object to track API calls for this page
                page_data = await self.get_page_content(page_id, True)
                
                if not page_data:
                    self.logger.warning(f"No content found for page {page_id}")
                    return {"status": "error", "page_id": page_id, "title": title_for_filename, 
                           "error": f"No content found for page {page_id}"}
                
                # Transform page_data to the format expected by unified storage
                page_obj = page_data.get("page", {})
                content_data = {
                    "id": page_id,
                    "content": page_data.get("content", []),
                    "properties": page_data.get("properties", {}),
                    "created_time": page_obj.get("created_time"),
                    "last_edited_time": page_obj.get("last_edited_time"),
                    "url": page_obj.get("url"),
                    "parent": page_obj.get("parent"),
                    "archived": page_obj.get("archived", False)
                }
                
                # Generate markdown content if needed
                markdown_content = None
                # Check if subpages sync is enabled for this database
                sync_subpages = getattr(db_config, 'sync_subpages', False)
                
                if sync_subpages:
                    # Use enhanced subpage functionality with inline expansion
                    from promaia.markdown.converter import page_to_markdown_with_subpages
                    try:
                        self.logger.debug(f"Fetching page with subpages for: {page_id}")
                        markdown_content = await page_to_markdown_with_subpages(
                            content_data["content"], 
                            properties=content_data.get("properties") if include_properties else None,
                            include_properties=include_properties,
                            excluded_properties=excluded_properties or [],
                            parent_page_id=page_id  # Pass the parent page ID
                        )
                    except Exception as subpage_error:
                        self.logger.warning(f"Failed to process subpages for {page_id}, falling back to regular content: {subpage_error}")
                        # Fall back to regular page_to_markdown
                        from promaia.markdown.converter import page_to_markdown
                        markdown_content = page_to_markdown(
                            content_data["content"], 
                            properties=content_data.get("properties") if include_properties else None,
                            include_properties=include_properties,
                            excluded_properties=excluded_properties or []
                        )
                else:
                    # Use regular page_to_markdown without subpages
                    from promaia.markdown.converter import page_to_markdown
                    markdown_content = page_to_markdown(
                        content_data["content"], 
                        properties=content_data.get("properties") if include_properties else None,
                        include_properties=include_properties,
                        excluded_properties=excluded_properties or []
                    )
                
                # Use the unified storage system to save the content
                saved_files = storage.save_content(
                    page_id=page_id,
                    title=title_for_filename,
                    content_data=content_data,
                    database_config=db_config,
                    markdown_content=markdown_content
                )
                
                return {"status": "saved", "page_id": page_id, "title": title_for_filename, 
                       "saved_files": saved_files}
                
            except Exception as e:
                self.logger.error(f"Failed to sync page {page_id}: {e}")
                return {"status": "error", "page_id": page_id, "title": title_for_filename, 
                       "error": str(e)}
        
        # Process pages in batches with optimized concurrency for maximum speed
        # Optimal batch size balances API rate limits with throughput
        BATCH_SIZE = 12  # Optimal for Notion API rate limits (3 req/sec with bursts)
        BATCH_DELAY = 0.08  # Minimal delay that prevents rate limiting
        
        results = []
        
        for i in range(0, len(pages_batch), BATCH_SIZE):
            batch = pages_batch[i:i + BATCH_SIZE]
            batch_tasks = [process_single_page(page, i + j) for j, page in enumerate(batch)]
            
            # Process this batch concurrently
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            results.extend(batch_results)
            
            # Small delay between batches to respect rate limits
            if i + BATCH_SIZE < len(pages_batch):
                await asyncio.sleep(BATCH_DELAY)

        return results

    async def _process_properties_only_batch(self, pages_batch: List[Dict[str, Any]], storage,
                                            db_config) -> List[Dict[str, Any]]:
        """
        Process a batch of pages for property-only sync (no content fetching).

        This is much faster than full sync as it only updates property columns
        without fetching page blocks or generating markdown.

        Args:
            pages_batch: List of page objects from query_pages (already includes properties)
            storage: HybridRegistryStorage instance
            db_config: Database configuration

        Returns:
            List of result dicts with status, page_id, title
        """
        database_id = getattr(db_config, 'database_id', None)
        database_name = getattr(db_config, 'nickname', None)  # Use nickname to avoid qualified names
        workspace = self.config.get('workspace', '')

        if not database_id or not database_name or not workspace:
            self.logger.error("Missing database_id, database_name, or workspace for property-only sync")
            return [{"status": "error", "error": "Missing required configuration"}]

        results = []

        for page in pages_batch:
            page_id = page["id"]

            # Extract title for logging
            title_for_log = page_id  # Default to ID
            try:
                properties = page.get("properties", {})
                for prop_name, prop_data in properties.items():
                    if prop_data.get("type") == "title" and prop_data.get("title"):
                        title_for_log = prop_data["title"][0].get("plain_text", page_id)
                        break
            except Exception:
                pass  # Use default

            try:
                # Update properties in storage
                success = storage.update_page_properties(
                    page_id=page_id,
                    database_id=database_id,
                    database_name=database_name,
                    workspace=workspace,
                    properties=page.get("properties", {})
                )

                if success:
                    results.append({"status": "saved", "page_id": page_id, "title": title_for_log})
                    self.logger.debug(f"✅ Updated properties for: {title_for_log}")
                else:
                    results.append({"status": "error", "page_id": page_id, "title": title_for_log,
                                  "error": "Failed to update properties"})

            except Exception as e:
                self.logger.error(f"Failed to update properties for {page_id}: {e}")
                results.append({"status": "error", "page_id": page_id, "title": title_for_log,
                              "error": str(e)})

        return results