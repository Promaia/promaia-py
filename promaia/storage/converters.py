"""
Format conversion utilities for Maia storage.
Handles converting between JSON, markdown, and other formats on-demand.
"""
import os
import glob
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from promaia.markdown.converter import page_to_markdown
from promaia.storage.json_files import get_json_output_dir, read_json_files

# Determine Project Root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def get_markdown_output_dir(content_type: str) -> str:
    """
    Get the markdown output directory for a specific content type.
    Uses the new root-level structure: data/md/{content_type}/
    
    Args:
        content_type: Type of content (e.g., "koii_journal", "koii_stories", etc.)
    Returns:
        Absolute path to the markdown output directory
    """
    return os.path.join(PROJECT_ROOT, "data", "md", content_type)

def ensure_markdown_output_dir(content_type: str):
    """Ensure the markdown output directory exists."""
    output_dir_path = get_markdown_output_dir(content_type)
    os.makedirs(output_dir_path, exist_ok=True)

async def convert_json_to_markdown(
    content_type: str, 
    days: Optional[int] = None,
    specific_files: Optional[List[str]] = None,
    include_properties: bool = True
) -> List[str]:
    """
    Convert JSON files to markdown format.
    
    Args:
        content_type: Database nickname (e.g., "koii_journal")
        days: Number of days back to convert (None for all)
        specific_files: List of specific JSON file paths to convert
        include_properties: Whether to include properties in markdown
        
    Returns:
        List of created markdown file paths
    """
    ensure_markdown_output_dir(content_type)
    markdown_dir = get_markdown_output_dir(content_type)
    created_files = []
    
    if specific_files:
        # Convert specific files
        for json_file_path in specific_files:
            try:
                with open(json_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                markdown_path = await _convert_single_json_to_markdown(
                    data, markdown_dir, include_properties
                )
                if markdown_path:
                    created_files.append(markdown_path)
                    
            except Exception as e:
                print(f"Error converting {json_file_path}: {e}")
    else:
        # Convert from JSON directory
        json_files_data = read_json_files(days=days, content_type=content_type)
        
        for data in json_files_data:
            try:
                markdown_path = await _convert_single_json_to_markdown(
                    data, markdown_dir, include_properties
                )
                if markdown_path:
                    created_files.append(markdown_path)
                    
            except Exception as e:
                print(f"Error converting JSON data: {e}")
    
    print(f"Converted {len(created_files)} files to markdown in {markdown_dir}")
    return created_files

async def _convert_single_json_to_markdown(
    json_data: Dict[str, Any], 
    output_dir: str, 
    include_properties: bool = True
) -> Optional[str]:
    """
    Convert a single JSON data object to markdown file.
    
    Args:
        json_data: The JSON data from a saved file
        output_dir: Directory to save markdown file
        include_properties: Whether to include properties in markdown
        
    Returns:
        Path to created markdown file, or None if failed
    """
    try:
        # Extract data from JSON structure
        page_id = json_data.get("page_id")
        title = json_data.get("title", "untitled")
        notion_data = json_data.get("notion_data", {})
        
        if not page_id:
            print(f"No page_id found in JSON data")
            return None
        
        # Get content blocks and properties
        content_blocks = notion_data.get("content", [])
        properties = notion_data.get("properties", {}) if include_properties else None
        
        # Convert to markdown
        markdown_content = page_to_markdown(
            content_blocks, 
            properties=properties,
            include_properties=include_properties
        )
        
        # Create filename
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        filename = f"{safe_title} {page_id}.md"
        file_path = os.path.join(output_dir, filename)
        
        # Add conversion metadata header
        conversion_header = f"""<!-- Converted from JSON on {datetime.now().isoformat()} -->
<!-- Original page_id: {page_id} -->
<!-- Original title: {title} -->

"""
        
        # Save markdown file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(conversion_header + markdown_content)
        
        return file_path
        
    except Exception as e:
        print(f"Error in _convert_single_json_to_markdown: {e}")
        return None

def convert_markdown_to_json(
    content_type: str,
    days: Optional[int] = None,
    specific_files: Optional[List[str]] = None
) -> List[str]:
    """
    Convert markdown files to JSON format.
    Note: This is limited because markdown doesn't contain full Notion metadata.
    
    Args:
        content_type: Database nickname
        days: Number of days back to convert (None for all)
        specific_files: List of specific markdown file paths to convert
        
    Returns:
        List of created JSON file paths
    """
    # This would be more complex to implement properly since markdown
    # loses a lot of the original Notion structure and metadata
    # For now, we'll focus on the JSON->Markdown direction
    print("Markdown to JSON conversion not yet implemented")
    print("Recommended workflow: Keep JSON as source of truth, convert to markdown as needed")
    return []

def cleanup_converted_files(content_type: str, format_type: str = "md", days: int = 7) -> int:
    """
    Clean up old converted files.
    
    Args:
        content_type: Database nickname
        format_type: Format to clean up ("md" for markdown)
        days: Remove files older than this many days
        
    Returns:
        Number of files removed
    """
    if format_type == "md":
        target_dir = get_markdown_output_dir(content_type)
        file_pattern = "*.md"
    else:
        print(f"Cleanup for format {format_type} not implemented")
        return 0
    
    if not os.path.exists(target_dir):
        return 0
    
    cutoff_date = datetime.now().timestamp() - (days * 24 * 60 * 60)
    removed_count = 0
    
    files = glob.glob(os.path.join(target_dir, file_pattern))
    for file_path in files:
        try:
            if os.path.getmtime(file_path) < cutoff_date:
                os.remove(file_path)
                removed_count += 1
        except Exception as e:
            print(f"Error removing {file_path}: {e}")
    
    return removed_count

def list_available_formats(content_type: str) -> Dict[str, Dict[str, Any]]:
    """
    List available formats for a content type and their file counts.
    
    Args:
        content_type: Database nickname
        
    Returns:
        Dictionary with format info
    """
    formats = {}
    
    # Check JSON
    json_dir = get_json_output_dir(content_type)
    if os.path.exists(json_dir):
        json_files = glob.glob(os.path.join(json_dir, "*.json"))
        formats["json"] = {
            "directory": json_dir,
            "file_count": len(json_files),
            "latest_file": max(json_files, key=os.path.getmtime) if json_files else None
        }
    
    # Check Markdown
    md_dir = get_markdown_output_dir(content_type)
    if os.path.exists(md_dir):
        md_files = glob.glob(os.path.join(md_dir, "*.md"))
        formats["markdown"] = {
            "directory": md_dir,
            "file_count": len(md_files),
            "latest_file": max(md_files, key=os.path.getmtime) if md_files else None
        }
    
    return formats