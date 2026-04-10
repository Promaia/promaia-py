"""
Notion synchronization for OCR results.

Handles creating and updating Notion pages with OCR content.
"""
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from datetime import datetime

from promaia.notion.client import get_client
from promaia.notion.schema import get_database_schema, generate_property_defaults
from promaia.storage.ocr_storage import OCRStorage
from promaia.ocr.markdown_to_notion import markdown_to_notion_blocks

if TYPE_CHECKING:
    from promaia.ocr.processor import ProcessedDocument

logger = logging.getLogger(__name__)


def get_notion_client(workspace: str = None):
    """Get Notion client for workspace."""
    return get_client(workspace)


def extract_database_id_from_url(url: str) -> Optional[str]:
    """
    Extract Notion database ID from a URL.

    Supports formats:
    - https://www.notion.so/workspace/DATABASE_ID?v=...
    - https://notion.so/DATABASE_ID
    - DATABASE_ID (raw ID)

    Args:
        url: Notion database URL or ID

    Returns:
        Database ID or None if invalid
    """
    # If it's already just an ID (32 chars, alphanumeric/hyphens)
    if re.match(r'^[a-f0-9]{32}$', url.replace('-', '')):
        return url.replace('-', '')

    # Extract from URL
    # Pattern: notion.so/anything/DATABASE_ID or notion.so/DATABASE_ID
    patterns = [
        r'notion\.so/[^/]+/([a-f0-9]{32})',  # With workspace
        r'notion\.so/([a-f0-9]{32})',         # Direct
        r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',  # UUID format
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1).replace('-', '')

    logger.error(f"Could not extract database ID from: {url}")
    return None


async def verify_database_access(database_id: str, workspace: str = None) -> bool:
    """
    Verify we have access to a Notion database.

    Args:
        database_id: Notion database ID
        workspace: Workspace name (optional)

    Returns:
        True if accessible
    """
    try:
        client = get_notion_client(workspace)
        db = await client.databases.retrieve(database_id=database_id)
        return True
    except Exception as e:
        logger.error(f"Cannot access database {database_id}: {e}")
        return False


async def get_or_create_ocr_properties(database_id: str, workspace: str = None) -> Dict[str, str]:
    """
    Get existing properties or suggest properties for OCR database.

    Args:
        database_id: Notion database ID
        workspace: Workspace name (optional)

    Returns:
        Dict mapping property purpose to property name
    """
    client = get_notion_client(workspace)

    # Get schema using the client
    try:
        database = await client.databases.retrieve(database_id=database_id)
        schema = database.get("properties", {})
    except Exception as e:
        logger.error(f"Error getting database schema: {e}")
        schema = {}

    # Map property purposes to property names
    property_map = {}

    # Find title property
    for prop_name, prop_config in schema.items():
        if prop_config.get("type") == "title":
            property_map["title"] = prop_name
            break

    # Find other properties by name (case-insensitive)
    name_mapping = {
        "upload_date": ["Upload Date", "Uploaded", "Upload"],
        "processing_date": ["Processing Date", "Processed", "OCR Date"],
        "confidence": ["OCR Confidence", "Confidence", "Quality"],
        "status": ["Status", "State"],
        "source_image": ["Source Image", "Image", "Original"],
        "language": ["Language", "Lang"],
        "text_length": ["Text Length", "Length", "Characters"],
        "notes": ["Notes", "Comments", "Remarks"]
    }

    for purpose, possible_names in name_mapping.items():
        for prop_name in schema.keys():
            if prop_name in possible_names:
                property_map[purpose] = prop_name
                break

    return property_map


def get_recommended_database_schema() -> Dict[str, Dict[str, Any]]:
    """
    Get recommended schema for OCR uploads database.

    Returns:
        Dict of property definitions
    """
    return {
        "Title": {"type": "title"},
        "Upload Date": {"type": "date"},
        "Processing Date": {"type": "date"},
        "OCR Confidence": {
            "type": "number",
            "number": {"format": "percent"}
        },
        "Status": {
            "type": "select",
            "select": {
                "options": [
                    {"name": "Pending", "color": "gray"},
                    {"name": "Processing", "color": "blue"},
                    {"name": "Completed", "color": "green"},
                    {"name": "Failed", "color": "red"},
                    {"name": "Review Needed", "color": "yellow"}
                ]
            }
        },
        "Source Image": {"type": "files"},
        "Language": {
            "type": "select",
            "select": {
                "options": [
                    {"name": "English", "color": "blue"},
                    {"name": "Spanish", "color": "orange"},
                    {"name": "French", "color": "purple"},
                    {"name": "Unknown", "color": "gray"}
                ]
            }
        },
        "Text Length": {"type": "number"},
        "Notes": {"type": "rich_text"}
    }


async def create_notion_page_from_ocr(
    database_id: str,
    doc: "ProcessedDocument",
    workspace: str
) -> Optional[str]:
    """
    Create a Notion page from OCR result.

    Args:
        database_id: Notion database ID
        doc: ProcessedDocument from OCR processor
        workspace: Workspace name

    Returns:
        Page ID if successful, None otherwise
    """
    try:
        client = get_notion_client(workspace)

        # Get property mapping
        property_map = await get_or_create_ocr_properties(database_id, workspace)

        # Generate title
        title = doc.image_path.stem

        # Build properties
        properties = {}

        # Title
        if "title" in property_map:
            properties[property_map["title"]] = {
                "title": [{"text": {"content": title}}]
            }

        # Upload Date
        if "upload_date" in property_map and doc.image_path.exists():
            upload_time = datetime.fromtimestamp(doc.image_path.stat().st_mtime)
            properties[property_map["upload_date"]] = {
                "date": {"start": upload_time.isoformat()}
            }

        # Processing Date
        if "processing_date" in property_map:
            properties[property_map["processing_date"]] = {
                "date": {"start": datetime.now().isoformat()}
            }

        # OCR Confidence
        if "confidence" in property_map and doc.ocr_result:
            properties[property_map["confidence"]] = {
                "number": doc.ocr_result.confidence
            }

        # Status
        if "status" in property_map:
            status_name = {
                "completed": "Completed",
                "review_needed": "Review Needed",
                "failed": "Failed",
                "pending": "Pending"
            }.get(doc.status, "Completed")

            properties[property_map["status"]] = {
                "select": {"name": status_name}
            }

        # Language
        if "language" in property_map and doc.ocr_result:
            lang_name = {
                "en": "English",
                "es": "Spanish",
                "fr": "French"
            }.get(doc.ocr_result.language, "Unknown")

            properties[property_map["language"]] = {
                "select": {"name": lang_name}
            }

        # Text Length
        if "text_length" in property_map and doc.ocr_result:
            properties[property_map["text_length"]] = {
                "number": len(doc.ocr_result.text) if doc.ocr_result.text else 0
            }

        # Create page content (blocks) from markdown
        children = []

        if doc.ocr_result and doc.ocr_result.text:
            # Convert markdown to Notion blocks
            children = markdown_to_notion_blocks(
                doc.ocr_result.text,
                max_blocks=100  # Notion API limit
            )

        # Create the page
        page = await client.pages.create(
            parent={"database_id": database_id},
            properties=properties,
            children=children if children else None
        )

        page_id = page["id"]
        logger.info(f"Created Notion page {page_id} for {doc.image_path.name}")

        # Upload source image if property exists
        if "source_image" in property_map and doc.processed_image_path:
            await upload_image_to_page(page_id, property_map["source_image"], doc.processed_image_path)

        return page_id

    except Exception as e:
        logger.error(f"Error creating Notion page: {e}")
        return None


async def upload_image_to_page(
    page_id: str,
    property_name: str,
    image_path: Path
) -> bool:
    """
    Upload an image to a Notion page's file property.

    Note: Notion API doesn't support direct file uploads.
    This is a placeholder for future enhancement using external hosting.

    Args:
        page_id: Notion page ID
        property_name: Name of files property
        image_path: Path to image file

    Returns:
        True if successful
    """
    # TODO: Implement file upload via external hosting (S3, etc.)
    # For now, just log that we would upload
    logger.info(f"Would upload {image_path} to page {page_id} property {property_name}")
    logger.info("Note: Notion API doesn't support direct file uploads. Consider external hosting.")
    return False


async def sync_ocr_results_to_notion(
    database_id: str,
    workspace: str,
    limit: Optional[int] = None
) -> Dict[str, int]:
    """
    Sync OCR results to Notion database.

    Args:
        database_id: Notion database ID
        workspace: Workspace name
        limit: Max number of results to sync

    Returns:
        Dict with sync statistics
    """
    storage = OCRStorage()
    stats = {
        "total": 0,
        "created": 0,
        "skipped": 0,
        "failed": 0
    }

    try:
        # Get OCR results that haven't been synced to Notion
        uploads = storage.get_by_workspace(workspace)

        # Filter to only unsynced results
        unsynced = [u for u in uploads if not u.get("page_id")]

        if limit:
            unsynced = unsynced[:limit]

        stats["total"] = len(unsynced)

        logger.info(f"Syncing {len(unsynced)} OCR results to Notion")

        # Import ProcessedDocument locally to avoid circular import
        from promaia.ocr.processor import ProcessedDocument

        for upload in unsynced:
            # Reconstruct ProcessedDocument from storage
            doc = ProcessedDocument(
                image_path=Path(upload["source_image_path"]),
                ocr_result=None,  # Will be populated from markdown
                markdown_path=Path(upload["file_path"]) if upload.get("file_path") else None,
                processed_image_path=Path(upload["processed_image_path"]) if upload.get("processed_image_path") else None,
                status=upload["status"]
            )

            # Read OCR text from markdown if available
            if doc.markdown_path and doc.markdown_path.exists():
                with open(doc.markdown_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Extract text (skip frontmatter)
                    if content.startswith('---'):
                        parts = content.split('---', 2)
                        if len(parts) >= 3:
                            text = parts[2].strip()
                        else:
                            text = content
                    else:
                        text = content

                    from promaia.ocr.engines.base import OCRResult
                    doc.ocr_result = OCRResult(
                        text=text,
                        confidence=upload.get("ocr_confidence", 0.0),
                        language=upload.get("language", "unknown")
                    )

            # Create Notion page
            page_id = await create_notion_page_from_ocr(database_id, doc, workspace)

            if page_id:
                # Update storage with page ID
                storage.update_page_id(upload["source_image_path"], page_id)
                stats["created"] += 1
                logger.info(f"✓ Synced {doc.image_path.name}")
            else:
                stats["failed"] += 1
                logger.error(f"✗ Failed to sync {doc.image_path.name}")

        return stats

    except Exception as e:
        logger.error(f"Error syncing OCR results: {e}")
        return stats
