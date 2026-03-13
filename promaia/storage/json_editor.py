"""
JSON Editor for Notion Pages

This module provides functionality to edit local JSON files containing Notion page data.
It includes safe editing, change tracking, and validation.
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass
from pathlib import Path

@dataclass
class EditChange:
    """Represents a change made to a JSON file"""
    field: str
    old_value: Any
    new_value: Any
    timestamp: datetime
    
class NotionJSONEditor:
    """Editor for local Notion JSON files with change tracking"""
    
    def __init__(self, workspace_root: str = None):
        if workspace_root:
            self.workspace_root = workspace_root
            self.data_dir = os.path.join(self.workspace_root, "data")
        else:
            from promaia.utils.env_writer import get_data_dir, get_data_subdir
            self.workspace_root = str(get_data_dir())
            self.data_dir = str(get_data_subdir())
        
    def load_page(self, content_type: str, page_id: str) -> Dict[str, Any]:
        """Load a page from JSON storage"""
        # Try different possible paths
        possible_paths = [
            os.path.join(self.data_dir, "json", content_type, f"{page_id}.json"),
            os.path.join(self.data_dir, content_type, "json", f"{page_id}.json"),
            os.path.join(self.data_dir, "md", "notion", content_type, f"{page_id}.json")
        ]
        
        for json_path in possible_paths:
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        
        raise FileNotFoundError(f"Page {page_id} not found for content type {content_type}")
    
    def save_page(self, content_type: str, page_data: Dict[str, Any]) -> str:
        """Save page data to JSON storage"""
        page_id = page_data.get('page_id') or page_data.get('id')
        if not page_id:
            raise ValueError("Page data must contain page_id or id field")
            
        # Update saved_at timestamp
        page_data['saved_at'] = datetime.utcnow().isoformat() + 'Z'
        
        json_dir = os.path.join(self.data_dir, "json", content_type)
        os.makedirs(json_dir, exist_ok=True)
        
        json_path = os.path.join(json_dir, f"{page_id}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(page_data, f, indent=2, ensure_ascii=False)
            
        return json_path
    
    def update_title(self, page_data: Dict[str, Any], new_title: str) -> Dict[str, Any]:
        """Update the title of a page"""
        updated_data = page_data.copy()
        updated_data['title'] = new_title
        
        # Also update the title in Notion properties if present
        if 'notion_data' in updated_data and 'properties' in updated_data['notion_data']:
            properties = updated_data['notion_data']['properties']
            
            # Find title property
            for prop_name, prop_data in properties.items():
                if prop_data.get('type') == 'title':
                    prop_data['title'] = [{
                        "type": "text",
                        "text": {"content": new_title},
                        "plain_text": new_title
                    }]
                    break
        
        return updated_data
    
    def update_property(self, page_data: Dict[str, Any], prop_name: str, prop_value: Any, prop_type: str = None) -> Dict[str, Any]:
        """Update a property in the page data"""
        updated_data = page_data.copy()
        
        if 'notion_data' not in updated_data:
            updated_data['notion_data'] = {'properties': {}}
        
        if 'properties' not in updated_data['notion_data']:
            updated_data['notion_data']['properties'] = {}
        
        properties = updated_data['notion_data']['properties']
        
        # Determine property type if not provided
        if not prop_type and prop_name in properties:
            prop_type = properties[prop_name].get('type', 'rich_text')
        elif not prop_type:
            prop_type = 'rich_text'
        
        # Format value based on type
        if prop_type == 'title':
            formatted_value = [{
                "type": "text",
                "text": {"content": str(prop_value)},
                "plain_text": str(prop_value)
            }]
        elif prop_type == 'rich_text':
            formatted_value = [{
                "type": "text",
                "text": {"content": str(prop_value)},
                "plain_text": str(prop_value)
            }]
        elif prop_type == 'select':
            if isinstance(prop_value, dict):
                formatted_value = prop_value
            else:
                formatted_value = {"name": str(prop_value), "color": "default"}
        else:
            formatted_value = prop_value
        
        properties[prop_name] = {
            "type": prop_type,
            prop_type: formatted_value
        }
        
        return updated_data
    
    def add_paragraph(self, page_data: Dict[str, Any], text: str) -> Dict[str, Any]:
        """Add a paragraph block to the page content"""
        updated_data = page_data.copy()
        
        if 'notion_data' not in updated_data:
            updated_data['notion_data'] = {'content': []}
        
        if 'content' not in updated_data['notion_data']:
            updated_data['notion_data']['content'] = []
        
        paragraph_block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": text},
                    "plain_text": text
                }]
            }
        }
        
        updated_data['notion_data']['content'].append(paragraph_block)
        return updated_data
    
    def add_heading(self, page_data: Dict[str, Any], text: str, level: int = 1) -> Dict[str, Any]:
        """Add a heading block to the page content"""
        updated_data = page_data.copy()
        
        if 'notion_data' not in updated_data:
            updated_data['notion_data'] = {'content': []}
        
        if 'content' not in updated_data['notion_data']:
            updated_data['notion_data']['content'] = []
        
        heading_type = f"heading_{level}"
        heading_block = {
            "type": heading_type,
            heading_type: {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": text},
                    "plain_text": text
                }]
            }
        }
        
        updated_data['notion_data']['content'].append(heading_block)
        return updated_data
    
    def list_pages(self, content_type: str, title_filter: str = None) -> List[Dict[str, Any]]:
        """List all pages for a content type"""
        json_dir = os.path.join(self.data_dir, "json", content_type)
        pages = []
        
        if not os.path.exists(json_dir):
            return pages
            
        for filename in os.listdir(json_dir):
            if filename.endswith('.json'):
                try:
                    with open(os.path.join(json_dir, filename), 'r', encoding='utf-8') as f:
                        page_data = json.load(f)
                        
                    title = page_data.get('title', 'Untitled')
                    if title_filter and title_filter.lower() not in title.lower():
                        continue
                        
                    pages.append({
                        'page_id': page_data.get('page_id') or page_data.get('id'),
                        'title': title,
                        'saved_at': page_data.get('saved_at', 'Unknown'),
                        'last_synced': page_data.get('last_synced', 'Never')
                    })
                except Exception as e:
                    print(f"Error reading {filename}: {e}")
                    
        return sorted(pages, key=lambda x: x.get('saved_at', ''), reverse=True)
    
    def track_changes(self, old_data: Dict[str, Any], new_data: Dict[str, Any]) -> List[EditChange]:
        """Track changes between old and new data"""
        changes = []
        # Simplified change tracking - could be expanded
        
        if old_data.get('title') != new_data.get('title'):
            changes.append(EditChange(
                field='title',
                old_value=old_data.get('title'),
                new_value=new_data.get('title'),
                timestamp=datetime.utcnow()
            ))
        
        return changes
