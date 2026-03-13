import os
from datetime import datetime
from typing import Optional

from promaia.utils.env_writer import get_data_subdir, get_data_dir

def get_md_output_dir(content_type: str, workspace: str = None) -> str:
    """
    Get the markdown output directory for a specific content type and workspace.
    Uses the structure: 
    - data/{workspace}/md/{content_type}/ for all workspaces
    
    Args:
        content_type: Type of content (database nickname, e.g., "journal", "stories")
        workspace: Workspace name (optional, defaults to "default")
    Returns:
        Absolute path to the markdown output directory
    """
    if not workspace:
        workspace = "default"
    
    return str(get_data_subdir() / workspace / "md" / content_type)

def get_md_output_dir_for_database(database_name: str) -> str:
    """
    Get the markdown output directory for a specific database by looking up its configuration.
    
    Args:
        database_name: Database name or qualified name (workspace.database)
    Returns:
        Absolute path to the markdown output directory
    """
    try:
        from promaia.config.databases import get_database_config
        
        # Parse workspace.database format if provided
        workspace = None
        db_name = database_name
        if '.' in database_name:
            workspace, db_name = database_name.split('.', 1)
        
        db_config = get_database_config(db_name, workspace)
        if db_config:
            # Use the actual markdown_directory from the database configuration
            # This already has the correct unified storage path
            return str(get_data_dir() / db_config.markdown_directory)
        else:
            # Fall back to simple content type lookup for backward compatibility
            return get_md_output_dir(database_name)
    except ImportError:
        # Fall back if config module not available
        return get_md_output_dir(database_name)

def save_page_to_markdown_file(page_json_data: dict, markdown_content: str) -> str:
    """
    Saves the provided markdown content to a file, deriving naming from page_json_data.
    Now workspace-aware based on the database configuration.

    Args:
        page_json_data: The Python dictionary loaded from the JSON file for this page.
                        Used to get content_type, page_id, and title for naming.
        markdown_content: The actual markdown string to save.
    
    Returns:
        Absolute path to the saved markdown file.
    """
    content_type = page_json_data.get("content_type")
    page_id = page_json_data.get("page_id")
    title = page_json_data.get("title", "untitled")

    if not all([content_type, page_id]):
        raise ValueError("page_json_data must contain 'content_type' and 'page_id'")

    # Use workspace-aware directory lookup
    md_dir = get_md_output_dir_for_database(content_type)
    os.makedirs(md_dir, exist_ok=True)

    # Create a safe filename from the title
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    
    # Extract date for filename prefix  
    date_prefix = ""
    try:
        # Try to get created_time from page_json_data or other date fields
        created_time_str = None
        
        # Check for various date fields that might be available
        if 'created_time' in page_json_data:
            created_time_str = page_json_data['created_time']
        elif 'date' in page_json_data:
            created_time_str = page_json_data['date']
        elif 'saved_at' in page_json_data:
            created_time_str = page_json_data['saved_at']
        
        if created_time_str:
            # Parse the date and format as YYYY-MM-DD
            from datetime import datetime
            if isinstance(created_time_str, str):
                created_dt = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
                date_prefix = created_dt.strftime("%Y-%m-%d") + " "
                print(f"Using date {created_time_str} for markdown file prefix: {date_prefix}")
            elif hasattr(created_time_str, 'strftime'):  # datetime object
                date_prefix = created_time_str.strftime("%Y-%m-%d") + " "
                print(f"Using datetime object for markdown file prefix: {date_prefix}")
        
        if not date_prefix:
            print(f"No date found for page {page_id}, using current date")
            date_prefix = datetime.now().strftime("%Y-%m-%d") + " "
            
    except Exception as e:
        print(f"Error extracting date for page {page_id}: {e}, using current date")
        date_prefix = datetime.now().strftime("%Y-%m-%d") + " "
    
    filename = f"{date_prefix}{safe_title} {page_id}.md" # Use page_id for uniqueness
    filepath = os.path.join(md_dir, filename)

    # Add a header to the markdown file for context
    conversion_header = f"""<!-- Derived from JSON on {datetime.now().isoformat()} -->
<!-- Original page_id: {page_id} -->
<!-- Original title: {title} -->

"""
    full_markdown_content = conversion_header + markdown_content

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(full_markdown_content)
    
    print(f"Saved markdown to: {filepath}") # Optional: for logging
    return filepath 