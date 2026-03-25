"""
Newsletter CLI commands.
"""
import os
import asyncio
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import urllib.parse
from bs4 import BeautifulSoup

from promaia.notion.client import ensure_default_client
from promaia.notion.pages import get_page_title, get_block_content
from promaia.html_converter.converter import block_to_html, page_to_html
from promaia.newsletter.resend_client import get_resend_client
from promaia.newsletter.template import (
    create_plain_text_newsletter, 
    notion_blocks_to_markdown,
    create_simple_newsletter_html
)
from promaia.webflow.client import get_webflow_client
from promaia.utils.config import get_config
from promaia.utils.display import print_text, print_separator

# Default database ID (uses the same as Webflow CMS)
WEBFLOW_CMS_DATABASE_ID = os.getenv("WEBFLOW_CMS_DATABASE_ID", "10dd13396967807ab987c92a4d29b9b8")

# Default collection ID for Webflow
DEFAULT_WEBFLOW_COLLECTION_ID = os.getenv("WEBFLOW_COLLECTION_ID")

def truncate_url(url: str, max_length: int = 50) -> str:
    """Truncate a URL for display purposes."""
    if len(url) <= max_length:
        return url
    return url[:max_length] + "..."

# Helper function to convert Notion page to markdown
async def notion_page_to_markdown(page_id: str) -> str:
    """
    Convert Notion page content to markdown.
    
    Args:
        page_id: The ID of the Notion page
        
    Returns:
        Markdown string of the page content
    """
    blocks = await get_block_content(page_id)
    return notion_blocks_to_markdown(blocks)

async def check_webflow_published(page: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Check if a Notion page has been published to Webflow.
    
    Args:
        page: Notion page object
        
    Returns:
        Tuple of (is_published, webflow_id, webflow_slug)
    """
    page_id = page["id"]
    properties = page.get("properties", {})
    
    # Check if the page has a Webflow ID
    webflow_id = None
    if "Webflow ID" in properties:
        webflow_id_prop = properties["Webflow ID"]
        if webflow_id_prop and webflow_id_prop.get("type") == "rich_text":
            rich_text = webflow_id_prop.get("rich_text", [])
            if rich_text:
                webflow_id = "".join([text.get("plain_text", "") for text in rich_text if text])
    
    if not webflow_id:
        print_text(f"   ❌ No Webflow ID found for page: {page_id}", style="red")
        return False, None, None
    
    # Get the slug
    slug = None
    if "Slug" in properties:
        slug_prop = properties["Slug"]
        if slug_prop and slug_prop.get("type") == "rich_text":
            rich_text = slug_prop.get("rich_text", [])
            if rich_text:
                slug = "".join([text.get("plain_text", "") for text in rich_text if text])
    
    # If no slug found in the Slug property, try to extract from the Name property
    if not slug and "Name" in properties:
        from slugify import slugify
        name_prop = properties["Name"]
        if name_prop and name_prop.get("type") == "title":
            title_array = name_prop.get("title", [])
            if title_array:
                title = "".join([text.get("plain_text", "") for text in title_array if text])
                if title:
                    slug = slugify(title)
    
    if not slug:
        print_text(f"   ❌ No slug found for page: {page_id}", style="red")
        return False, webflow_id, None
    
    # Try to verify the item exists in Webflow
    collection_id = DEFAULT_WEBFLOW_COLLECTION_ID
    if not collection_id:
        print_text("   ❌ No Webflow collection ID configured", style="red")
        return False, webflow_id, slug
    
    try:
        # Check if the item exists in Webflow
        webflow_item = get_webflow_client(silent=True).get_item(collection_id, webflow_id)
        if webflow_item:
            print_text(f"   ✅ Found published blog post in Webflow (ID: {webflow_id})", style="green")
            return True, webflow_id, slug
        else:
            print_text(f"   ❌ Blog post with ID {webflow_id} not found in Webflow", style="red")
            return False, webflow_id, slug
    except Exception as e:
        print_text(f"   ❌ Error checking Webflow: {str(e)}", style="red")
        return False, webflow_id, slug

def replace_notion_images_with_webflow(html_content: str, webflow_id: str, header_image: str = None) -> str:
    """
    Replace Notion image URLs with Webflow CDN URLs from the synced blog post.

    Notion S3 presigned URLs expire after ~1 hour, so emails must use
    permanent Webflow-hosted URLs instead. This fetches the Webflow item's
    post-body HTML, extracts its image URLs, and replaces Notion URLs
    in order of appearance.

    Args:
        html_content: HTML content with Notion image URLs
        webflow_id: Webflow item ID (to fetch the correct item)
        header_image: Optional URL of the header image to exempt from replacement

    Returns:
        HTML content with Webflow image URLs
    """
    # Fetch the Webflow item to get its post-body with CDN image URLs
    collection_id = DEFAULT_WEBFLOW_COLLECTION_ID
    if not collection_id or not webflow_id:
        print_text("   ⚠️ No Webflow collection/item ID, keeping Notion image URLs", style="yellow")
        return html_content

    try:
        webflow_item = get_webflow_client(silent=True).get_item(collection_id, webflow_id)
        if not webflow_item or "fieldData" not in webflow_item:
            print_text("   ⚠️ Could not fetch Webflow item, keeping Notion image URLs", style="yellow")
            return html_content

        post_body = webflow_item["fieldData"].get("post-body", "")
        if not post_body:
            print_text("   ⚠️ No post-body in Webflow item, keeping Notion image URLs", style="yellow")
            return html_content

        # Extract Webflow CDN image URLs from the post body (in order)
        webflow_soup = BeautifulSoup(post_body, 'html.parser')
        webflow_images = [img.get('src') for img in webflow_soup.find_all('img') if img.get('src')]
        if not webflow_images:
            print_text("   ⚠️ No images found in Webflow post body", style="yellow")
            return html_content

        print_text(f"   🔗 Found {len(webflow_images)} Webflow-hosted images to use", style="white")

    except Exception as e:
        print_text(f"   ⚠️ Error fetching Webflow images: {e}", style="yellow")
        return html_content

    # Parse the newsletter HTML and replace Notion URLs with Webflow URLs (by position)
    soup = BeautifulSoup(html_content, 'html.parser')
    newsletter_images = soup.find_all('img')

    webflow_idx = 0
    for img in newsletter_images:
        src = img.get('src', '')

        # Skip the header image and already-Webflow URLs
        if not src:
            continue
        if header_image and src == header_image:
            continue
        if 'webflow.com' in src or 'website-files.com' in src or 'cdn.prod.' in src:
            continue

        # This is a Notion/S3 image — replace with next Webflow URL
        if webflow_idx < len(webflow_images):
            old_url = truncate_url(src)
            new_url = webflow_images[webflow_idx]
            img['src'] = new_url
            print_text(f"   🔄 Replaced: {old_url} → {truncate_url(new_url)}", style="white")
            webflow_idx += 1
        else:
            print_text(f"   ⚠️ No more Webflow images to map for: {truncate_url(src)}", style="yellow")

    return str(soup)

async def query_pages_by_status(database_id: str, status: str) -> List[Dict[str, Any]]:
    """
    Query Notion database for pages with a specific Newsletter Status.
    
    Args:
        database_id: Notion database ID
        status: Newsletter Status value to filter by
        
    Returns:
        List of page objects
    """
    status_filter = {
        "property": "Newsletter Status",
        "status": {
            "equals": status
        }
    }
    
    notion_client = ensure_default_client()
    response = await notion_client.databases.query(
        database_id=database_id,
        filter=status_filter
    )
    
    return response.get("results", [])

async def get_eligible_newsletter_pages(database_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get pages eligible for newsletter syncing.
    
    Only includes pages with Newsletter Status "To send".
    
    Args:
        database_id: Notion database ID (defaults to WEBFLOW_CMS_DATABASE_ID)
        
    Returns:
        List of page objects with "To send" status
    """
    # Use default database ID if not provided
    db_id = database_id or WEBFLOW_CMS_DATABASE_ID
    
    # Query for pages with "To send" status only
    to_send_pages = await query_pages_by_status(db_id, "To send")
    
    return to_send_pages

async def update_page_newsletter_status(page_id: str, status: str):
    """
    Update the Newsletter Status property of a Notion page.
    
    Args:
        page_id: The ID of the Notion page
        status: The new status value
    """
    # Get the Notion client
    notion_client = ensure_default_client()
    
    # Update as rich_text property
    await notion_client.pages.update(
        page_id=page_id,
        properties={
            "Newsletter Status": {
                "status": {
                    "name": status
                }
            }
        }
    )

async def update_page_last_synced(page_id: str):
    """
    Update the Newsletter Last Synced property of a Notion page with the current datetime.
    If the property doesn't exist, this function will silently skip the update.

    Args:
        page_id: The ID of the Notion page
    """
    try:
        # Get the Notion client
        notion_client = ensure_default_client()

        # Use current time for sync timestamp
        now = datetime.now(timezone.utc).isoformat()

        # Update as rich_text property
        await notion_client.pages.update(
            page_id=page_id,
            properties={
                "Newsletter Last Synced": {
                    "rich_text": [
                        {
                            "text": {
                                "content": now
                            }
                        }
                    ]
                }
            }
        )
    except Exception as e:
        # If the property doesn't exist, just skip the update
        # This is optional metadata and shouldn't block the newsletter send
        if "not a property that exists" in str(e):
            pass  # Silently skip if property doesn't exist
        else:
            # Log other errors but don't fail
            print_text(f"   ⚠️ Could not update last synced time: {str(e)}", style="yellow")

def get_cover_image_url(page: Dict[str, Any]) -> Optional[str]:
    """
    Extract the cover image URL from a Notion page.
    
    Args:
        page: Notion page object
        
    Returns:
        Cover image URL if available, None otherwise
    """
    # First try to get thumbnail image from properties
    thumbnail_image = get_property_value(page, "Thumbnail Image")
    if thumbnail_image:
        print_text(f"   🖼️ Found thumbnail image: {truncate_url(thumbnail_image)}", style="white")
        return thumbnail_image
    
    # Then try to get cover image from page object
    cover = page.get("cover")
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
            print_text(f"   🖼️ Found cover image: {truncate_url(image_url)}", style="white")
            return image_url
    
    return None

async def get_webflow_hosted_image_url(page: Dict[str, Any], notion_image_url: str) -> Optional[str]:
    """
    Get the Webflow-hosted version of an image if the page is published to Webflow.
    
    Args:
        page: Notion page object
        notion_image_url: Original Notion image URL
        
    Returns:
        Webflow-hosted image URL if available, None otherwise
    """
    try:
        # Check if the blog post has been published to Webflow
        is_published, webflow_id, webflow_slug = await check_webflow_published(page)
        
        if not is_published or not webflow_id:
            print_text(f"   ⚠️  Page not published to Webflow, using Notion image URL", style="yellow")
            return None
        
        # Get the Webflow item to check if it has a main image
        from promaia.webflow.client import get_webflow_client
        
        try:
            # Get the collection ID from environment
            collection_id = os.getenv("WEBFLOW_COLLECTION_ID")
            if not collection_id:
                print_text(f"   ⚠️  No WEBFLOW_COLLECTION_ID configured, using Notion image URL", style="yellow")
                return None
            
            # Get the Webflow item data
            webflow_item = get_webflow_client(silent=True).get_item(collection_id, webflow_id)
            
            if webflow_item and "fieldData" in webflow_item:
                # Check if the main image is available
                main_image = webflow_item["fieldData"].get("main-image")
                if main_image:
                    # Extract the URL from the main image object
                    image_url = main_image.get("url") if isinstance(main_image, dict) else main_image
                    print_text(f"   ✅ Using Webflow-hosted image: {truncate_url(image_url)}", style="green")
                    return image_url
                else:
                    print_text(f"   ⚠️  No main image found in Webflow item, using Notion image URL", style="yellow")
            
        except Exception as e:
            print_text(f"   ⚠️  Error fetching Webflow item: {e}, using Notion image URL", style="yellow")
        
    except Exception as e:
        print_text(f"   ⚠️  Error checking Webflow status: {e}, using Notion image URL", style="yellow")
    
    return None

async def send_newsletter_via_resend(page: Dict[str, Any], test_mode: bool = False) -> Tuple[bool, str, Optional[str]]:
    """
    Send newsletter via Resend based on Notion page content.
    
    Args:
        page: Notion page object
        test_mode: If True, send to test emails only. If False, send broadcast to audience.
        
    Returns:
        Tuple of (success, message, email_id)
    """
    page_id = page["id"]
    properties = page.get("properties", {})
    
    # Debug: Print available properties
    print_text("   Available properties:", style="white")
    for key in properties.keys():
        print_text(f"   • {key}", style="dim")
    
    # Get required properties
    title = get_property_value(page, "Title") or get_property_value(page, "Name") or ""
    if not title:
        return False, "❌ Missing page title", None
    
    # Check if the blog post has been published to Webflow
    is_published, webflow_id, webflow_slug = await check_webflow_published(page)
    if not is_published:
        print_text(f"   ⚠️  Blog post not found in Webflow, but continuing for newsletter testing...", style="yellow")
        # Use a default slug based on title
        webflow_slug = title.lower().replace(' ', '-').replace(',', '').replace('.', '')
    
    # Generate the "read on website" URL using the blog slug
    post_link = f"https://www.koiibenvenutto.com/post/{webflow_slug}" if webflow_slug else f"https://www.koiibenvenutto.com/"
    print_text(f"   📄 Using 'read on website' URL: {post_link}", style="white")
    
    # Get additional properties for email template
    subtitle = get_property_value(page, "Newsletter Subtitle") or ""
    print_text(f"   📄 Subtitle: {subtitle}", style="white")
    
    # Get cover image URL
    cover_image_url = get_cover_image_url(page)
    if cover_image_url:
        # Try to get Webflow-hosted version (best practice)
        webflow_image_url = await get_webflow_hosted_image_url(page, cover_image_url)
        if webflow_image_url:
            cover_image_url = webflow_image_url
    
    # Convert page content to markdown
    try:
        blocks = await get_block_content(page_id)
        content_markdown = notion_blocks_to_markdown(blocks)
        print_text(f"   📄 Generated markdown content length: {len(content_markdown)} characters", style="white")
    except Exception as e:
        return False, f"❌ Error converting page to markdown: {str(e)}", None
    
    # Create HTML newsletter using the new simple template
    try:
        email_html_content = create_simple_newsletter_html(
            content_markdown=content_markdown,
            title=title,
            header_image_url=cover_image_url,
            subtitle=subtitle,
            post_link=post_link
        )

        # Replace Notion S3 image URLs with permanent Webflow CDN URLs
        if is_published and webflow_id:
            email_html_content = replace_notion_images_with_webflow(
                email_html_content, webflow_id, header_image=cover_image_url
            )

        print_text(f"   📧 Generated HTML newsletter with proper markdown rendering", style="white")
        print_text(f"   📧 HTML content length: {len(email_html_content)} characters", style="white")
        
        # Also create plain text version for fallback
        email_plain_text = create_plain_text_newsletter(
            content_text=content_markdown,
            newsletter_title=title,
            subtitle=subtitle,
            post_link=post_link,
            cover_image_url=cover_image_url
        )
        
    except Exception as e:
        return False, f"❌ Error creating newsletter content: {str(e)}", None
    
    # Create email subject
    email_subject = title
    if subtitle:
        email_subject += f" | {subtitle}"
    print_text(f"   📧 Email subject: {email_subject}", style="white")
    
    # Send via Resend
    try:
        resend_client = get_resend_client(silent=True)
        
        if test_mode:
            # Test mode: send to individual test emails
            result = resend_client.send_newsletter(
                subject=email_subject,
                plain_text=email_plain_text,
                html_content=email_html_content
            )
            
            if result["success"]:
                email_id = result["email_id"]
                success_message = f"✅ TEST Newsletter sent successfully (Email ID: {email_id})"
                if cover_image_url:
                    success_message += f" with header image"
                
                return True, success_message, email_id
            else:
                return False, f"❌ Failed to send TEST newsletter: {result.get('error', 'Unknown error')}", None
        else:
            # Production mode: send broadcast to audience
            result = resend_client.send_broadcast_to_audience(
                subject=email_subject,
                plain_text=email_plain_text,
                html_content=email_html_content
            )
            
            if result["success"]:
                broadcast_id = result["broadcast_id"]
                success_message = f"✅ Newsletter BROADCAST sent successfully to audience (Broadcast ID: {broadcast_id})"
                if cover_image_url:
                    success_message += f" with header image"
                
                return True, success_message, broadcast_id
            else:
                return False, f"❌ Failed to send newsletter broadcast: {result.get('error', 'Unknown error')}", None
            
    except Exception as e:
        return False, f"❌ Error with Resend API: {str(e)}", None


def format_datetime(iso_date: str) -> str:
    """Format ISO datetime for display."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_date

def get_property_value(page: Dict[str, Any], property_name: str) -> Any:
    """Extract a property value from a Notion page."""
    properties = page.get("properties", {})
    prop = properties.get(property_name, {})
    prop_type = prop.get("type")
    
    if not prop_type:
        return None
    
    if prop_type == "title":
        title_array = prop.get("title", [])
        if title_array:
            # Concatenate all text content from the title array
            return "".join([text.get("text", {}).get("content", "") for text in title_array if text.get("text", {}).get("content")])
        return ""
    
    elif prop_type == "rich_text":
        text_array = prop.get("rich_text", [])
        if text_array:
            # Concatenate all text content from the rich_text array
            return "".join([text.get("text", {}).get("content", "") for text in text_array if text.get("text", {}).get("content")])
        return ""
    
    elif prop_type == "select":
        return prop.get("select", {}).get("name")
    
    elif prop_type == "status":
        return prop.get("status", {}).get("name")
    
    elif prop_type == "url":
        return prop.get("url")
    
    elif prop_type == "date":
        return prop.get("date", {}).get("start")
    
    elif prop_type == "checkbox":
        return prop.get("checkbox")
    
    elif prop_type == "files":
        files = prop.get("files", [])
        if files and len(files) > 0:
            file = files[0]
            if file.get("type") == "external":
                return file.get("external", {}).get("url", "")
            elif file.get("type") == "file":
                return file.get("file", {}).get("url", "")
        return None
    
    return None

def get_page_display_title(page: Dict[str, Any]) -> str:
    """
    Extract a human-readable title from a Notion page.
    
    Args:
        page: Notion page object
        
    Returns:
        Human-readable title string
    """
    page_id = page["id"]
    properties = page.get("properties", {})
    
    # Get the full page title
    title_property = properties.get("Title", properties.get("Name", {}))
    
    if title_property and title_property.get("type") == "title":
        title_parts = []
        for text_obj in title_property.get("title", []):
            if text_obj.get("text", {}).get("content"):
                title_parts.append(text_obj["text"]["content"])
        title = "".join(title_parts) if title_parts else f"Untitled ({page_id[:8]})"
    else:
        # Fallback to the get_property_value function
        title = get_property_value(page, "Title") or get_property_value(page, "Name") or f"Untitled ({page_id[:8]})"
    
    return title

async def list_eligible_newsletter_pages(args):
    """
    List pages eligible for newsletter syncing.

    Args:
        args: Command line arguments
    """
    print("🔍 Finding eligible newsletters...")

    # Get database ID from args or use default
    database_id = getattr(args, "database", None) or WEBFLOW_CMS_DATABASE_ID

    # Get eligible pages
    eligible_pages = await get_eligible_newsletter_pages(database_id)

    if not eligible_pages:
        print("❌ No eligible newsletters found")
        return

    print(f"📄 Found {len(eligible_pages)} newsletter(s):")

    for i, page in enumerate(eligible_pages, 1):
        # Get page details
        title = get_page_display_title(page)
        status = get_property_value(page, "Newsletter Status")

        # Check if the blog post has been published to Webflow
        is_published, _, _ = await check_webflow_published(page)
        webflow_status = "✅ Published" if is_published else "❌ Not published"

        # Display page information
        print(f"   {i}. {title}")
        print(f"      Status: {status}")
        print(f"      Webflow: {webflow_status}")

async def newsletter_sync_command(args):
    """
    Send newsletters via Resend for eligible CMS pages.
    Takes pages with "Newsletter Status" = "To send" from the CMS database
    and sends newsletters via Resend, then updates status to "Sent".

    Args:
        args: Command line arguments
    """
    print("📧 Sending newsletters...")
    
    # Always use the CMS database
    database_id = WEBFLOW_CMS_DATABASE_ID
    
    # Get eligible pages
    eligible_pages = await get_eligible_newsletter_pages(database_id)
    
    if not eligible_pages:
        print("❌ No newsletters to send")
        return

    print(f"📄 Found {len(eligible_pages)} newsletter(s) to send")
    
    # SAFETY CONFIRMATION - Require user to type newsletter title(s) to confirm (unless --force is used)
    force_send = getattr(args, 'force', False)
    
    if force_send:
        print("⚠️ Force mode enabled")
    else:
        # Safety confirmation - require typing first 5 characters of the title
        if len(eligible_pages) == 1:
            title = get_page_display_title(eligible_pages[0])
            title_prefix = title[:5]
            user_input = input(f"Send newsletter '{title}'? Type first 5 characters to confirm: ").strip()
            if user_input.lower() != title_prefix.lower():
                print("❌ Confirmation failed")
                return
        else:
            count = len(eligible_pages)
            user_input = input(f"Send {count} newsletters? Type '{count}' to confirm: ").strip()
            if user_input != str(count):
                print("❌ Confirmation failed")
                return

        print("✅ Proceeding with newsletter send...")
    
    success_count = 0
    failure_count = 0
    
    for i, page in enumerate(eligible_pages, 1):
        page_id = page["id"]

        # Send newsletter via Resend (production mode - broadcast to audience)
        success, message, email_id = await send_newsletter_via_resend(page, test_mode=False)

        if success:
            # Update page status and last synced date
            current_status = get_property_value(page, "Newsletter Status")
            if current_status == "To send":
                await update_page_newsletter_status(page_id, "Sent")
            await update_page_last_synced(page_id)
            success_count += 1
        else:
            failure_count += 1

    # Clean summary
    if success_count > 0:
        print(f"✅ Sent {success_count} newsletter(s) successfully")
    if failure_count > 0:
        print(f"❌ {failure_count} newsletter(s) failed to send")


async def newsletter_test_command(args):
    """
    Test newsletter generation for eligible CMS pages without actually sending.
    Takes pages with "Newsletter Status" = "To send" from the CMS database
    and shows what would be sent via Resend, without actually sending or updating status.

    Args:
        args: Command line arguments
    """
    print("🧪 Testing newsletters...")

    # Get test email recipients from args or environment variable
    if hasattr(args, 'email') and args.email:
        test_recipients = args.email
        print(f"📧 Test emails: {', '.join(test_recipients)}")
    else:
        test_email = os.getenv("RESEND_TEST_EMAIL", "koii@koiibenvenutto.com")
        test_recipients = [test_email]
        print(f"📧 Test email: {test_email}")

    # Always use the CMS database
    database_id = WEBFLOW_CMS_DATABASE_ID

    # Get eligible pages
    eligible_pages = await get_eligible_newsletter_pages(database_id)

    if not eligible_pages:
        print("❌ No newsletters to test")
        return

    print(f"📄 Testing {len(eligible_pages)} newsletter(s)...")

    success_count = 0
    failure_count = 0

    for i, page in enumerate(eligible_pages, 1):
        page_id = page["id"]
        title = get_page_display_title(page)

        # Test newsletter generation (with actual TEST email sending)
        success, message, email_id = await test_newsletter_generation(page, test_recipients=test_recipients)

        if success:
            print(f"   {message}")
            success_count += 1
        else:
            print(f"   {message}")
            failure_count += 1

    # Clean summary
    if success_count > 0:
        print(f"✅ Sent {success_count} test email(s)")
        print(f"📬 Check {', '.join(test_recipients)} for test newsletters")
    if failure_count > 0:
        print(f"❌ {failure_count} test(s) failed")


async def test_newsletter_generation(page: Dict[str, Any], test_recipients: Optional[List[str]] = None) -> Tuple[bool, str, Optional[str]]:
    """
    Test newsletter generation for a page and send actual TEST email to safe recipients.

    Args:
        page: Notion page object
        test_recipients: Optional list of email addresses to send test to. If not provided, uses RESEND_TEST_EMAIL env var.

    Returns:
        Tuple of (success, message, email_id)
    """
    try:
        page_id = page["id"]
        
        # Get basic page properties
        properties = page.get("properties", {})
        
        # Check Webflow publishing status (but don't fail if not published)
        is_published, webflow_id, slug = await check_webflow_published(page)
        if not is_published:
            # Create a fallback slug from the title
            title = get_page_display_title(page)
            slug = title.lower().replace(' ', '-').replace(',', '').replace('.', '').replace('✨', '')
        
        # Get the website URL
        website_url = f"https://www.koiibenvenutto.com/post/{slug}"

        # Get subtitle (description)
        subtitle = get_property_value(page, "Description") or ""

        # Get cover image URL
        cover_image_url = get_cover_image_url(page)
        if cover_image_url:
            # Try to get Webflow-hosted version
            webflow_image_url = await get_webflow_hosted_image_url(page, cover_image_url)
            if webflow_image_url:
                # Check if we have a cached WebP version of this Webflow image
                from promaia.config.webp_cache import get_cached_webp_url
                cached_webp = get_cached_webp_url(webflow_image_url)
                if cached_webp:
                    cover_image_url = cached_webp
                else:
                    cover_image_url = webflow_image_url
        
        # Get page title
        title = get_page_display_title(page)
        
        # Convert page content to markdown
        try:
            blocks = await get_block_content(page_id)
            content_markdown = notion_blocks_to_markdown(blocks)
        except Exception as e:
            return False, f"❌ Error converting page content: {str(e)}", None

        # Generate newsletter content using new simple template
        html_newsletter_content = create_simple_newsletter_html(
            content_markdown=content_markdown,
            title=title,
            header_image_url=cover_image_url,
            subtitle=subtitle,
            post_link=website_url
        )

        # Replace Notion S3 image URLs with permanent Webflow CDN URLs
        if is_published and webflow_id:
            html_newsletter_content = replace_notion_images_with_webflow(
                html_newsletter_content, webflow_id, header_image=cover_image_url
            )

        # Also create plain text version for fallback
        newsletter_content = create_plain_text_newsletter(
            content_text=content_markdown,
            newsletter_title=title,
            subtitle=subtitle,
            post_link=website_url,
            cover_image_url=cover_image_url
        )

        # Get safe test recipients
        if test_recipients is None:
            test_email = os.getenv("RESEND_TEST_EMAIL", "koii@koiibenvenutto.com")
            test_recipients = [test_email]

        # Create TEST subject line
        test_subject = f"[TEST] {title}"
        
        # Send actual test email
        try:
            resend_client = get_resend_client(silent=True)
            
            result = resend_client.send_newsletter(
                subject=test_subject,
                plain_text=newsletter_content,
                html_content=html_newsletter_content,  # Use HTML content if available
                to_emails=test_recipients
            )
            
            if result["success"]:
                email_id = result["email_id"]
                success_message = f"✅ TEST email sent successfully (Email ID: {email_id})"
                if cover_image_url:
                    success_message += f" with header image"
                return True, success_message, email_id
            else:
                return False, f"❌ Failed to send TEST email: {result['error']}", None
                
        except Exception as e:
            return False, f"❌ Error sending TEST email: {str(e)}", None
        
    except Exception as e:
        return False, f"❌ Error testing newsletter generation: {str(e)}", None