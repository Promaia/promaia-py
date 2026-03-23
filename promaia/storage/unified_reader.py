"""
Unified file reader that works with both JSON and markdown formats.
Automatically detects and uses the appropriate format based on database configuration.
"""
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

from promaia.storage.files import read_markdown_files
from promaia.storage.json_files import read_json_files
from promaia.config.databases import get_database_manager

def read_database_content(
    database_name: str,
    days: Optional[int] = None,
    target_data_source: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Read content from a database using the appropriate format.
    
    Args:
        database_name: Name of the database to read from
        days: Number of days back to read (None for all)
        target_data_source: Legacy parameter for backward compatibility
        
    Returns:
        List of content data dictionaries with standardized format
    """
    db_manager = get_database_manager()
    db_config = db_manager.get_database(database_name)
    
    if not db_config:
        print(f"Warning: Database '{database_name}' not found, trying legacy method")
        return _try_legacy_read(database_name, days, target_data_source)
    
    primary_format = db_config.primary_format
    content_type = db_config.nickname
    
    if primary_format == 'json':
        return _read_json_content(content_type, days)
    else:
        return _read_markdown_content(content_type, days, target_data_source)

def read_content_by_type(
    content_type: str,
    days: Optional[int] = None,
    preferred_format: str = 'auto'
) -> List[Dict[str, Any]]:
    """
    Read content by content type (nickname) with format preference.
    
    Args:
        content_type: Database nickname (e.g., "koii_journal", "koii_cms")
        days: Number of days back to read
        preferred_format: 'json', 'markdown', or 'auto'
        
    Returns:
        List of content data dictionaries
    """
    if preferred_format == 'json':
        return _read_json_content(content_type, days)
    elif preferred_format == 'markdown':
        return _read_markdown_content(content_type, days)
    else:  # auto
        # Try JSON first (preferred), fallback to markdown
        json_content = _read_json_content(content_type, days)
        if json_content:
            return json_content
        
        return _read_markdown_content(content_type, days)

def _read_json_content(content_type: str, days: Optional[int]) -> List[Dict[str, Any]]:
    """Read JSON content and convert to standardized format."""
    try:
        json_data = read_json_files(days=days, content_type=content_type)
        standardized_content = []
        
        for item in json_data:
            # Extract content from JSON structure
            notion_data = item.get('notion_data', {})
            content_blocks = notion_data.get('content', [])
            properties = notion_data.get('properties', {})
            
            # Convert content blocks to text (simplified)
            content_text = _extract_text_from_blocks(content_blocks)
            
            # Create standardized format similar to markdown reader
            standardized_item = {
                'filename': f"{item.get('title', 'untitled')} {item.get('page_id', '')}.json",
                'content': content_text,
                'date': _extract_date_from_properties(properties),
                'date_obj': datetime.fromisoformat(item.get('saved_at', '').replace('Z', '+00:00')) if item.get('saved_at') else datetime.min,
                'page_id': item.get('page_id'),
                'title': item.get('title'),
                'properties': properties,
                'format': 'json',
                'raw_data': item  # Keep original data for advanced use
            }
            standardized_content.append(standardized_item)
        
        return standardized_content
        
    except Exception as e:
        print(f"Error reading JSON content for {content_type}: {e}")
        return []

def _read_markdown_content(
    content_type: str, 
    days: Optional[int], 
    target_data_source: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Read markdown content using existing reader."""
    try:
        # Try to use registry-based reading first if database config is available
        from promaia.storage.files import load_database_pages_with_filters
        from promaia.config.databases import get_database_manager
        
        db_manager = get_database_manager()
        db_config = db_manager.get_database(content_type)
        
        if db_config:
            # Use registry-based reader for proper database handling
            md_data = load_database_pages_with_filters(db_config, days=days)
        else:
            # Fallback to legacy reader
            md_data = read_markdown_files(
                days=days, 
                content_type=content_type,
                target_data_source=target_data_source
            )
        
        # Add format indicator
        for item in md_data:
            item['format'] = 'markdown'
            
        return md_data
        
    except Exception as e:
        print(f"Error reading markdown content for {content_type}: {e}")
        return []

def _try_legacy_read(
    database_name: str, 
    days: Optional[int], 
    target_data_source: Optional[str]
) -> List[Dict[str, Any]]:
    """Try to read using legacy content type mapping."""
    # Map common database names to content types
    legacy_mapping = {
        'journal': 'journal',
        'cms': 'cms',
        'koii_journal': 'journal',
        'koii_cms': 'cms'
    }
    
    content_type = legacy_mapping.get(database_name, database_name)
    
    # Try JSON first, then markdown
    json_content = _read_json_content(content_type, days)
    if json_content:
        return json_content
    
    return _read_markdown_content(content_type, days, target_data_source)

def _extract_text_from_blocks(blocks: List[Dict[str, Any]]) -> str:
    """
    Extract text content from Notion blocks.
    This is a simplified version - you could enhance this further.
    """
    text_parts = []
    
    for block in blocks:
        block_type = block.get('type', '')
        
        if block_type in ['paragraph', 'heading_1', 'heading_2', 'heading_3']:
            rich_text = block.get(block_type, {}).get('rich_text', [])
            block_text = ''.join([rt.get('plain_text', '') for rt in rich_text])
            if block_text.strip():
                text_parts.append(block_text)
                
        elif block_type == 'bulleted_list_item':
            rich_text = block.get('bulleted_list_item', {}).get('rich_text', [])
            block_text = ''.join([rt.get('plain_text', '') for rt in rich_text])
            if block_text.strip():
                text_parts.append(f"• {block_text}")
                
        elif block_type == 'numbered_list_item':
            rich_text = block.get('numbered_list_item', {}).get('rich_text', [])
            block_text = ''.join([rt.get('plain_text', '') for rt in rich_text])
            if block_text.strip():
                text_parts.append(f"1. {block_text}")
                
        elif block_type == 'quote':
            rich_text = block.get('quote', {}).get('rich_text', [])
            block_text = ''.join([rt.get('plain_text', '') for rt in rich_text])
            if block_text.strip():
                text_parts.append(f"> {block_text}")
    
    return '\n\n'.join(text_parts)

def _extract_date_from_properties(properties: Dict[str, Any]) -> str:
    """Extract date string from Notion properties."""
    # Common date property names
    date_props = ['Date', 'Created', 'Created Time', 'Last Edited']
    
    for prop_name in date_props:
        if prop_name in properties:
            prop_data = properties[prop_name]
            prop_type = prop_data.get('type')
            
            if prop_type == 'date' and prop_data.get('date'):
                return prop_data['date'].get('start', 'Unknown Date')
            elif prop_type == 'created_time':
                return prop_data.get('created_time', 'Unknown Date')
            elif prop_type == 'last_edited_time':
                return prop_data.get('last_edited_time', 'Unknown Date')
    
    return 'Unknown Date'

# Backward compatibility functions
def load_cms_entries(days_to_load: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Backward compatibility function for CMS loading.
    Now uses unified reader with automatic format detection.
    """
    return read_content_by_type('cms', days=days_to_load)

def load_journal_entries(days_to_load: Optional[int] = None, target_data_source: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Backward compatibility function for journal loading.
    Now uses unified reader with automatic format detection.
    """
    return read_content_by_type('journal', days=days_to_load)