"""
Notion page and block retrieval operations.
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json
import os
import traceback
import logging

from promaia.notion.client import notion_client, ensure_default_client
from promaia.utils.config import get_last_sync_time, get_sync_days_setting
from notion_client.errors import APIResponseError
from promaia.storage.block_cache import BlockCache
from promaia.utils.rate_limiter import get_notion_rate_limiter

logger = logging.getLogger(__name__)

# Cache for blocks to avoid redundant API calls
block_cache = {}

# Rate limiting configuration
NOTION_RATE_LIMIT_DELAY = 0.2  # 200ms delay between API calls to avoid rate limiting
NOTION_MAX_CHUNK_SIZE = 25     # Maximum pages per chunk to avoid rate limiting

# Sub-page sync configuration
SUBPAGE_SYNC_ENABLED = True    # Feature flag for sub-page syncing
MAX_SUBPAGE_DEPTH = 3          # Maximum depth for recursive sub-page fetching
SUBPAGE_RATE_LIMIT_DELAY = 0.3 # Additional delay for sub-page fetching

async def get_database_properties(database_id: str):
    """
    Get all properties from a Notion database.
    
    Args:
        database_id: ID of the Notion database
        
    Returns:
        Dictionary of property names and types
    """
    try:
        client = ensure_default_client()
        database = await client.databases.retrieve(database_id=database_id)
        properties = database.get("properties", {})
        
        return {
            prop_name: prop_info.get("type", "unknown")
            for prop_name, prop_info in properties.items()
        }
    except Exception as e:
        logger.error(f"Error getting database properties for {database_id}: {str(e)}")
        return {}

async def query_database(database_id: str, filter_condition=None, sort_condition=None, start_cursor=None, page_size=100):
    """
    Query a Notion database with optional filter and sort conditions.
    
    Args:
        database_id: ID of the Notion database
        filter_condition: Optional filter to apply to the query
        sort_condition: Optional sort to apply to the query
        start_cursor: Optional pagination cursor
        page_size: Number of results per page (max 100)
        
    Returns:
        List of page objects from the database
    """
    # Ensure the notion client is initialized
    client = ensure_default_client()
    
    query_params = {
        "database_id": database_id,
        "page_size": page_size
    }
    
    if filter_condition:
        query_params["filter"] = filter_condition
        
    if sort_condition:
        query_params["sorts"] = sort_condition
        
    if start_cursor:
        query_params["start_cursor"] = start_cursor
    
    logger.debug(f"[query_database] Initial query_params for {database_id}: {json.dumps(query_params, indent=2)}")

    # Use adaptive rate limiter
    rate_limiter = get_notion_rate_limiter()
    await rate_limiter.acquire()

    # Get the first page of results
    response = await client.databases.query(**query_params)
    results = response["results"]
    logger.debug(f"[query_database] Initial response for {database_id}: {len(results)} results. Has more: {response.get('has_more')}, Next cursor: {response.get('next_cursor')}")
    
    # If the requested page_size was 1 (implying "get latest/top 1 after sort" by the caller),
    # and we got at least one result, we don't need to paginate further.
    # The calling function (e.g., get_pages_by_date with get_latest=True)
    # typically takes results[:1] from what query_database returns.
    if page_size == 1 and results:
        logger.debug(f"[query_database] Optimization for {database_id}: page_size is 1 and results were found. Skipping further pagination. Returning {len(results)} result(s).")
        return results

    # If page_size was 1 but no results, and has_more is true (unlikely with sorts, but possible), allow pagination to try.
    # Or if page_size > 1, proceed with normal pagination.
    page_count = 1 # Debug counter
    while response.get("has_more", False) and response.get("next_cursor"):
        logger.debug(f"[query_database] Pagination for {database_id} (Page: {page_count + 1}). Current results: {len(results)}")
        query_params["start_cursor"] = response["next_cursor"]
        logger.debug(f"[query_database] Querying {database_id} with new start_cursor: {query_params['start_cursor']}")

        # Use adaptive rate limiter for pagination
        await rate_limiter.acquire()

        response = await client.databases.query(**query_params)
        logger.debug(f"[query_database] Paginated response for {database_id}: {len(response.get('results', []))} new results. Has more: {response.get('has_more')}, Next cursor: {response.get('next_cursor')}")
        results.extend(response["results"])
        page_count += 1
        
        # Stop pagination if we've reached a reasonable limit to avoid rate limiting
        if len(results) >= NOTION_MAX_CHUNK_SIZE:  # Stop at the chunk size limit
            logger.warning(f"[query_database] Stopping pagination for {database_id} at {len(results)} results to avoid rate limiting. Consider using smaller date ranges.")
            break
    
    logger.debug(f"[query_database] Exiting pagination for {database_id}. Total results: {len(results)}, Total pages fetched: {page_count}")
    return results

async def get_sync_pages(database_id: str, sync_property: str = "Sync"):
    """
    Get all pages from the specified Notion database 
    where the specified sync property is checked true.
    
    Args:
        database_id: ID of the Notion database
        sync_property: Name of the checkbox property to filter by (default: "Sync")
        
    Returns:
        List of page objects with the specified sync property=true
    """
    filter_condition = {
        "property": sync_property,
        "checkbox": {
            "equals": True
        }
    }
    
    return await query_database(database_id, filter_condition)

async def get_pages_edited_after(database_id: str, timestamp: str, sync_property: str = "Sync"):
    """
    Get all pages from the specified Notion database that were edited after
    the given timestamp and have the sync property checked true.
    
    Args:
        database_id: ID of the Notion database
        timestamp: ISO format timestamp to filter by last_edited_time
        sync_property: Name of the checkbox property to filter by (default: "Sync")
        
    Returns:
        List of page objects that match the criteria
    """
    # Create a compound filter for both sync property and last edited time
    filter_condition = {
        "and": [
            {
                "property": sync_property,
                "checkbox": {
                    "equals": True
                }
            },
            {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "after": timestamp
                }
            }
        ]
    }
    
    # Sort by last edited time descending
    sort_condition = [
        {
            "timestamp": "last_edited_time",
            "direction": "descending"
        }
    ]
    
    return await query_database(database_id, filter_condition, sort_condition)

async def get_pages_by_date(
    database_id: str, 
    days: Optional[int] = None, 
    specific_date: Optional[str] = None,
    fetch_all: bool = False, 
    last_sync_time_override: Optional[datetime] = None, # For specific cases, usually from config
    force_pull: bool = False, # New flag
    content_type: str = "journal", # Added to get specific last_sync_time
    get_latest: bool = False # ADDED: New parameter to fetch only the latest page
) -> List[Dict[str, Any]]:
    """
    Retrieve pages from a Notion database, filtered by date or fetching the latest.

    Args:
        database_id: The ID of the Notion database.
        days: Number of past days to retrieve entries from. Used if specific_date is None and fetch_all is False.
        specific_date: Specific date (YYYY-MM-DD) to fetch entries for.
        fetch_all: If True, retrieve all pages from the database, ignoring date filters and get_latest.
        last_sync_time_override: Specific last sync time to use. If None, uses config.
        force_pull: If True, ignores last_sync_time and fetches all pages matching date criteria (unless get_latest is True).
        content_type: The type of content being fetched, for fetching the correct last_sync_time.
        get_latest: If True, fetches only the most recently edited page, overriding some other filters.

    Returns:
        A list of page objects from Notion. If get_latest is True, the list will contain at most one page.
    """
    filters = []
    sorts = None # Initialize sorts

    # If get_latest is True, we primarily sort to find the latest and ignore other date range filters.
    if get_latest:
        logger.info(f"Fetching the latest page for db: {database_id} (content_type: {content_type})")
        sorts = [{
            "timestamp": "last_edited_time", # Or "created_time" depending on desired "latest" definition
            "direction": "descending"
        }]
        # No specific date filters needed if we just want the absolute latest.
        # Pagination will be handled by query_database, we'll take the first result later.
        # We don't set page_size=1 here in query_database call yet, as it might interact poorly if other filters were to be hypothetically added with get_latest.
        # Instead, we'll fetch with default page_size and then take the first from the sorted results.
        use_last_sync_time = False # Overridden by get_latest
        fetch_all = True # Effectively, yes, to ensure we get the true latest unless other non-date filters are added in future.
                         # For now, with no other non-date filters, this simplifies logic for get_latest.

    else: # Original logic if not get_latest
        use_last_sync_time = not fetch_all and not force_pull and not specific_date
        
        actual_last_sync_time = None
        if use_last_sync_time:
            actual_last_sync_time = last_sync_time_override if last_sync_time_override else get_last_sync_time(content_type)

        if fetch_all:
            logger.info(f"Fetching all pages for db: {database_id} (force_pull: {force_pull}, content_type: {content_type})")
            pass 
        elif specific_date is not None:
            try:
                specific_date_obj = datetime.strptime(specific_date, "%Y-%m-%d")
                start_of_day = specific_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = specific_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                filters.append({
                    "or": [
                        {
                            "timestamp": "created_time",
                            "created_time": {
                                "on_or_after": start_of_day.isoformat(),
                                "on_or_before": end_of_day.isoformat()
                            }
                        },
                        {
                            "timestamp": "last_edited_time",
                            "last_edited_time": {
                                "on_or_after": start_of_day.isoformat(),
                                "on_or_before": end_of_day.isoformat()
                            }
                        }
                    ]
                })
                logger.info(f"Querying for specific date: {specific_date} for db: {database_id} (force_pull: {force_pull}, content_type: {content_type})")
            except ValueError:
                logger.error(f"Invalid specific_date format: {specific_date}. Should be YYYY-MM-DD.")
                return []
        elif days is not None and days > 0:
            past_date = datetime.now() - timedelta(days=days)
            created_time_filter = {
                "timestamp": "created_time",
                "created_time": {"after": past_date.isoformat()}
            }
            last_edited_time_filter_for_days = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"after": past_date.isoformat()}
            }

            if use_last_sync_time and actual_last_sync_time:
                filters.append({
                    "or": [
                        created_time_filter, 
                        { 
                            "timestamp": "last_edited_time",
                            "last_edited_time": {"after": actual_last_sync_time.isoformat()}
                        }
                    ]
                })
                logger.info(f"Querying for last {days} days OR modified since last sync: {actual_last_sync_time.isoformat()} (force_pull: {force_pull}, content_type: {content_type})")
            else: 
                filters.append({
                    "or": [
                        created_time_filter,
                        last_edited_time_filter_for_days 
                    ]
                })
                logger.info(f"Querying for last {days} days (created or edited). (force_pull: {force_pull}, content_type: {content_type})")
        else: # Default: no days, no specific_date, not fetch_all - might mean fetch since last sync only
            if use_last_sync_time and actual_last_sync_time:
                 filters.append({
                    "timestamp": "last_edited_time",
                    "last_edited_time": {"after": actual_last_sync_time.isoformat()}
                })
                 logger.info(f"Querying for pages modified since last sync: {actual_last_sync_time.isoformat()} (force_pull: {force_pull}, content_type: {content_type})")
            elif not fetch_all: # Catch-all if no other conditions met and not fetch_all
                 logger.info(f"No specific date criteria, not fetching all, and no valid last_sync_time. Returning no pages. (force_pull: {force_pull}, content_type: {content_type})")
                 return []


    # Construct the final filter based on collected conditions
    final_filter = None
    if len(filters) == 1:
        final_filter = filters[0]
    elif len(filters) > 1:
        final_filter = {"and": filters}
    
    # If get_latest is true, we want to limit results to 1 after sorting.
    # We still fetch with default page_size to ensure the sort is applied over a reasonable set,
    # then take the first one. If performance becomes an issue, we could use page_size=1 with sort.
    page_size_to_use = 1 if get_latest else 100 # Use page_size 1 if get_latest

    all_pages = await query_database(
        database_id=database_id,
        filter_condition=final_filter,
        sort_condition=sorts, # Pass the sort condition here
        page_size=page_size_to_use # Use modified page_size
    )

    if get_latest:
        return all_pages[:1] # Return only the first item (or empty list if no results)
    else:
        return all_pages


async def get_pages_by_date_chunked(
    database_id: str,
    start_date: str,  # YYYY-MM-DD format
    end_date: str,    # YYYY-MM-DD format
    chunk_days: int = 3,  # Number of days per chunk (reduced from 7)
    max_pages_per_chunk: int = NOTION_MAX_CHUNK_SIZE,
    content_type: str = "journal"
) -> List[Dict[str, Any]]:
    """
    Retrieve pages from a Notion database using date chunking to avoid rate limits.
    
    This function splits the date range into smaller chunks and processes them sequentially
    to avoid hitting Notion's rate limits when dealing with large datasets.
    
    Args:
        database_id: The ID of the Notion database.
        start_date: Start date in YYYY-MM-DD format (inclusive).
        end_date: End date in YYYY-MM-DD format (inclusive).
        chunk_days: Number of days per chunk (default: 7).
        max_pages_per_chunk: Maximum pages to fetch per chunk (default: NOTION_MAX_CHUNK_SIZE).
        content_type: The type of content being fetched, for logging purposes.
        
    Returns:
        A list of page objects from Notion, sorted by last_edited_time descending.
    """
    try:
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        logger.error(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
        return []
    
    if start_date_obj > end_date_obj:
        logger.error(f"Start date {start_date} is after end date {end_date}")
        return []
    
    all_pages = []
    current_date = end_date_obj  # Start from most recent and work backwards
    total_chunks = 0
    
    logger.info(f"Starting chunked retrieval from {end_date} to {start_date} with {chunk_days}-day chunks")
    
    while current_date >= start_date_obj:
        chunk_start = max(current_date - timedelta(days=chunk_days - 1), start_date_obj)
        chunk_end = current_date
        
        total_chunks += 1
        
        logger.info(f"Processing chunk {total_chunks}: {chunk_start.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}")
        
        # Create date filter for this chunk
        chunk_start_iso = chunk_start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        chunk_end_iso = chunk_end.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
        
        filter_condition = {
            "or": [
                {
                    "timestamp": "created_time",
                    "created_time": {
                        "on_or_after": chunk_start_iso,
                        "on_or_before": chunk_end_iso
                    }
                },
                {
                    "timestamp": "last_edited_time",
                    "last_edited_time": {
                        "on_or_after": chunk_start_iso,
                        "on_or_before": chunk_end_iso
                    }
                }
            ]
        }
        
        # Sort by last_edited_time descending to get most recent first
        sort_condition = [{
            "timestamp": "last_edited_time",
            "direction": "descending"
        }]
        
        try:
            chunk_pages = await query_database(
                database_id=database_id,
                filter_condition=filter_condition,
                sort_condition=sort_condition,
                page_size=min(max_pages_per_chunk, 25)  # Force smaller page sizes
            )
            
            logger.info(f"Retrieved {len(chunk_pages)} pages from chunk {total_chunks}")
            all_pages.extend(chunk_pages)

            # Rate limiting is handled by the adaptive rate limiter in query_database

            # If we hit the max pages limit for this chunk, log a warning and suggest smaller chunks
            if len(chunk_pages) >= max_pages_per_chunk:
                logger.warning(f"Chunk {total_chunks} hit the max pages limit ({max_pages_per_chunk}). Consider using --chunk-days 1 for better rate limiting.")
            
        except Exception as e:
            logger.error(f"Error processing chunk {total_chunks} ({chunk_start.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}): {e}")
            # Continue with next chunk rather than failing completely
            continue
        
        # Move to the next chunk (going backwards in time)
        current_date = chunk_start - timedelta(days=1)
    
    # Sort all results by last_edited_time descending
    all_pages.sort(key=lambda x: x.get("last_edited_time", ""), reverse=True)
    
    logger.info(f"Completed chunked retrieval: {len(all_pages)} total pages from {total_chunks} chunks")
    return all_pages


async def get_pages_by_date_range(
    database_id: str,
    start_date: str,
    end_date: str,
    content_type: str = "journal",
    use_chunking: bool = True,
    chunk_days: int = 7
) -> List[Dict[str, Any]]:
    """
    Retrieve pages from a Notion database within a specific date range.
    
    Args:
        database_id: The ID of the Notion database.
        start_date: Start date in YYYY-MM-DD format (inclusive).
        end_date: End date in YYYY-MM-DD format (inclusive).
        content_type: The type of content being fetched.
        use_chunking: Whether to use date chunking to avoid rate limits.
        chunk_days: Number of days per chunk if using chunking.
        
    Returns:
        A list of page objects from Notion.
    """
    if use_chunking:
        return await get_pages_by_date_chunked(
            database_id, start_date, end_date, chunk_days, content_type=content_type
        )
    else:
        # Use the existing single-query method
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
            
            start_iso = start_date_obj.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            end_iso = end_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            
            filter_condition = {
                "or": [
                    {
                        "timestamp": "created_time",
                        "created_time": {
                            "on_or_after": start_iso,
                            "on_or_before": end_iso
                        }
                    },
                    {
                        "timestamp": "last_edited_time",
                        "last_edited_time": {
                            "on_or_after": start_iso,
                            "on_or_before": end_iso
                        }
                    }
                ]
            }
            
            sort_condition = [{
                "timestamp": "last_edited_time",
                "direction": "descending"
            }]
            
            return await query_database(
                database_id=database_id,
                filter_condition=filter_condition,
                sort_condition=sort_condition
            )
            
        except ValueError as e:
            logger.error(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
            return []


async def get_page_title(page_id: str) -> str:
    """
    Get the title of a Notion page.
    
    Args:
        page_id: ID of the Notion page
        
    Returns:
        Title of the page as a string
    """
    try:
        client = ensure_default_client()
        page = await client.pages.retrieve(page_id=page_id)
        
        # Try different common title property names
        title_property_names = ["Title", "Name", "title", "name"]
        
        for prop_name in title_property_names:
            title_property = page["properties"].get(prop_name, {})
            if title_property.get("type") == "title":
                title_objects = title_property.get("title", [])
                if title_objects:
                    title = title_objects[0].get("text", {}).get("content", "")
                    if title:
                        return title
        
        # If we couldn't find a title, try to get the first heading from the content
        blocks = await get_block_content(page_id)
        for block in blocks:
            if block["type"] in ["heading_1", "heading_2", "heading_3"]:
                heading_text = "".join([
                    span.get("text", {}).get("content", "")
                    for span in block[block["type"]].get("rich_text", [])
                ])
                if heading_text:
                    return heading_text
        
        # If all else fails, use the page ID as part of the title
        return f"Page {page_id[:8]}"
    except Exception as e:
        logger.error(f"Error getting page title: {str(e)}")
        return f"Untitled {page_id[:8]}"

async def get_block_content(block_id: str, last_edited_time: Optional[str] = None, use_persistent_cache: bool = True) -> List[Dict[str, Any]]:
    """
    Fetch all blocks from a page or block including nested blocks.

    Args:
        block_id: ID of the block or page (when called initially, this is the page_id)
        last_edited_time: Optional last edited timestamp for cache validation
        use_persistent_cache: If True, uses persistent SQLite cache (default: True)

    Returns:
        List of block objects with their content
    """
    # Check persistent cache first if enabled
    if use_persistent_cache and last_edited_time:
        persistent_cache = BlockCache()
        cached_blocks = persistent_cache.get_blocks(block_id, last_edited_time)
        if cached_blocks:
            persistent_cache.close()
            return cached_blocks
        persistent_cache.close()

    # Check in-memory cache
    if block_id in block_cache:
        return block_cache[block_id]

    page_id_for_logging = block_id # Keep the initial block_id as page_id for logging

    blocks = []
    has_more = True
    start_cursor = None

    while has_more:
        client = ensure_default_client()
        response = await client.blocks.children.list(
            block_id=block_id,
            start_cursor=start_cursor,
            page_size=100,  # Get maximum blocks per request
        )
        new_blocks = response["results"]
        blocks.extend(new_blocks)
        has_more = response["has_more"]
        if has_more:
            start_cursor = response["next_cursor"]

    # Process nested blocks recursively
    async def process_block(block, current_page_id: str): # Added current_page_id for logging
        block_type = block["type"]
        
        # Handle synced blocks
        if block_type == "synced_block":
            synced_from = block["synced_block"].get("synced_from")
            if synced_from is None:
                # This is an original synced block - process its children normally
                # If it has children, they will be processed by the has_children logic below
                pass
            else:
                # This is a duplicate synced block - get the original block's content
                original_block_id = synced_from["block_id"]
                try:
                    client = ensure_default_client()
                    original_block = await client.blocks.retrieve(block_id=original_block_id)
                    # Process the original block instead
                    return await process_block(original_block, current_page_id)
                except APIResponseError as e:
                    if e.status == 404:
                        logger.warning(f"WARNING: Original content for synced block ID {original_block_id} on page {current_page_id} could not be retrieved (404 Not Found). Replacing with placeholder.")
                        return {
                            "object": "block",
                            "id": block.get("id", "unknown_synced_block_id"), # Use ID of the current duplicate synced block
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{
                                    "type": "text",
                                    "text": {"content": f"[Content of synced block (original ID: {original_block_id}) unavailable due to API error: {e.code}. Please check Notion sharing permissions for the original block.]"},
                                    "annotations": {"italic": True}
                                }]
                            },
                            "has_children": False 
                        }
                    else:
                        logger.error(f"ERROR: APIResponseError (status {e.status}, code: {e.code}) retrieving original synced block ID {original_block_id} on page {current_page_id}. Re-raising.")
                        raise # Re-raise other API errors
                except Exception as e_general:
                     logger.error(f"ERROR: Unexpected error retrieving original synced block ID {original_block_id} on page {current_page_id}: {str(e_general)}. Re-raising.")
                     raise # Re-raise other unexpected errors
        
        if block.get("has_children"):
            try:
                # Get nested blocks directly
                # nested_blocks = [] # This line is not needed, asyncio.gather will create the list
                has_more_children = True
                start_cursor_children = None
                
                tasks = []
                
                while has_more_children:
                    client = ensure_default_client()
                    response_children = await client.blocks.children.list(
                        block_id=block["id"],
                        start_cursor=start_cursor_children,
                        page_size=100
                    )
                    new_nested_blocks = response_children["results"]
                    
                    for nested_block in new_nested_blocks:
                        tasks.append(process_block(nested_block, current_page_id)) # Pass current_page_id
                    
                    has_more_children = response_children["has_more"]
                    if has_more_children:
                        start_cursor_children = response_children["next_cursor"]
                
                # Wait for all tasks to complete
                if tasks:
                    block["children"] = await asyncio.gather(*tasks)
                else:
                    block["children"] = [] # Ensure 'children' key exists even if no tasks
            except Exception as e:
                logger.warning(f"WARNING: Error fetching nested blocks for block {block.get('id', 'unknown_id')} on page {current_page_id}: {str(e)}. Children may be incomplete.")
                block["children"] = [{ # Placeholder for children if error occurs
                    "object": "block",
                    "id": f"error_placeholder_children_of_{block.get('id', 'unknown_id')}",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": f"[Error fetching children for this block: {str(e)}]"},
                            "annotations": {"italic": True, "color": "red"}
                        }]
                    },
                    "has_children": False
                }]
        return block

    # Process all blocks that have children
    processed_blocks = []
    tasks = []
    for b in blocks: # renamed block to b to avoid conflict with the outer scope 'block' variable
        tasks.append(process_block(b, page_id_for_logging)) # Pass page_id_for_logging

    if tasks:
        processed_blocks = await asyncio.gather(*tasks)

    # Cache the processed blocks in memory
    block_cache[block_id] = processed_blocks

    # Cache in persistent storage if enabled
    if use_persistent_cache and last_edited_time:
        persistent_cache = BlockCache()
        persistent_cache.set_blocks(block_id, last_edited_time, processed_blocks)
        persistent_cache.close()

    return processed_blocks

def clear_block_cache():
    """Clear the block cache to free memory and ensure fresh data."""
    global block_cache
    block_cache = {}

async def update_webflow_id(page_id: str, webflow_id: Optional[str], property_name: str = "Webflow ID") -> bool:
    """
    Update the Webflow ID property of a Notion page.
    If webflow_id is None or empty, it will clear the property.
    
    Args:
        page_id: ID of the Notion page
        webflow_id: Webflow item ID to store, or None/empty to clear
        property_name: Name of the property to update (default: "Webflow ID")
        
    Returns:
        True if the update was successful, False otherwise
    """
    try:
        # Prepare the update payload
        if webflow_id and webflow_id.strip():
            # Set or update the Webflow ID
            update_data = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": webflow_id
                        }
                    }
                ]
            }
            logger.info(f"  ✓ Updated '{property_name}' in Notion to: {webflow_id}")
        else:
            # Clear the Webflow ID (by setting rich_text to an empty array)
            update_data = {"rich_text": []}
            logger.info(f"  ✓ Cleared '{property_name}' in Notion.")

        update_payload = {
            "properties": {
                property_name: update_data
            }
        }
        
        # Update the page
        client = ensure_default_client()
        await client.pages.update(
            page_id=page_id,
            **update_payload
        )
        
        return True
    except Exception as e:
        logger.error(f"  ✗ Error updating '{property_name}' in Notion for page {page_id}: {str(e)}")
        return False

async def get_pages_by_blog_status(database_id: str, status_property_name: str, target_statuses: List[str]):
    """
    Get all pages from the specified Notion database
    where the 'status_property_name' (assumed to be a 'status' type property) 
    is one of 'target_statuses'.

    Args:
        database_id: ID of the Notion database
        status_property_name: Name of the status property (e.g., "Blog Status")
        target_statuses: List of status values to filter for (e.g., ["To sync", "Update on sync"])

    Returns:
        List of page objects matching the criteria.
    """
    if not target_statuses:
        return []

    or_conditions = []
    for status_value in target_statuses: # Renamed 'status' to 'status_value' for clarity
        or_conditions.append({
            "property": status_property_name,
            "status": { # Changed from "select" to "status"
                "equals": status_value
            }
        })
    
    filter_condition = None
    if len(or_conditions) == 1:
        filter_condition = or_conditions[0]
    elif len(or_conditions) > 1:
        filter_condition = {"or": or_conditions}
    
    if not filter_condition:
        # If after all logic, filter_condition is still None (e.g. empty target_statuses initially),
        # return empty list to avoid error with query_database
        logger.info(f"No valid filter conditions derived from target_statuses: {target_statuses}. Returning empty list.")
        return []

    # Sort by last edited time descending to process most recent changes first
    sort_condition = [
        {
            "timestamp": "last_edited_time",
            "direction": "descending"
        }
    ]
        
    return await query_database(database_id, filter_condition, sort_condition)

async def update_page_blog_status(page_id: str, status_property_name: str, new_status: str) -> bool:
    """
    Update the 'Blog Status' (assumed to be a 'status' type property) of a Notion page.

    Args:
        page_id: ID of the Notion page
        status_property_name: Name of the status property to update (e.g., "Blog Status")
        new_status: The new status value (e.g., "Update on sync")

    Returns:
        True if the update was successful, False otherwise.
    """
    try:
        update_payload = {
            "properties": {
                status_property_name: {
                    "status": { # Changed from "select" to "status"
                        "name": new_status
                    }
                }
            }
        }
        client = ensure_default_client()
        await client.pages.update(page_id=page_id, **update_payload)
        logger.info(f"  ✓ Updated '{status_property_name}' to '{new_status}' for page {page_id}")
        return True
    except Exception as e:
        logger.error(f"  ✗ Error updating '{status_property_name}' for page {page_id}: {str(e)}")
        return False

async def update_page_properties_batch(page_id: str,
                                       status_property_name: Optional[str] = None,
                                       new_status: Optional[str] = None,
                                       webflow_id_property_name: Optional[str] = None,
                                       webflow_id: Optional[str] = None) -> bool:
    """
    Update multiple properties of a Notion page in a single API call.
    This is more efficient than making separate calls for each property.

    Args:
        page_id: ID of the Notion page
        status_property_name: Name of the status property (if updating)
        new_status: New status value (if updating status)
        webflow_id_property_name: Name of the Webflow ID property (if updating)
        webflow_id: Webflow item ID to store, or None/empty to clear (if updating)

    Returns:
        True if the update was successful, False otherwise.
    """
    try:
        properties = {}

        # Add status property if specified
        if status_property_name and new_status:
            properties[status_property_name] = {
                "status": {
                    "name": new_status
                }
            }

        # Add webflow_id property if specified
        if webflow_id_property_name:
            if webflow_id and webflow_id.strip():
                properties[webflow_id_property_name] = {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": webflow_id
                            }
                        }
                    ]
                }
            else:
                # Clear the Webflow ID
                properties[webflow_id_property_name] = {"rich_text": []}

        if not properties:
            logger.warning(f"No properties to update for page {page_id}")
            return False

        update_payload = {"properties": properties}

        client = ensure_default_client()
        await client.pages.update(page_id=page_id, **update_payload)

        logger.info(f"  ✓ Batch updated properties for page {page_id}")
        return True
    except Exception as e:
        logger.error(f"  ✗ Error batch updating properties for page {page_id}: {str(e)}")
        return False

async def get_page_property(page_id: str, property_name: str) -> Any:
    """
    Retrieve a specific property value from a Notion page.

    Args:
        page_id: ID of the Notion page.
        property_name: The name of the property to retrieve.

    Returns:
        The value of the property, or None if not found or an error occurs.
    """
    try:
        client = ensure_default_client()
        page = await client.pages.retrieve(page_id=page_id)
        prop_data = page.get("properties", {}).get(property_name)

        if not prop_data:
            logger.info(f"Property '{property_name}' not found on page {page_id}")
            return None

        prop_type = prop_data.get("type")

        if prop_type == "select":
            return prop_data.get("select", {}).get("name")
        elif prop_type == "status": # Added handling for "status" type
            return prop_data.get("status", {}).get("name")
        elif prop_type == "rich_text":
            return "".join([rt.get("plain_text", "") for rt in prop_data.get("rich_text", [])])
        elif prop_type == "title":
            return "".join([rt.get("plain_text", "") for rt in prop_data.get("title", [])])
        elif prop_type == "checkbox":
            return prop_data.get("checkbox")
        elif prop_type == "date":
            return prop_data.get("date", {}).get("start")
        # Add more property types as needed
        else:
            logger.warning(f"Property type '{prop_type}' for '{property_name}' not fully handled yet.")
            return prop_data # Return raw data for unhandled types

    except Exception as e:
        logger.error(f"Error retrieving property '{property_name}' for page {page_id}: {str(e)}")
        return None

async def get_pages_by_properties(database_id: str, properties_filter: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get pages from a Notion database that match specific property values.

    Args:
        database_id: ID of the Notion database.
        properties_filter: A dictionary where keys are property names and values are the desired property values.
                           Example: {"Status": "Done", "Priority": "High"}

    Returns:
        List of page objects matching the filter, or an empty list if error or no pages found.
    """
    try:
        client = ensure_default_client()
    except Exception as e:
        logger.error(f"Failed to initialize Notion client: {e}")
        return []

    # Construct the filter conditions based on the properties_filter dictionary
    filter_conditions = []
    for prop_name, prop_value in properties_filter.items():
        # Heuristic to determine property type based on value or common names
        # This is a simplification; a more robust solution might need property type info from Notion
        if isinstance(prop_value, bool) or prop_name.lower() in ["sync", "reference", "done"]:
            filter_conditions.append({
                "property": prop_name,
                "checkbox": {
                    "equals": prop_value
                }
            })
        elif prop_name.lower().endswith("status"):
             filter_conditions.append({
                "property": prop_name,
                "status": {
                    "equals": prop_value
                }
            })
        elif isinstance(prop_value, str) and any(kw in prop_name.lower() for kw in ["select", "tag", "category"]):
             filter_conditions.append({
                "property": prop_name,
                "select": {
                    "equals": prop_value
                }
            })
        # Add more type handlers as needed (e.g., date, number, multi_select, title, rich_text)
        else:
            # Default to rich_text or title for simple string matches if unsure
            # This might not be correct for all cases and is a placeholder
            filter_conditions.append({
                "property": prop_name,
                "rich_text": { # Or "title" depending on the property
                    "equals": str(prop_value) 
                }
            })
            logger.warning(f"Warning: Property '{prop_name}' type for filtering is assumed. Review filter construction if results are unexpected.")

    query_filter = {}
    if len(filter_conditions) == 1:
        query_filter = filter_conditions[0]
    elif len(filter_conditions) > 1:
        query_filter = {"and": filter_conditions}
    
    if not query_filter: # No valid filters constructed
        logger.info("No valid filter conditions provided for get_pages_by_properties.")
        return []
        
    logger.debug(f"Querying Notion database {database_id} with filter: {json.dumps(query_filter, indent=2)}")

    try:
        all_pages = []
        start_cursor = None
        while True:
            response = await client.databases.query(
                database_id=database_id,
                filter=query_filter,
                start_cursor=start_cursor,
                page_size=100
            )
            all_pages.extend(response.get("results", []))
            start_cursor = response.get("next_cursor")
            if not start_cursor:
                break
        return all_pages
    except Exception as e:
        logger.error(f"Error querying Notion database {database_id} by properties: {e}")
        # Log the specific filter that caused the error if possible
        logger.debug(f"Filter that caused error: {json.dumps(query_filter, indent=2)}")
        return []


async def detect_child_pages_in_blocks(blocks: List[Dict[str, Any]], parent_page_id: str) -> List[str]:
    """
    Recursively scans blocks to find 'child_page' blocks and page mentions that are
    true children of the parent page.

    A mentioned page is considered a "true child" if its parent is a block that
    itself is a child of `parent_page_id`. This distinguishes sub-pages from
    cross-references to other pages.

    Args:
        blocks: A list of Notion block objects to scan.
        parent_page_id: The ID of the page that contains these blocks.

    Returns:
        A list of unique page IDs that are confirmed children.
    """
    child_page_ids = set()
    client = ensure_default_client()

    # Memoization cache for block parents to reduce API calls
    block_parent_cache = {}

    async def get_block_parent(block_id: str) -> Optional[Dict[str, Any]]:
        if block_id in block_parent_cache:
            return block_parent_cache[block_id]
        try:
            response = await client.blocks.retrieve(block_id=block_id)
            parent = response.get("parent")
            block_parent_cache[block_id] = parent
            return parent
        except APIResponseError as e:
            if e.code == "object_not_found":
                logger.warning(f"Could not find block {block_id} to check its parent.")
            else:
                logger.error(f"API error checking parent of block {block_id}: {e}")
            block_parent_cache[block_id] = None
            return None

    async def scan_block(block: Dict[str, Any]):
        block_id = block.get("id")
        block_type = block.get("type")

        # 1. Direct child_page blocks are always true children
        if block_type == "child_page":
            child_page_ids.add(block["id"])

        # 2. Check for page mentions in rich text
        content = block.get(block_type, {})
        rich_text_fields = ["rich_text", "caption"]
        for field in rich_text_fields:
            if field in content:
                for text_obj in content[field]:
                    if text_obj.get("type") == "mention":
                        mention = text_obj.get("mention", {})
                        if mention.get("type") == "page":
                            mentioned_page_id = mention.get("page", {}).get("id")
                            if mentioned_page_id:
                                # Retrieve the mentioned page to check its parent
                                try:
                                    page_response = await client.pages.retrieve(page_id=mentioned_page_id)
                                    page_parent = page_response.get("parent", {})

                                    # If the parent is a block, it's a sub-page.
                                    # We must confirm that block lives on our `parent_page_id`.
                                    if page_parent.get("type") == "block_id":
                                        parent_block_id = page_parent.get("block_id")
                                        # Check if the block containing the mention is the direct parent
                                        if parent_block_id == block_id:
                                             child_page_ids.add(mentioned_page_id)
                                        else:
                                             # Fallback: check if the parent block belongs to the main page
                                             grandparent = await get_block_parent(parent_block_id)
                                             if grandparent and grandparent.get("type") == "page_id" and grandparent.get("page_id") == parent_page_id:
                                                 child_page_ids.add(mentioned_page_id)

                                except APIResponseError as e:
                                    if e.code == "object_not_found":
                                        logger.warning(f"Mentioned page {mentioned_page_id} not found.")
                                    else:
                                        logger.error(f"API error checking parent of mentioned page {mentioned_page_id}: {e}")

        # 3. Recurse on children
        if block.get("children"):
            for child_block in block["children"]:
                await scan_block(child_block)

    # Start scanning from the top-level blocks
    for block in blocks:
        await scan_block(block)

    unique_ids = list(child_page_ids)
    if unique_ids:
        logger.info(f"Detected {len(unique_ids)} true child pages for parent {parent_page_id}.")
    return unique_ids


async def fetch_sub_page_content(page_id: str, 
                                depth: int = 0, 
                                max_depth: int = MAX_SUBPAGE_DEPTH,
                                visited_pages: Optional[set] = None) -> Dict[str, Any]:
    """
    Fetch a sub-page and its content, including nested sub-pages.
    
    Args:
        page_id: ID of the page to fetch
        depth: Current recursion depth
        max_depth: Maximum recursion depth to prevent infinite loops
        visited_pages: Set of page IDs already visited (for circular reference detection)
        
    Returns:
        Dictionary containing page data with nested sub-pages
    """
    if visited_pages is None:
        visited_pages = set()
    
    # Prevent circular references
    if page_id in visited_pages:
        logger.warning(f"Circular reference detected: page {page_id} already visited")
        return {
            "id": page_id,
            "title": f"[Circular Reference: {page_id[:8]}]",
            "content": "[Circular reference detected - content not fetched]",
            "sub_pages": [],
            "error": "circular_reference"
        }
    
    # Prevent excessive recursion
    if depth >= max_depth:
        logger.warning(f"Maximum sub-page depth ({max_depth}) reached for page {page_id}")
        return {
            "id": page_id,
            "title": f"[Max Depth Reached: {page_id[:8]}]",
            "content": "[Maximum recursion depth reached - content not fetched]",
            "sub_pages": [],
            "error": "max_depth_reached"
        }
    
    visited_pages.add(page_id)

    try:
        # Rate limiting is handled by the adaptive rate limiter in API calls

        logger.debug(f"Fetching sub-page content: {page_id} (depth: {depth})")
        
        # Fetch page title
        title = await get_page_title(page_id)
        
        # Fetch page blocks
        blocks = await get_block_content(page_id)
        
        # Convert blocks to markdown
        from promaia.markdown.converter import page_to_markdown
        content = page_to_markdown(blocks)
        
        # Look for child pages in the blocks
        child_page_ids = await detect_child_pages_in_blocks(blocks, page_id)
        
        # Recursively fetch child pages
        sub_pages = []
        if child_page_ids and SUBPAGE_SYNC_ENABLED:
            logger.info(f"Fetching {len(child_page_ids)} child pages for {page_id} (depth: {depth})")
            
            for child_page_id in child_page_ids:
                try:
                    child_page_data = await fetch_sub_page_content(
                        child_page_id, 
                        depth + 1, 
                        max_depth, 
                        visited_pages.copy()  # Pass a copy to avoid shared state issues
                    )
                    sub_pages.append(child_page_data)
                except Exception as e:
                    logger.error(f"Error fetching child page {child_page_id}: {e}")
                    # Add placeholder for failed child page
                    sub_pages.append({
                        "id": child_page_id,
                        "title": f"[Error: {child_page_id[:8]}]",
                        "content": f"[Error fetching child page: {str(e)}]",
                        "sub_pages": [],
                        "error": str(e)
                    })
        
        return {
            "id": page_id,
            "title": title,
            "content": content,
            "sub_pages": sub_pages,
            "depth": depth,
            "child_count": len(child_page_ids)
        }
        
    except Exception as e:
        logger.error(f"Error fetching sub-page {page_id} at depth {depth}: {e}")
        return {
            "id": page_id,
            "title": f"[Error: {page_id[:8]}]",
            "content": f"[Error fetching page content: {str(e)}]",
            "sub_pages": [],
            "error": str(e),
            "depth": depth
        }
    finally:
        visited_pages.discard(page_id)


async def get_page_with_sub_pages(page_id: str, 
                                 include_sub_pages: bool = True,
                                 max_depth: int = MAX_SUBPAGE_DEPTH) -> Dict[str, Any]:
    """
    Fetch a page and optionally its sub-pages recursively.
    
    Args:
        page_id: ID of the main page to fetch
        include_sub_pages: Whether to recursively fetch sub-pages
        max_depth: Maximum depth for sub-page recursion
        
    Returns:
        Dictionary containing the page and all its sub-pages
    """
    logger.info(f"Fetching page {page_id} with sub-pages: {include_sub_pages}")
    
    if not include_sub_pages:
        # Just fetch the main page without sub-pages
        try:
            title = await get_page_title(page_id)
            blocks = await get_block_content(page_id)
            from promaia.markdown.converter import page_to_markdown
            content = page_to_markdown(blocks)
            
            return {
                "id": page_id,
                "title": title,
                "content": content,
                "sub_pages": [],
                "depth": 0,
                "child_count": 0
            }
        except Exception as e:
            logger.error(f"Error fetching page {page_id}: {e}")
            return {
                "id": page_id,
                "title": f"[Error: {page_id[:8]}]",
                "content": f"[Error fetching page: {str(e)}]",
                "sub_pages": [],
                "error": str(e),
                "depth": 0
            }
    
    # Fetch page with sub-pages
    return await fetch_sub_page_content(page_id, 0, max_depth)


def format_page_with_sub_pages(page_data: Dict[str, Any], 
                              include_sub_pages_in_content: bool = True) -> str:
    """
    Format page data with sub-pages into a single markdown document.
    
    Args:
        page_data: Page data dictionary from get_page_with_sub_pages
        include_sub_pages_in_content: Whether to include sub-page content in the output
        
    Returns:
        Formatted markdown content
    """
    def format_page_recursive(page: Dict[str, Any], level: int = 0) -> str:
        """Recursively format a page and its sub-pages."""
        content = ""
        indent = "#" * max(1, level + 1)  # Ensure at least one #
        
        # Add page title as heading
        title = page.get("title", "Untitled")
        content += f"{indent} {title}\n\n"
        
        # Add page content
        page_content = page.get("content", "")
        if page_content and page_content.strip():
            content += f"{page_content}\n\n"
        
        # Add error information if present
        if page.get("error"):
            content += f"*Error: {page['error']}*\n\n"
        
        # Add sub-pages if enabled
        if include_sub_pages_in_content:
            sub_pages = page.get("sub_pages", [])
            if sub_pages:
                content += f"## Sub-pages ({len(sub_pages)})\n\n"
                for sub_page in sub_pages:
                    content += format_page_recursive(sub_page, level + 1)
        
        return content
    
    return format_page_recursive(page_data)
