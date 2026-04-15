"""
Sync Notion pages to Webflow CMS.
"""
import os
import re
import asyncio
import json
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from slugify import slugify
import requests
from bs4 import BeautifulSoup
import urllib.parse
import hashlib
import shutil

from promaia.notion.pages import (
    get_sync_pages,
    get_pages_edited_after,
    get_block_content,
    get_page_title,
    update_webflow_id,
    get_pages_by_blog_status,
    update_page_blog_status,
    get_page_property,
    update_page_properties_batch
)
from promaia.html_converter.converter import page_to_html
from promaia.webflow.client import get_webflow_client, WebflowClient
from promaia.utils.config import update_last_sync_time, get_last_sync_time
from promaia.utils.config_loader import get_notion_database_id
from promaia.storage.sync_cache import SyncCache

# Default database IDs from environment variables
# DEFAULT_NOTION_DATABASE_ID = os.getenv("NOTION_WEBFLOW_DATABASE_ID") # Will be replaced by config loader
DEFAULT_WEBFLOW_COLLECTION_ID = os.getenv("WEBFLOW_COLLECTION_ID")

# Mapping between Notion property names and Webflow field names
DEFAULT_FIELD_MAPPING = {
    "Name": "name",              # Required
    "Slug": "slug",              # Required
    "Publish Date": "publish-date",     # DateTime
    "Author Name": "author-name",       # PlainText
    "Tag": "tag",                # PlainText
    "Emoji Tags": "emoji-tags",  # PlainText
    "Description": "post-summary",      # PlainText
    "Thumbnail Image": "main-image",    # Image
    "Featured": "featured",      # Switch
    "Is newsletter": "is-newsletter",   # Switch (note: lowercase 'n' in Notion)
    "Webflow ID": "webflow-id"   # Internal use only
}

def truncate_url(url: str, max_length: int = 60) -> str:
    """Truncate a URL for display in logs."""
    if not url or len(url) <= max_length:
        return url
    
    # Split into parts
    parts = url.split('://')
    if len(parts) < 2:
        return url[:max_length-3] + '...'
    
    protocol = parts[0]
    rest = parts[1]
    
    # Calculate how much space we have left
    remaining = max_length - len(protocol) - 6  # 6 = len('://...') + len('...')
    
    # If not enough space, just do basic truncation
    if remaining < 10:
        return url[:max_length-3] + '...'
    
    # Divide remaining space between start and end of the URL
    start_length = remaining // 2
    end_length = remaining - start_length
    
    return f"{protocol}://{rest[:start_length]}...{rest[-end_length:]}"

async def process_html_images(html_content: str, page_id: str, max_concurrent: int = 8) -> str:
    """
    Process all images in HTML content in parallel, uploading them to Webflow and replacing URLs.
    The formatted HTML must follow Webflow's RichText field requirements.

    Args:
        html_content: HTML content containing images
        page_id: ID of the page (used to create unique filenames)
        max_concurrent: Maximum number of concurrent image uploads (default: 8)

    Returns:
        HTML content with Notion image URLs replaced with Webflow URLs in proper format
    """
    # Parse the HTML
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find all image tags
    images = soup.find_all('img')

    # Keep track of processed images to avoid duplicates
    processed_urls = {}

    # Collect images to process
    images_to_process = []
    for img in images:
        src = img.get('src')

        # Skip if no src attribute or already processed
        if not src or src in processed_urls:
            if src in processed_urls:
                img['src'] = processed_urls[src]
            continue

        # Check if image is on Webflow but needs WebP conversion
        from promaia.config.webp_cache import should_convert_webflow_image, get_cached_webp_url
        from promaia.config.cms_config import is_webp_conversion_enabled

        if 'webflow.com' in src or 'website-files.com' in src:
            # Check cache first
            cached_webp = get_cached_webp_url(src)
            if cached_webp:
                # Already converted and cached, use the cached WebP URL
                img['src'] = cached_webp
                processed_urls[src] = cached_webp
                continue

            # Check if this Webflow image should be converted to WebP
            if is_webp_conversion_enabled() and should_convert_webflow_image(src):
                # This is a JPEG/PNG on Webflow that needs conversion
                # Add it to the processing queue
                pass  # Will be processed below
            else:
                # Already WebP or conversion disabled - skip
                continue

        images_to_process.append((img, src))

    # Process images in parallel
    if images_to_process:
        async def upload_single_image(img, src):
            """Upload a single image and return the result."""
            try:
                # Generate a unique filename for the image
                parsed_url = urllib.parse.urlparse(src)
                original_filename = os.path.basename(parsed_url.path)

                # Create a hash from the URL to ensure uniqueness
                url_hash = hashlib.md5(src.encode()).hexdigest()[:8]

                # Create a filename with page ID and hash
                if '.' in original_filename:
                    name, ext = os.path.splitext(original_filename)
                    filename = f"{page_id[:8]}_{url_hash}{ext}"
                else:
                    filename = f"{page_id[:8]}_{url_hash}.jpg"

                # Upload the image to Webflow (synchronous call wrapped in executor)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: get_webflow_client(silent=True).upload_asset_from_url(src, filename)
                )

                if result and 'url' in result:
                    new_url = result['url']

                    # If this was a Webflow image that got converted, cache the mapping
                    if 'webflow.com' in src or 'website-files.com' in src:
                        if new_url.lower().endswith('.webp') and not src.lower().endswith('.webp'):
                            from promaia.config.webp_cache import cache_webp_conversion
                            cache_webp_conversion(src, new_url)
                            print(f"   📝 Cached WebP conversion for existing Webflow image")

                    return (img, src, new_url, True)
                else:
                    return (img, src, None, False)
            except Exception:
                return (img, src, None, False)

        # Create semaphore to limit concurrent uploads
        semaphore = asyncio.Semaphore(max_concurrent)

        async def upload_with_semaphore(img, src):
            async with semaphore:
                return await upload_single_image(img, src)

        # Process all images concurrently
        tasks = [upload_with_semaphore(img, src) for img, src in images_to_process]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Apply results to the HTML
        for result in results:
            if isinstance(result, Exception):
                continue

            img, src, new_url, success = result
            if success and new_url:
                # Create a new figure element with the proper Webflow structure
                figure = soup.new_tag('figure')
                figure['class'] = 'w-richtext-figure-type-image w-richtext-align-fullwidth'

                # Create a new img element with the Webflow URL
                new_img = soup.new_tag('img')
                new_img['src'] = new_url

                # Add alt text if present
                if img.get('alt'):
                    new_img['alt'] = img.get('alt')

                # Add the image to the figure
                figure.append(new_img)

                # Create a figcaption if there's a title
                if img.get('title'):
                    figcaption = soup.new_tag('figcaption')
                    figcaption.string = img.get('title')
                    figure.append(figcaption)

                # Replace the original img with the figure
                img.replace_with(figure)

                # Keep track of this URL
                processed_urls[src] = new_url

    # Return the updated HTML content
    return str(soup)

async def notion_to_webflow_item(page: Dict[str, Any], 
                                 field_mapping: Dict[str, str] = None, 
                                 webflow_id_property_name: str = "Webflow ID"
                                 ) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Convert a Notion page to a Webflow CMS item format.
    
    Args:
        page: Notion page object
        field_mapping: Optional custom mapping of Notion property names to Webflow field names
        webflow_id_property_name: Name of the Notion property storing the Webflow ID.
        
    Returns:
        Tuple of (Webflow item data, stored Webflow ID or None)
    """
    # Use default mapping if none provided
    if field_mapping is None:
        field_mapping = DEFAULT_FIELD_MAPPING
    
    # Get page properties - adding defensive check
    properties = page.get("properties", {}) if page else {}
    if not properties:
        raise ValueError(f"Page has no properties or is malformed")
        
    webflow_data = {}
    
    # Extract the page ID
    page_id = page.get("id", "") if page else ""
    if not page_id:
        raise ValueError(f"Page has no ID")
    
    # Extract Webflow ID if it exists using get_page_property
    # This is more robust than direct property access if the property name changes
    # However, notion_to_webflow_item primarily *constructs* the data.
    # The stored_webflow_id should ideally be fetched *before* calling this,
    # but we can also retrieve it here for completeness or if page object is all we have.
    # For now, we'll assume it's passed or handled by the caller fetching it with get_page_property.
    # The original logic for extracting from properties was:
    stored_webflow_id = None
    if webflow_id_property_name in properties:
        webflow_id_prop_value = properties[webflow_id_property_name]
        if webflow_id_prop_value and webflow_id_prop_value.get("type") == "rich_text":
            rich_text_list = webflow_id_prop_value.get("rich_text", [])
            if rich_text_list:
                stored_webflow_id = "".join([text.get("plain_text", "") for text in rich_text_list if text])
                if stored_webflow_id and stored_webflow_id.strip():
                    # This function returns data payload and ID; it doesn't put _webflow_id in payload.
                    pass # stored_webflow_id is now correctly populated
                else:
                    stored_webflow_id = None

    # Get the page title from the Name property
    if "Name" in properties:
        name_prop = properties["Name"]
        if name_prop and name_prop.get("type") == "title":
            title_array = name_prop.get("title", [])
            if title_array:
                title = "".join([text.get("plain_text", "") for text in title_array if text])
                if title:
                    webflow_data[field_mapping.get("Name", "name")] = title
                else:
                    raise ValueError(f"Page title is empty")
            else:
                raise ValueError(f"Page title array is empty")
        else:
            raise ValueError(f"Name property has unexpected format")
    else:
        # If Name property is missing, this is a required field - throw an error
        raise ValueError(f"Missing required field: Name")
    
    # Generate slug if not present
    slug_field = field_mapping.get("Slug", "slug")
    if "Slug" in properties:
        slug_prop = properties["Slug"]
        if slug_prop and slug_prop.get("type") == "rich_text":
            # Get slug from rich text
            rich_text = slug_prop.get("rich_text", [])
            if rich_text:
                slug = "".join([text.get("plain_text", "") for text in rich_text if text])
                if slug:
                    webflow_data[slug_field] = slug
                else:
                    webflow_data[slug_field] = slugify(title)
            else:
                webflow_data[slug_field] = slugify(title)
        else:
            webflow_data[slug_field] = slugify(title)
    else:
        webflow_data[slug_field] = slugify(title)
    
    # Process date field
    date_field = field_mapping.get("Publish Date", "publish-date")
    if "Publish Date" in properties:
        date_prop = properties["Publish Date"]
        if date_prop and date_prop.get("type") == "date":
            date_value = date_prop.get("date")
            if date_value and date_value.get("start"):
                # Format the date for Webflow (ISO format)
                webflow_data[date_field] = date_value.get("start")
    
    # If no date is found, use current date
    if date_field not in webflow_data or not webflow_data.get(date_field):
        current_date = datetime.now().isoformat().split("T")[0]  # Format as YYYY-MM-DD
        webflow_data[date_field] = current_date
    
    # Process author field
    author_field = field_mapping.get("Author Name", "author-name")
    if "Author Name" in properties:
        author_prop = properties["Author Name"]
        if author_prop and author_prop.get("type") == "rich_text":
            rich_text = author_prop.get("rich_text", [])
            if rich_text:
                author = "".join([text.get("plain_text", "") for text in rich_text if text])
                if author:
                    webflow_data[author_field] = author
                else:
                    # Use a default author name if empty
                    webflow_data[author_field] = "Anonymous"
            else:
                webflow_data[author_field] = "Anonymous"
        else:
            webflow_data[author_field] = "Anonymous"
    else:
        # Use a default author if missing
        webflow_data[author_field] = "Anonymous"
    
    # Process tag field
    tag_field = field_mapping.get("Tag", "tag")
    if "Tag" in properties:
        tag_prop = properties["Tag"]
        if tag_prop and tag_prop.get("type") == "select":
            select = tag_prop.get("select")
            if select:
                tag_name = select.get("name")
                if tag_name:
                    webflow_data[tag_field] = tag_name
    
    # Process emoji tags field
    emoji_tags_field = field_mapping.get("Emoji Tags", "emoji-tags")
    if "Emoji Tags" in properties:
        emoji_prop = properties["Emoji Tags"]
        if emoji_prop and emoji_prop.get("type") == "rich_text":
            rich_text = emoji_prop.get("rich_text", [])
            if rich_text:
                emoji_tags = "".join([text.get("plain_text", "") for text in rich_text if text])
                if emoji_tags:
                    webflow_data[emoji_tags_field] = emoji_tags
    
    # Process featured image field as Webflow asset
    image_field = field_mapping.get("Thumbnail Image", "main-image")
    if "Thumbnail Image" in properties:
        img_prop = properties["Thumbnail Image"]
        if img_prop and img_prop.get("type") == "files":
            files = img_prop.get("files", [])
            if files and len(files) > 0:
                file = files[0]
                if file:
                    image_url = None
                    
                    if file.get("type") == "external":
                        external = file.get("external")
                        if external:
                            image_url = external.get("url", "")
                    elif file.get("type") == "file":
                        file_obj = file.get("file")
                        if file_obj:
                            image_url = file_obj.get("url", "")
                    
                    if image_url:
                        # Use the Notion URL directly for the main image
                        webflow_data[image_field] = image_url

    # Handle page cover image if present but no thumbnail was provided
    if image_field not in webflow_data and page and "cover" in page:
        cover = page.get("cover", {})
        if cover:
            image_url = None

            if cover.get("type") == "external":
                external = cover.get("external")
                if external:
                    image_url = external.get("url", "")
            elif cover.get("type") == "file":
                file_obj = cover.get("file")
                if file_obj:
                    image_url = file_obj.get("url", "")

            if image_url:
                webflow_data[image_field] = image_url

    # Process featured field (checkbox)
    featured_field = field_mapping.get("Featured", "featured")
    if "Featured" in properties:
        featured_prop = properties["Featured"]
        if featured_prop and featured_prop.get("type") == "checkbox":
            webflow_data[featured_field] = featured_prop.get("checkbox", False)

    # Process is_newsletter field (checkbox)
    is_newsletter_field = field_mapping.get("Is newsletter", "is-newsletter")
    if "Is newsletter" in properties:
        is_newsletter_prop = properties["Is newsletter"]
        if is_newsletter_prop and is_newsletter_prop.get("type") == "checkbox":
            webflow_data[is_newsletter_field] = is_newsletter_prop.get("checkbox", False)


    try:
        # Get page content with persistent caching
        last_edited_time = page.get('last_edited_time') if page else None
        blocks = await get_block_content(page_id, last_edited_time=last_edited_time)

        # Convert blocks to HTML
        html_content = page_to_html(blocks)

        # Process images in the HTML content (now async with parallel processing)
        processed_html = await process_html_images(html_content, page_id)

        # Set HTML content field - always supported
        webflow_data["post-body"] = processed_html
    except Exception:
        # If there's an error with content processing, just use a placeholder
        webflow_data["post-body"] = f"<p>Content unavailable. Please check the original Notion page.</p>"
    
    # Remove internal fields that shouldn't be sent to Webflow
    # The _webflow_id logic was specific to how this function used to return.
    # Now it explicitly returns webflow_data and stored_webflow_id.
    
    return (webflow_data, stored_webflow_id)

async def process_single_page(page_data: Dict[str, Any], webflow_collection_id: str, webflow_id_map: Dict[str, Any],
                             required_fields: List[str], blog_status_property_name: str, webflow_id_property_name: str,
                             field_mapping: Dict[str, str], sync_cache: Optional[SyncCache] = None) -> Tuple[str, int, int, int, int, int]:
    """
    Process a single Notion page for Webflow sync.

    Returns:
        Tuple of (page_id, created, updated, deleted, skipped, error)
    """
    page_id = page_data.get("id")
    if not page_id:
        return "", 0, 0, 0, 0, 1

    try:
        # Extract properties from page_data instead of making API calls
        properties = page_data.get("properties", {})

        # Extract current blog status
        status_prop = properties.get(blog_status_property_name, {})
        current_blog_status = None
        if status_prop.get("type") == "status":
            current_blog_status = status_prop.get("status", {}).get("name")
        elif status_prop.get("type") == "select":
            current_blog_status = status_prop.get("select", {}).get("name")

        # Extract stored webflow ID
        webflow_id_prop = properties.get(webflow_id_property_name, {})
        stored_webflow_id_on_notion = None
        if webflow_id_prop.get("type") == "rich_text":
            rich_text_list = webflow_id_prop.get("rich_text", [])
            if rich_text_list:
                stored_webflow_id_on_notion = "".join([text.get("plain_text", "") for text in rich_text_list if text])
                if not stored_webflow_id_on_notion or not stored_webflow_id_on_notion.strip():
                    stored_webflow_id_on_notion = None

        if current_blog_status == "Live":
            # Live pages are kept as-is
            return page_id, 0, 0, 0, 1, 0

        elif current_blog_status == "Don't sync":
            if stored_webflow_id_on_notion and stored_webflow_id_on_notion in webflow_id_map:
                try:
                    delete_success = get_webflow_client(silent=True).delete_item(webflow_collection_id, stored_webflow_id_on_notion)
                    if delete_success:
                        await update_webflow_id(page_id, None, property_name=webflow_id_property_name)
                        return page_id, 0, 0, 1, 0, 0
                    else:
                        return page_id, 0, 0, 0, 0, 1
                except Exception:
                    return page_id, 0, 0, 0, 0, 1
            elif stored_webflow_id_on_notion:
                await update_webflow_id(page_id, None, property_name=webflow_id_property_name)
            return page_id, 0, 0, 0, 1, 0

        elif current_blog_status in ["To sync", "Update on sync"]:
            # Convert Notion page to Webflow data
            webflow_data_payload, _ = await notion_to_webflow_item(page_data, field_mapping, webflow_id_property_name)
            slug = webflow_data_payload.get("slug", f"page-{page_id[:8]}")

            # Validate required fields
            missing_fields = [rf for rf in required_fields if rf not in webflow_data_payload or not webflow_data_payload.get(rf)]
            if missing_fields:
                return page_id, 0, 0, 0, 0, 1

            # Simple logic: ID exists in Webflow = UPDATE, else = CREATE
            if stored_webflow_id_on_notion and stored_webflow_id_on_notion in webflow_id_map:
                # UPDATE: Remove slug from payload
                update_payload = webflow_data_payload.copy()
                update_payload.pop("slug", None)

                response = get_webflow_client(silent=True).update_item(webflow_collection_id, stored_webflow_id_on_notion, update_payload)
                if response:
                    # Batch update status to "Update on sync" if needed
                    if current_blog_status == "To sync":
                        await update_page_properties_batch(
                            page_id,
                            status_property_name=blog_status_property_name,
                            new_status="Update on sync"
                        )

                    # Update cache after successful sync
                    if sync_cache:
                        sync_cache.update_cache(page_id, page_data, stored_webflow_id_on_notion)

                    return page_id, 0, 1, 0, 0, 0
                else:
                    return page_id, 0, 0, 0, 0, 1

            else:
                # CREATE: Include slug in payload
                response = get_webflow_client(silent=True).create_item(webflow_collection_id, webflow_data_payload)
                if response and response.get("id"):
                    new_webflow_id = response["id"]

                    # Batch update both webflow_id and status in a single API call
                    if current_blog_status == "To sync":
                        await update_page_properties_batch(
                            page_id,
                            status_property_name=blog_status_property_name,
                            new_status="Update on sync",
                            webflow_id_property_name=webflow_id_property_name,
                            webflow_id=new_webflow_id
                        )
                    else:
                        await update_webflow_id(page_id, new_webflow_id, property_name=webflow_id_property_name)

                    # Update cache after successful sync
                    if sync_cache:
                        sync_cache.update_cache(page_id, page_data, new_webflow_id)

                    return page_id, 1, 0, 0, 0, 0
                else:
                    return page_id, 0, 0, 0, 0, 1
        else:
            # Unknown status
            return page_id, 0, 0, 0, 1, 0

    except Exception as e:
        return page_id, 0, 0, 0, 0, 1


async def sync_to_webflow(notion_database_id: str = None,
                         webflow_collection_id: str = None,
                         field_mapping: Dict[str, str] = None,
                         blog_status_property_name: str = "Blog Status",
                         webflow_id_property_name: str = "Webflow ID",
                         force_update: bool = False,
                         max_concurrent: int = 5,
                         use_cache: bool = True
                         ) -> Tuple[int, int, int, int, int]:
    """
    Sync Notion pages to Webflow CMS based on a 'Blog Status' property with optimized async processing.

    Args:
        notion_database_id: ID of the Notion database. If None, will be fetched from config using nickname 'cms'.
        webflow_collection_id: ID of the Webflow collection.
        field_mapping: Custom mapping of Notion property names to Webflow field names.
        blog_status_property_name: Name of the Notion select property for blog status (default: "Blog Status").
        webflow_id_property_name: Name of the Notion rich_text property storing the Webflow item ID (default: "Webflow ID").
        force_update: If True, attempts to update items even if status doesn't force it (e.g. "Live" items if modified).
        max_concurrent: Maximum number of concurrent page processing operations.
        use_cache: If True, uses content hash caching to skip unchanged pages (default: True).

    Returns:
        Tuple of (created_count, updated_count, deleted_count, skipped_count, error_count)
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if notion_database_id is None:
        try:
            notion_database_id = get_notion_database_id("cms")
        except (FileNotFoundError, ValueError) as e:
            raise ValueError(f"Error loading Notion database ID for 'cms': {e}. Please specify or configure in notion_config.json.") from e

    webflow_collection_id = webflow_collection_id or DEFAULT_WEBFLOW_COLLECTION_ID

    if not notion_database_id:
        raise ValueError("No Notion database ID provided or configured for nickname 'cms'. Please specify or set in notion_config.json.")
    if not webflow_collection_id:
        raise ValueError("No Webflow collection ID provided. Please specify or set WEBFLOW_COLLECTION_ID.")

    created_count, updated_count, deleted_count, skipped_count, error_count = 0, 0, 0, 0, 0

    target_statuses_for_fetch = ["To sync", "Update on sync", "Don't sync", "Live"]

    # Clean, minimal status message
    print("🔄 Syncing CMS content...")

    try:
        pages_to_process = await get_pages_by_blog_status(notion_database_id, blog_status_property_name, target_statuses_for_fetch)
    except Exception as e:
        print(f"❌ Error getting pages from Notion: {str(e)}")
        return 0, 0, 0, 0, 1

    if not pages_to_process:
        print("✅ No pages to sync")
        new_sync_time = update_last_sync_time()
        return 0, 0, 0, 0, 0

    try:
        webflow_items = get_webflow_client(silent=True).get_collection_items(webflow_collection_id) or []
    except Exception as e:
        print(f"❌ Error getting Webflow items: {str(e)}")
        webflow_items = []

    webflow_id_map = {item["id"]: item for item in webflow_items if item and "id" in item}

    # Get collection schema (required fields) - silent operation
    try:
        collection_fields = get_webflow_client(silent=True).get_collection_fields(webflow_collection_id) or {}
        required_fields = [slug for slug, info in collection_fields.items() if info and info.get("required")]
    except Exception:
        required_fields = []

    # Initialize sync cache if enabled
    sync_cache = SyncCache() if use_cache else None

    # Pre-filter pages by status and check cache to avoid unnecessary processing
    # Extract status directly from page_data properties (eliminates N+1 query)
    pages_by_status = {"To sync": [], "Update on sync": [], "Don't sync": [], "Live": []}
    cache_skipped_count = 0

    for page_data in pages_to_process:
        page_id = page_data.get("id")
        if not page_id:
            error_count += 1
            continue

        try:
            # Extract status directly from page properties instead of making API call
            properties = page_data.get("properties", {})
            status_prop = properties.get(blog_status_property_name, {})

            # Handle both status and select property types
            current_blog_status = None
            if status_prop.get("type") == "status":
                status_obj = status_prop.get("status", {})
                current_blog_status = status_obj.get("name")
            elif status_prop.get("type") == "select":
                select_obj = status_prop.get("select", {})
                current_blog_status = select_obj.get("name")

            if current_blog_status and current_blog_status in target_statuses_for_fetch:
                # Check cache for "Update on sync" pages to skip unchanged content
                if use_cache and sync_cache and current_blog_status == "Update on sync":
                    if not sync_cache.should_process_page(page_id, page_data):
                        # Page hasn't changed since last sync, skip it
                        cache_skipped_count += 1
                        skipped_count += 1
                        continue

                pages_by_status[current_blog_status].append(page_data)
        except Exception as e:
            error_count += 1

    if use_cache and cache_skipped_count > 0:
        print(f"⚡ Cache: Skipped {cache_skipped_count} unchanged pages")

    # Show what we're working with
    total_to_process = len(pages_by_status['To sync']) + len(pages_by_status['Update on sync'])
    if total_to_process > 0:
        page_word = "page" if total_to_process == 1 else "pages"
        print(f"📄 Processing {total_to_process} {page_word}...")

        # Show which pages are being processed - extract from page_data directly
        sync_pages = pages_by_status["To sync"] + pages_by_status["Update on sync"]
        for page_data in sync_pages:
            page_id = page_data.get("id")
            if page_id:
                try:
                    # Extract title and status from page_data properties (no API calls)
                    properties = page_data.get("properties", {})

                    # Extract title from Name or Title property
                    page_title = None
                    for title_key in ["Name", "Title"]:
                        if title_key in properties:
                            title_prop = properties[title_key]
                            if title_prop.get("type") == "title":
                                title_array = title_prop.get("title", [])
                                if title_array:
                                    page_title = "".join([text.get("plain_text", "") for text in title_array if text])
                                    break

                    if not page_title:
                        page_title = f"Page {page_id[:8]}"

                    # Extract status
                    status_prop = properties.get(blog_status_property_name, {})
                    current_status = None
                    if status_prop.get("type") == "status":
                        current_status = status_prop.get("status", {}).get("name")
                    elif status_prop.get("type") == "select":
                        current_status = status_prop.get("select", {}).get("name")

                    action = "Creating" if current_status == "To sync" else "Updating"
                    print(f"   {action}: {page_title}")
                except Exception:
                    print(f"   Processing: Page {page_id[:8]}")

    # Handle "Live" pages first (quick status check)
    live_pages = pages_by_status["Live"]
    for page_data in live_pages:
        page_id = page_data.get("id")
        stored_webflow_id = await get_page_property(page_id, webflow_id_property_name)
        if stored_webflow_id and stored_webflow_id in webflow_id_map:
            pass  # Keep track that this ID is still needed
        skipped_count += 1

    # Handle "Don't sync" pages (deletions)
    dont_sync_pages = pages_by_status["Don't sync"]
    for page_data in dont_sync_pages:
        page_id = page_data.get("id")
        stored_webflow_id = await get_page_property(page_id, webflow_id_property_name)

        if stored_webflow_id and stored_webflow_id in webflow_id_map:
            try:
                delete_success = get_webflow_client(silent=True).delete_item(webflow_collection_id, stored_webflow_id)
                if delete_success:
                    await update_webflow_id(page_id, None, property_name=webflow_id_property_name)
                    deleted_count += 1
                else:
                    error_count += 1
            except Exception:
                error_count += 1
        elif stored_webflow_id:
            await update_webflow_id(page_id, None, property_name=webflow_id_property_name)
        skipped_count += 1

    # Process "To sync" and "Update on sync" pages concurrently
    sync_pages = pages_by_status["To sync"] + pages_by_status["Update on sync"]

    if sync_pages:
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_with_semaphore(page_data):
            async with semaphore:
                return await process_single_page(
                    page_data, webflow_collection_id, webflow_id_map, required_fields,
                    blog_status_property_name, webflow_id_property_name, field_mapping, sync_cache
                )

        # Process pages concurrently
        tasks = [process_with_semaphore(page_data) for page_data in sync_pages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for result in results:
            if isinstance(result, Exception):
                error_count += 1
                continue

            page_id, created, updated, deleted, skipped, error = result
            created_count += created
            updated_count += updated
            deleted_count += deleted
            skipped_count += skipped
            error_count += error

    # Update last sync time
    new_sync_time = update_last_sync_time()

    # Clean up sync cache
    if sync_cache:
        sync_cache.close()

    # Clean final summary
    if created_count > 0 or updated_count > 0 or deleted_count > 0:
        print("✅ Sync completed")
        if created_count > 0:
            print(f"   📝 Created: {created_count}")
        if updated_count > 0:
            print(f"   🔄 Updated: {updated_count}")
        if deleted_count > 0:
            print(f"   🗑️  Deleted: {deleted_count}")
        if error_count > 0:
            print(f"   ⚠️  Errors: {error_count}")
    else:
        print("✅ No changes needed")
    return created_count, updated_count, deleted_count, skipped_count, error_count 