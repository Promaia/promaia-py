"""
Router for journal-related operations that orchestrates the flow between modules.
"""
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, AsyncIterator
import asyncio
import traceback
import logging

from promaia.notion.pages import get_pages_by_date, get_pages_by_date_range, get_page_title, get_block_content, clear_block_cache
from promaia.notion.pages import detect_child_pages_in_blocks, get_page_with_sub_pages, format_page_with_sub_pages, SUBPAGE_SYNC_ENABLED
from promaia.markdown.converter import page_to_markdown
from promaia.storage.files import save_page_to_file, get_journal_entry_filepath
# Summarize functionality has been deprecated/removed
# from promaia.summarize.interface import summarize_journal_entry, save_summary_entry, should_update_entry

# Stub functions to replace removed summarize functionality
async def summarize_journal_entry(content, title):
    """Deprecated: summarize functionality removed."""
    return None

def save_summary_entry(content, title):
    """Deprecated: summarize functionality removed."""
    return None

def should_update_entry(filepath, last_edited_dt):
    """Deprecated: Always return True to process entries."""
    return True
from promaia.utils.ai import debug_print

logger = logging.getLogger(__name__)

async def fetch_journal_pages_content(
    pages_metadata: List[Dict[str, Any]]
) -> AsyncIterator[Dict[str, Any]]:
    """
    For a given list of page metadata objects from Notion, 
    fetches the full block content for each page and yields it one by one along with title.
        
    Yields:
        Page data dictionary: {'id', 'title', 'content', 'last_edited_time'}
    """
    if not pages_metadata:
        logger.info("No page metadata provided to fetch_journal_pages_content. Nothing to do.")
        return

    # Deduplicate pages by ID to avoid processing the same page multiple times
    seen_page_ids = set()
    unique_pages = []
    for page_meta in pages_metadata:
        page_id = page_meta["id"]
        if page_id not in seen_page_ids:
            seen_page_ids.add(page_id)
            unique_pages.append(page_meta)
        else:
            debug_print(f"DEBUG: Skipping duplicate page ID: {page_id}")
    
    if len(unique_pages) < len(pages_metadata):
        logger.info(f"Deduplicated {len(pages_metadata)} pages to {len(unique_pages)} unique pages")

    for i, page_meta in enumerate(unique_pages, 1):
        page_id = page_meta["id"]
        notion_last_edited_time = page_meta.get("last_edited_time")
        debug_print(f"DEBUG: [{i}/{len(unique_pages)}] Fetching content for page: {page_id}...")
        
        try:
            clear_block_cache()
            
            # Add rate limiting between individual page fetches
            if i > 1:  # Don't delay before the first page
                await asyncio.sleep(0.3)  # 300ms delay between page fetches
            
            title = await get_page_title(page_id)
            
            # Add another small delay before fetching blocks
            await asyncio.sleep(0.1)
            
            blocks = await get_block_content(page_id)
            markdown_content = page_to_markdown(blocks)
            debug_print(f"DEBUG: [{i}/{len(unique_pages)}] Content fetched and converted for page: {page_id} (Title: {title})")
            
            yield {
                "id": page_id,
                "title": title,
                "content": markdown_content,
                "last_edited_time": notion_last_edited_time
            }
            
        except Exception as e:
            logger.error(f"Error fetching content for page {page_id}: {e}")
            # Yield a placeholder to avoid breaking the processing flow
            yield {
                "id": page_id,
                "title": f"Error fetching page {page_id[:8]}",
                "content": f"Error fetching content: {str(e)}",
                "last_edited_time": notion_last_edited_time
            }


async def fetch_journal_pages_content_with_sub_pages(
    pages_metadata: List[Dict[str, Any]],
    include_sub_pages: bool = True,
    max_sub_page_depth: int = 3
) -> AsyncIterator[Dict[str, Any]]:
    """
    Enhanced version of fetch_journal_pages_content that optionally includes sub-pages.
    
    For a given list of page metadata objects from Notion, 
    fetches the full block content for each page and optionally its sub-pages.
        
    Args:
        pages_metadata: List of page metadata objects from Notion
        include_sub_pages: Whether to recursively fetch sub-pages
        max_sub_page_depth: Maximum depth for sub-page recursion
        
    Yields:
        Page data dictionary: {'id', 'title', 'content', 'last_edited_time', 'sub_pages', 'total_pages'}
    """
    if not pages_metadata:
        logger.info("No page metadata provided to fetch_journal_pages_content_with_sub_pages. Nothing to do.")
        return

    # Deduplicate pages by ID to avoid processing the same page multiple times
    seen_page_ids = set()
    unique_pages = []
    for page_meta in pages_metadata:
        page_id = page_meta["id"]
        if page_id not in seen_page_ids:
            seen_page_ids.add(page_id)
            unique_pages.append(page_meta)
        else:
            debug_print(f"DEBUG: Skipping duplicate page ID: {page_id}")
    
    if len(unique_pages) < len(pages_metadata):
        logger.info(f"Deduplicated {len(pages_metadata)} pages to {len(unique_pages)} unique pages")

    # Track sub-page statistics
    total_sub_pages_fetched = 0
    pages_with_sub_pages = 0

    for i, page_meta in enumerate(unique_pages, 1):
        page_id = page_meta["id"]
        notion_last_edited_time = page_meta.get("last_edited_time")
        
        if include_sub_pages and SUBPAGE_SYNC_ENABLED:
            logger.info(f"[{i}/{len(unique_pages)}] Fetching page with sub-pages: {page_id}...")
        else:
            debug_print(f"DEBUG: [{i}/{len(unique_pages)}] Fetching content for page: {page_id}...")
        
        try:
            clear_block_cache()
            
            # Add rate limiting between individual page fetches
            if i > 1:  # Don't delay before the first page
                await asyncio.sleep(0.3)  # 300ms delay between page fetches
            
            if include_sub_pages and SUBPAGE_SYNC_ENABLED:
                # Fetch page with sub-pages using the new functionality
                page_data = await get_page_with_sub_pages(
                    page_id, 
                    include_sub_pages=True,
                    max_depth=max_sub_page_depth
                )
                
                title = page_data.get("title", f"Untitled {page_id[:8]}")
                
                # Format the page with sub-pages into markdown
                markdown_content = format_page_with_sub_pages(
                    page_data, 
                    include_sub_pages_in_content=True
                )
                
                # Count sub-pages
                sub_page_count = page_data.get("child_count", 0)
                if sub_page_count > 0:
                    pages_with_sub_pages += 1
                    total_sub_pages_fetched += sub_page_count
                    logger.info(f"  ✓ Fetched {sub_page_count} sub-pages for {title}")
                
                # Additional metadata for sub-page sync
                additional_data = {
                    "sub_pages_count": sub_page_count,
                    "max_depth_reached": any(
                        sub_page.get("error") == "max_depth_reached" 
                        for sub_page in page_data.get("sub_pages", [])
                    ),
                    "has_errors": any(
                        sub_page.get("error") 
                        for sub_page in page_data.get("sub_pages", [])
                    )
                }
                
            else:
                # Standard fetching without sub-pages
                title = await get_page_title(page_id)
                
                # Add another small delay before fetching blocks
                await asyncio.sleep(0.1)
                
                blocks = await get_block_content(page_id)
                markdown_content = page_to_markdown(blocks)
                
                additional_data = {
                    "sub_pages_count": 0,
                    "max_depth_reached": False,
                    "has_errors": False
                }
            
            debug_print(f"DEBUG: [{i}/{len(unique_pages)}] Content fetched and converted for page: {page_id} (Title: {title})")
            
            yield {
                "id": page_id,
                "title": title,
                "content": markdown_content,
                "last_edited_time": notion_last_edited_time,
                **additional_data
            }
            
        except Exception as e:
            logger.error(f"Error fetching content for page {page_id}: {e}")
            # Yield a placeholder to avoid breaking the processing flow
            yield {
                "id": page_id,
                "title": f"Error fetching page {page_id[:8]}",
                "content": f"Error fetching content: {str(e)}",
                "last_edited_time": notion_last_edited_time,
                "sub_pages_count": 0,
                "max_depth_reached": False,
                "has_errors": True
            }
    
    # Log sub-page statistics
    if include_sub_pages and SUBPAGE_SYNC_ENABLED:
        logger.info(f"Sub-page sync summary: {total_sub_pages_fetched} sub-pages from {pages_with_sub_pages} pages")


async def _save_original_and_optionally_summarize_page_content(
    page_id: str, 
    title: str, 
    content: str, 
    summarize_flag: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """
    Saves the original journal content and, if summarize_flag is True, its summary.
    Returns filepaths of (original_journal_file, summary_file). summary_file can be None.
    """
    journal_filepath = None
    summary_filepath = None

    journal_filepath = await save_page_to_file(page_id, title, content, "journal")
    
    if summarize_flag:
        debug_print(f"DEBUG: Summarizing page {page_id} (Title: {title})...")
        summarized_content = await summarize_journal_entry(content, title)
        summary_filepath = save_summary_entry(summarized_content, title)
        debug_print(f"DEBUG: Summary saved to {summary_filepath}")
    else:
        debug_print(f"DEBUG: Skipping summarization for page {page_id} (Title: {title}) as per flag.")
            
    return journal_filepath, summary_filepath

async def handle_journal_pull(
    database_id: str, 
    days: Optional[int] = None, 
    specific_date: Optional[str] = None, 
    fetch_all: bool = False, 
    force_pull: bool = False,
    summarize_flag: bool = False
) -> Tuple[List[str], List[str], int, int, int]:
    """
    Handle the complete journal pull operation, processing pages one by one.
    Args:
        database_id: Notion database ID
        days: Number of days to look back
        specific_date: Specific date (YYYY-MM-DD) to fetch
        fetch_all: Whether to fetch all pages
        force_pull: If True, ignore last sync time for fetching.
        summarize_flag: If True, also generate and save summaries.
        
    Returns:
        Tuple of (list_of_journal_filepaths, list_of_summary_filepaths, saved_originals_count, saved_summaries_count, skipped_originals_count)
    """
    all_journal_filepaths = []
    all_summary_filepaths = []
    processed_count = 0
    saved_originals_count = 0
    saved_summaries_count = 0
    skipped_originals_count = 0
    total_pages_to_fetch = 0

    pages_metadata_to_process = await get_pages_by_date(
        database_id,
        days=days,
        specific_date=specific_date,
        fetch_all=fetch_all,
        force_pull=force_pull,
        content_type='journal'
    )
    total_pages_to_fetch = len(pages_metadata_to_process)

    if total_pages_to_fetch == 0:
        logger.info("No journal pages found matching date criteria.")
        return [], [], 0, 0, 0
        
    logger.info(f"Found {total_pages_to_fetch} journal page(s) matching date criteria. Starting full content pull & processing...")

    async for page_data in fetch_journal_pages_content(pages_metadata_to_process):
        processed_count += 1
        page_id = page_data["id"]
        title = page_data["title"]
        content = page_data["content"]
        notion_last_edited_time = page_data.get("last_edited_time")
        notion_last_edited_dt = None
        if notion_last_edited_time:
            try:
                notion_last_edited_dt = datetime.fromisoformat(notion_last_edited_time.replace("Z", "+00:00"))
            except ValueError:
                debug_print(f"DEBUG: Could not parse last_edited_time '{notion_last_edited_time}' for page {page_id}")

        potential_journal_filepath = get_journal_entry_filepath(title, page_id)

        proceed_with_saving_original = True
        if os.path.exists(potential_journal_filepath) and notion_last_edited_dt and not force_pull:
            if not should_update_entry(potential_journal_filepath, notion_last_edited_dt):
                proceed_with_saving_original = False
                skipped_originals_count += 1
                logger.info(f"[{processed_count}/{total_pages_to_fetch}] Skipping unmodified: {title} ({page_id})")
            else:
                debug_print(f"DEBUG: Original file {potential_journal_filepath} needs update.")
        elif not notion_last_edited_dt:
             debug_print(f"DEBUG: No last_edited_time from Notion for {page_id}, will save/overwrite.")
        elif force_pull:
            debug_print(f"DEBUG: Force pull is enabled, processing regardless of modification time: {page_id}")
        else:
            debug_print(f"DEBUG: Original file {potential_journal_filepath} does not exist, will save.")

        if proceed_with_saving_original:
            logger.info(f"[{processed_count}/{total_pages_to_fetch}] Processing: {title} ({page_id}) | Summarize: {summarize_flag}")
            try:
                journal_fp, summary_fp = await _save_original_and_optionally_summarize_page_content(
                    page_id, title, content,
                    summarize_flag=summarize_flag
                )
                
                if journal_fp:
                    all_journal_filepaths.append(journal_fp)
                    saved_originals_count += 1
                    debug_print(f"DEBUG: Original saved: {journal_fp}")

                if summary_fp:
                    all_summary_filepaths.append(summary_fp)
                    saved_summaries_count += 1
                    debug_print(f"DEBUG: Summary saved: {summary_fp}")
                
            except Exception as e:
                logger.error(f"Failed to process page {page_id} (Title: {title}): {e}")
                if os.getenv("MAIA_DEBUG") == "1":
                    traceback.print_exc()

    return all_journal_filepaths, all_summary_filepaths, saved_originals_count, saved_summaries_count, skipped_originals_count


async def handle_journal_pull_with_sub_pages(
    database_id: str, 
    days: Optional[int] = None, 
    specific_date: Optional[str] = None, 
    fetch_all: bool = False, 
    force_pull: bool = False,
    summarize_flag: bool = False,
    include_sub_pages: bool = True,
    max_sub_page_depth: int = 3
) -> Tuple[List[str], List[str], int, int, int]:
    """
    Enhanced journal pull operation that supports sub-page syncing.
    
    Args:
        database_id: Notion database ID
        days: Number of days to look back
        specific_date: Specific date (YYYY-MM-DD) to fetch
        fetch_all: Whether to fetch all pages
        force_pull: If True, ignore last sync time for fetching
        summarize_flag: If True, also generate and save summaries
        include_sub_pages: Whether to recursively fetch sub-pages
        max_sub_page_depth: Maximum depth for sub-page recursion
        
    Returns:
        Tuple of (list_of_journal_filepaths, list_of_summary_filepaths, saved_originals_count, saved_summaries_count, skipped_originals_count)
    """
    all_journal_filepaths = []
    all_summary_filepaths = []
    processed_count = 0
    saved_originals_count = 0
    saved_summaries_count = 0
    skipped_originals_count = 0
    total_sub_pages_processed = 0

    pages_metadata_to_process = await get_pages_by_date(
        database_id,
        days=days,
        specific_date=specific_date,
        fetch_all=fetch_all,
        force_pull=force_pull,
        content_type='journal'
    )
    total_pages_to_fetch = len(pages_metadata_to_process)

    if total_pages_to_fetch == 0:
        logger.info("No journal pages found matching date criteria.")
        return [], [], 0, 0, 0
        
    if include_sub_pages and SUBPAGE_SYNC_ENABLED:
        logger.info(f"Found {total_pages_to_fetch} journal page(s) matching date criteria. Starting enhanced sync with sub-pages (max depth: {max_sub_page_depth})...")
    else:
        logger.info(f"Found {total_pages_to_fetch} journal page(s) matching date criteria. Starting standard sync (sub-pages disabled)...")

    async for page_data in fetch_journal_pages_content_with_sub_pages(
        pages_metadata_to_process,
        include_sub_pages=include_sub_pages,
        max_sub_page_depth=max_sub_page_depth
    ):
        processed_count += 1
        page_id = page_data["id"]
        title = page_data["title"]
        content = page_data["content"]
        notion_last_edited_time = page_data.get("last_edited_time")
        sub_pages_count = page_data.get("sub_pages_count", 0)
        has_errors = page_data.get("has_errors", False)
        
        # Track sub-page statistics
        total_sub_pages_processed += sub_pages_count
        
        notion_last_edited_dt = None
        if notion_last_edited_time:
            try:
                notion_last_edited_dt = datetime.fromisoformat(notion_last_edited_time.replace("Z", "+00:00"))
            except ValueError:
                debug_print(f"DEBUG: Could not parse last_edited_time '{notion_last_edited_time}' for page {page_id}")

        potential_journal_filepath = get_journal_entry_filepath(title, page_id)

        proceed_with_saving_original = True
        if os.path.exists(potential_journal_filepath) and notion_last_edited_dt and not force_pull:
            if not should_update_entry(potential_journal_filepath, notion_last_edited_dt):
                proceed_with_saving_original = False
                skipped_originals_count += 1
                sub_page_info = f" (+{sub_pages_count} sub-pages)" if sub_pages_count > 0 else ""
                logger.info(f"[{processed_count}/{total_pages_to_fetch}] Skipping unmodified: {title} ({page_id}){sub_page_info}")
            else:
                debug_print(f"DEBUG: Original file {potential_journal_filepath} needs update.")
        elif not notion_last_edited_dt:
             debug_print(f"DEBUG: No last_edited_time from Notion for {page_id}, will save/overwrite.")
        elif force_pull:
            debug_print(f"DEBUG: Force pull is enabled, processing regardless of modification time: {page_id}")
        else:
            debug_print(f"DEBUG: Original file {potential_journal_filepath} does not exist, will save.")

        if proceed_with_saving_original:
            sub_page_info = f" (+{sub_pages_count} sub-pages)" if sub_pages_count > 0 else ""
            error_info = " [with errors]" if has_errors else ""
            logger.info(f"[{processed_count}/{total_pages_to_fetch}] Processing: {title} ({page_id}){sub_page_info}{error_info} | Summarize: {summarize_flag}")
            
            try:
                journal_fp, summary_fp = await _save_original_and_optionally_summarize_page_content(
                    page_id, title, content,
                    summarize_flag=summarize_flag
                )
                
                if journal_fp:
                    all_journal_filepaths.append(journal_fp)
                    saved_originals_count += 1
                    debug_print(f"DEBUG: Original saved: {journal_fp}")

                if summary_fp:
                    all_summary_filepaths.append(summary_fp)
                    saved_summaries_count += 1
                    debug_print(f"DEBUG: Summary saved: {summary_fp}")
                
            except Exception as e:
                logger.error(f"Failed to process page {page_id} (Title: {title}): {e}")
                if os.getenv("MAIA_DEBUG") == "1":
                    traceback.print_exc()

    # Log final statistics
    if include_sub_pages and SUBPAGE_SYNC_ENABLED:
        logger.info(f"Sub-page sync completed: processed {total_sub_pages_processed} sub-pages across {processed_count} main pages")

    return all_journal_filepaths, all_summary_filepaths, saved_originals_count, saved_summaries_count, skipped_originals_count


async def handle_journal_pull_date_range(
    database_id: str,
    start_date: str,  # YYYY-MM-DD format
    end_date: str,    # YYYY-MM-DD format
    use_chunking: bool = True,
    chunk_days: int = 7,
    force_pull: bool = False,
    summarize_flag: bool = False
) -> Tuple[List[str], List[str], int, int, int]:
    """
    Handle journal pull operation for a specific date range with optional chunking.
    
    Args:
        database_id: Notion database ID
        start_date: Start date in YYYY-MM-DD format (inclusive)
        end_date: End date in YYYY-MM-DD format (inclusive)
        use_chunking: Whether to use date chunking to avoid rate limits
        chunk_days: Number of days per chunk if using chunking
        force_pull: If True, ignore modification times and always process
        summarize_flag: If True, also generate and save summaries
        
    Returns:
        Tuple of (list_of_journal_filepaths, list_of_summary_filepaths, saved_originals_count, saved_summaries_count, skipped_originals_count)
    """
    all_journal_filepaths = []
    all_summary_filepaths = []
    processed_count = 0
    saved_originals_count = 0
    saved_summaries_count = 0
    skipped_originals_count = 0
    
    logger.info(f"Starting date range journal pull from {start_date} to {end_date} (chunking: {use_chunking})")
    
    try:
        pages_metadata_to_process = await get_pages_by_date_range(
            database_id=database_id,
            start_date=start_date,
            end_date=end_date,
            content_type='journal',
            use_chunking=use_chunking,
            chunk_days=chunk_days
        )
        
        total_pages_to_fetch = len(pages_metadata_to_process)
        
        if total_pages_to_fetch == 0:
            logger.info(f"No journal pages found in date range {start_date} to {end_date}.")
            return [], [], 0, 0, 0
            
        logger.info(f"Found {total_pages_to_fetch} journal page(s) in date range. Starting content processing...")
        
        async for page_data in fetch_journal_pages_content(pages_metadata_to_process):
            processed_count += 1
            page_id = page_data["id"]
            title = page_data["title"]
            content = page_data["content"]
            notion_last_edited_time = page_data.get("last_edited_time")
            notion_last_edited_dt = None
            if notion_last_edited_time:
                try:
                    notion_last_edited_dt = datetime.fromisoformat(notion_last_edited_time.replace("Z", "+00:00"))
                except ValueError:
                    debug_print(f"DEBUG: Could not parse last_edited_time '{notion_last_edited_time}' for page {page_id}")

            potential_journal_filepath = get_journal_entry_filepath(title, page_id)

            proceed_with_saving_original = True
            if os.path.exists(potential_journal_filepath) and notion_last_edited_dt and not force_pull:
                if not should_update_entry(potential_journal_filepath, notion_last_edited_dt):
                    proceed_with_saving_original = False
                    skipped_originals_count += 1
                    logger.info(f"[{processed_count}/{total_pages_to_fetch}] Skipping unmodified: {title} ({page_id})")
                else:
                    debug_print(f"DEBUG: Original file {potential_journal_filepath} needs update.")
            elif not notion_last_edited_dt:
                 debug_print(f"DEBUG: No last_edited_time from Notion for {page_id}, will save/overwrite.")
            elif force_pull:
                debug_print(f"DEBUG: Force pull is enabled, processing regardless of modification time: {page_id}")
            else:
                debug_print(f"DEBUG: Original file {potential_journal_filepath} does not exist, will save.")

            if proceed_with_saving_original:
                logger.info(f"[{processed_count}/{total_pages_to_fetch}] Processing: {title} ({page_id}) | Summarize: {summarize_flag}")
                try:
                    journal_fp, summary_fp = await _save_original_and_optionally_summarize_page_content(
                        page_id, title, content,
                        summarize_flag=summarize_flag
                    )
                    
                    if journal_fp:
                        all_journal_filepaths.append(journal_fp)
                        saved_originals_count += 1
                        debug_print(f"DEBUG: Original saved: {journal_fp}")

                    if summary_fp:
                        all_summary_filepaths.append(summary_fp)
                        saved_summaries_count += 1
                        debug_print(f"DEBUG: Summary saved: {summary_fp}")
                    
                except Exception as e:
                    logger.error(f"Failed to process page {page_id} (Title: {title}): {e}")
                    if os.getenv("MAIA_DEBUG") == "1":
                        traceback.print_exc()

        return all_journal_filepaths, all_summary_filepaths, saved_originals_count, saved_summaries_count, skipped_originals_count
        
    except Exception as e:
        logger.error(f"Error in date range journal pull: {e}")
        if os.getenv("MAIA_DEBUG") == "1":
            traceback.print_exc()
        return [], [], 0, 0, 0 