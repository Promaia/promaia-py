"""
Dynamic Notion database schema discovery and property generation.
"""
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from promaia.notion.client import notion_client

async def get_database_schema(database_id: str) -> Dict[str, Any]:
    """
    Get the schema/properties of a Notion database.
    
    Args:
        database_id: The Notion database ID
        
    Returns:
        Dictionary with property definitions
    """
    try:
        database = await notion_client.databases.retrieve(database_id=database_id)
        return database.get("properties", {})
    except Exception as e:
        print(f"Error getting database schema for {database_id}: {str(e)}")
        return {}

def generate_property_defaults(schema: Dict[str, Any], title: str) -> Dict[str, Any]:
    """
    Generate default property values based on database schema.
    
    Args:
        schema: Database schema from get_database_schema()
        title: The page title
        
    Returns:
        Dictionary of property values ready for Notion API
    """
    properties = {}
    
    for prop_name, prop_config in schema.items():
        prop_type = prop_config.get("type")
        
        if prop_type == "title":
            # Title property - use the provided title
            properties[prop_name] = {
                "title": [{"text": {"content": title}}]
            }
            
        elif prop_type == "status":
            # Status property - use first available option or "To Do" style default
            status_options = prop_config.get("status", {}).get("options", [])
            if status_options:
                # Find a good default status (prefer "To Do", "Draft", "Not started", etc.)
                default_status = None
                preferred_defaults = ["To do", "To Do", "Draft", "Not started", "Planned", "New"]
                
                for preferred in preferred_defaults:
                    for option in status_options:
                        if option.get("name", "").lower() == preferred.lower():
                            default_status = option["name"]
                            break
                    if default_status:
                        break
                
                # If no preferred default found, use the first option
                if not default_status and status_options:
                    default_status = status_options[0]["name"]
                    
                if default_status:
                    properties[prop_name] = {"status": {"name": default_status}}
                    
        elif prop_type == "select":
            # Select property - use first available option or reasonable default
            select_options = prop_config.get("select", {}).get("options", [])
            if select_options:
                default_option = None
                
                # For Priority: prefer "2", "Medium", "Normal"
                if "priority" in prop_name.lower():
                    priority_defaults = ["2", "Medium", "Normal", "P2"]
                    for preferred in priority_defaults:
                        for option in select_options:
                            if option.get("name", "") == preferred:
                                default_option = option["name"]
                                break
                        if default_option:
                            break
                
                # For Story Points: prefer "2", "3", "1"
                elif "story" in prop_name.lower() and "point" in prop_name.lower():
                    points_defaults = ["2", "3", "1", "S", "M"]
                    for preferred in points_defaults:
                        for option in select_options:
                            if option.get("name", "") == preferred:
                                default_option = option["name"]
                                break
                        if default_option:
                            break
                
                # If no smart default found, use first option
                if not default_option:
                    default_option = select_options[0]["name"]
                    
                if default_option:
                    properties[prop_name] = {"select": {"name": default_option}}
                    
        elif prop_type == "date":
            # Date property - use today's date for reasonable defaults
            if "date" in prop_name.lower() or "created" in prop_name.lower():
                properties[prop_name] = {
                    "date": {"start": datetime.now().strftime("%Y-%m-%d")}
                }
                
        elif prop_type == "number":
            # Number property - default to 0 or 1
            properties[prop_name] = {"number": 1}
            
        elif prop_type == "checkbox":
            # Checkbox property - default to False
            properties[prop_name] = {"checkbox": False}
            
        elif prop_type == "rich_text":
            # Rich text property - leave empty
            properties[prop_name] = {"rich_text": []}
            
        elif prop_type == "multi_select":
            # Multi-select property - leave empty (user can add later)
            properties[prop_name] = {"multi_select": []}
            
        elif prop_type == "relation":
            # Relation property - leave empty (user can link later)
            properties[prop_name] = {"relation": []}
            
        elif prop_type == "people":
            # People property - leave empty (user can assign later)
            properties[prop_name] = {"people": []}
            
        # Skip read-only properties
        elif prop_type in ["created_time", "last_edited_time", "created_by", "last_edited_by"]:
            continue
            
        # For any other property types, skip with debug info
        else:
            print(f"Skipping unknown property type '{prop_type}' for property '{prop_name}'")
    
    return properties

async def create_page_with_schema(database_id: str, title: str, initial_content: str = "") -> Dict[str, Any]:
    """
    Create a page with properties automatically generated from database schema.
    
    Args:
        database_id: The Notion database ID
        title: Page title
        initial_content: Optional initial content
        
    Returns:
        Notion API response for the created page
    """
    # Get database schema
    schema = await get_database_schema(database_id)
    if not schema:
        raise ValueError(f"Could not retrieve schema for database {database_id}")
    
    # Generate properties
    properties = generate_property_defaults(schema, title)
    
    # Create children blocks if initial content provided
    children = []
    if initial_content:
        # Split long content into chunks that fit Notion's 2000 character limit
        NOTION_PARAGRAPH_LIMIT = 2000
        
        # If content is short enough, use a single paragraph
        if len(initial_content) <= NOTION_PARAGRAPH_LIMIT:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": initial_content}}]
                }
            })
        else:
            # Split content intelligently - try to break at sentences or paragraphs
            content_chunks = []
            remaining_content = initial_content
            
            while remaining_content:
                if len(remaining_content) <= NOTION_PARAGRAPH_LIMIT:
                    # Last chunk fits in one paragraph
                    content_chunks.append(remaining_content)
                    break
                
                # Find a good break point within the limit
                chunk_end = NOTION_PARAGRAPH_LIMIT
                
                # Try to break at sentence end (. ! ?)
                for sentence_end in ['. ', '! ', '? ']:
                    last_sentence = remaining_content[:chunk_end].rfind(sentence_end)
                    if last_sentence > chunk_end * 0.7:  # Don't break too early
                        chunk_end = last_sentence + len(sentence_end)
                        break
                else:
                    # If no good sentence break, try paragraph breaks (\n\n)
                    last_paragraph = remaining_content[:chunk_end].rfind('\n\n')
                    if last_paragraph > chunk_end * 0.7:
                        chunk_end = last_paragraph + 2
                    else:
                        # If no good break found, try single newline
                        last_newline = remaining_content[:chunk_end].rfind('\n')
                        if last_newline > chunk_end * 0.7:
                            chunk_end = last_newline + 1
                        else:
                            # Last resort: break at last space
                            last_space = remaining_content[:chunk_end].rfind(' ')
                            if last_space > chunk_end * 0.7:
                                chunk_end = last_space + 1
                
                # Extract chunk and continue with remaining
                chunk = remaining_content[:chunk_end].strip()
                content_chunks.append(chunk)
                remaining_content = remaining_content[chunk_end:].strip()
            
            # Create paragraph blocks for each chunk
            for chunk in content_chunks:
                if chunk:  # Skip empty chunks
                    children.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": chunk}}]
                        }
                    })
    
    # Create the page
    response = await notion_client.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=children
    )
    
    return response 