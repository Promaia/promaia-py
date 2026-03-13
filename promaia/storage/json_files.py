"""
JSON file storage operations for saving and reading raw Notion data.
"""
import os
import glob
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import re

from promaia.utils.env_writer import get_data_subdir

def get_json_output_dir(content_type: str) -> str:
    """
    Get the JSON output directory for a specific content type.
    Uses the new root-level structure: data/json/{content_type}/

    Args:
        content_type: Type of content (database nickname, e.g., "journal", "stories")
    Returns:
        Absolute path to the JSON output directory
    """
    return str(get_data_subdir() / "json" / content_type)

def ensure_json_output_dir(content_type: str):
    """Ensure the JSON output directory exists."""
    output_dir_path = get_json_output_dir(content_type)
    os.makedirs(output_dir_path, exist_ok=True)

async def save_page_to_json(page_id: str, title: str, page_data: Dict[str, Any], content_type: str = "journal") -> str:
    """
    Save a page's raw data to a JSON file.
    
    Args:
        page_id: ID of the page
        title: Title of the page
        page_data: Complete page data from Notion (properties, content blocks, etc.)
        content_type: Type of content (database nickname, e.g., "journal")
        
    Returns:
        Path to the saved JSON file
    """
    ensure_json_output_dir(content_type)
    output_dir = get_json_output_dir(content_type)
    
    # Create a safe filename from the title
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    
    # Create the filename with title and page ID
    filename = f"{safe_title} {page_id}.json"
    filepath = os.path.join(output_dir, filename)
    
    # Add metadata to the page data
    enhanced_data = {
        "page_id": page_id,
        "title": title,
        "saved_at": datetime.now().isoformat(),
        "content_type": content_type,
        "notion_data": page_data
    }
    
    # Save the JSON data to the file
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(enhanced_data, f, indent=2, ensure_ascii=False)
    
    return filepath

def read_json_files(
    days: Optional[int] = None, 
    content_type: str = "journal"
) -> List[Dict[str, Any]]:
    """
    Read JSON files from the specified content type directory.
    
    Args:
        days: Number of days back to read (None for all)
        content_type: Database nickname to read from (e.g., "journal")
        
    Returns:
        List of page data dictionaries
    """
    data_directory_path = get_json_output_dir(content_type)
    
    if not os.path.isdir(data_directory_path):
        print(f"Error: JSON directory not found: {data_directory_path}")
        return []
    
    pages = []
    json_files_glob = os.path.join(data_directory_path, "*.json")
    json_files = glob.glob(json_files_glob)
    
    total_files = 0
    filtered_files = 0
    all_dates = []
    
    # Collect all dates from saved_at timestamps for reference
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                saved_at = data.get('saved_at')
                if saved_at:
                    try:
                        file_date = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                        all_dates.append(file_date)
                    except ValueError:
                        pass
        except Exception:
            pass
    
    if all_dates:
        all_dates.sort(reverse=True)
    
    cutoff_date = None
    if days is not None:
        reference_date = max(all_dates) if all_dates else datetime.now()
        cutoff_date = reference_date - timedelta(days=days)
    
    # Process files
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            total_files += 1
            
            # Check date filter
            if cutoff_date:
                saved_at = data.get('saved_at')
                if saved_at:
                    try:
                        file_date = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                        if file_date < cutoff_date:
                            continue
                    except ValueError:
                        continue
            
            pages.append(data)
            filtered_files += 1
            
        except Exception as e:
            print(f"Error reading JSON file {file_path}: {e}")
            continue
    
    print(f"Read {filtered_files} JSON files out of {total_files} total files")
    
    # Sort by saved_at timestamp (newest first)
    pages.sort(key=lambda x: x.get('saved_at', ''), reverse=True)
    
    return pages

def get_existing_json_page_ids(content_type: str = "journal") -> List[str]:
    """
    Get list of existing page IDs from JSON files.
    
    Args:
        content_type: Database nickname (e.g., "journal")
        
    Returns:
        List of page IDs that already exist locally
    """
    data_directory_path = get_json_output_dir(content_type)
    
    if not os.path.isdir(data_directory_path):
        return []
    
    page_ids = []
    json_files_glob = os.path.join(data_directory_path, "*.json")
    json_files = glob.glob(json_files_glob)
    
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                page_id = data.get('page_id')
                if page_id:
                    page_ids.append(page_id)
        except Exception:
            continue
    
    return page_ids

def get_json_file_path(title: str, page_id: str, content_type: str = "journal") -> str:
    """
    Construct the expected filepath for a JSON file.
    
    Args:
        title: The title of the page
        page_id: The Notion page ID
        content_type: Database nickname (e.g., "journal")
        
    Returns:
        The predicted absolute filepath for the JSON file
    """
    output_dir = get_json_output_dir(content_type)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    filename = f"{safe_title} {page_id}.json"
    return os.path.join(output_dir, filename)

def cleanup_old_json_files(days: int = 30, content_type: str = "journal") -> int:
    """
    Remove JSON files older than the specified number of days.
    
    Args:
        days: Number of days (files older than this will be removed)
        content_type: Database nickname (e.g., "journal")
        
    Returns:
        Number of files removed
    """
    if days <= 0:
        return 0
        
    data_directory_path = get_json_output_dir(content_type)
    
    if not os.path.isdir(data_directory_path):
        return 0
    
    cutoff_date = datetime.now() - timedelta(days=days)
    removed_count = 0
    
    json_files_glob = os.path.join(data_directory_path, "*.json")
    json_files = glob.glob(json_files_glob)
    
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                saved_at = data.get('saved_at')
                if saved_at:
                    file_date = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                    if file_date < cutoff_date:
                        os.remove(file_path)
                        removed_count += 1
        except Exception:
            continue
    
    return removed_count