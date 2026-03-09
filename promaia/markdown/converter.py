"""
Convert Notion blocks to markdown format.
"""
from typing import Dict, Any, List, Optional
import re

def block_to_markdown(block: Dict[str, Any], level: int = 0, subpage_data: Optional[Dict[str, str]] = None) -> str:
    """
    Convert a Notion block to markdown format.
    
    Args:
        block: Notion block object to convert
        level: Indentation level for nested blocks
        subpage_data: Optional dictionary mapping page URLs to their content
        
    Returns:
        Markdown representation of the block
    """
    block_type = block["type"]
    content = block[block_type]
    markdown = ""
    indent = "    " * level

    try:
        # Extract text content from any block type
        if block_type == "code":
            # For code blocks, get text from the code property's rich_text
            text = "".join(
                [
                    span.get("text", {}).get("content", "")
                    for span in content.get("rich_text", [])
                ]
            )
            
            # Get code block properties
            language = content.get("language", "")
            
            # Format the code block with language specification
            markdown = f"{indent}```{language}\n"
            
            # Add the code content with proper indentation
            code_lines = text.split("\n")
            for line in code_lines:
                markdown += f"{indent}{line}\n"
            
            # Close the code block
            markdown += f"{indent}```\n"  # Add line break after code block
            
            # Add caption if present (safely handle empty caption array)
            caption_text = ""
            if content.get("caption"):
                caption_text = "".join(
                    [
                        span.get("text", {}).get("content", "")
                        for span in content["caption"]
                    ]
                )
            if caption_text:
                markdown += f"{indent}*{caption_text}*\n"  # Add line break after caption
        else:
            # For other blocks, get text from the block's rich_text with formatting
            if subpage_data:
                text = format_rich_text_with_subpages(content.get("rich_text", []), subpage_data)
            else:
                text = format_rich_text(content.get("rich_text", []))
            
            # Handle different block types
            if block_type == "paragraph":
                if text:
                    markdown = f"{indent}{text}\n\n"
                else:
                    markdown = f"{indent}\n\n"  # Empty paragraph gets a blank line
            
            elif block_type == "heading_1":
                markdown = f"{indent}# {text}\n\n"
            
            elif block_type == "heading_2":
                markdown = f"{indent}## {text}\n\n"
            
            elif block_type == "heading_3":
                markdown = f"{indent}### {text}\n\n"
                
            elif block_type == "bulleted_list_item":
                markdown = f"{indent}- {text}\n"
                
            elif block_type == "numbered_list_item":
                markdown = f"{indent}1. {text}\n"
                
            elif block_type == "to_do":
                checked = content.get("checked", False)
                checkbox = "x" if checked else " "
                markdown = f"{indent}- [{checkbox}] {text}\n"
                
            elif block_type == "toggle":
                markdown = f"{indent}> **{text}**\n"
                
            elif block_type == "quote":
                markdown = f"{indent}> {text}\n\n"
                
            elif block_type == "divider":
                markdown = f"{indent}---\n\n"
                
            elif block_type == "callout":
                emoji = content.get("icon", {}).get("emoji", "")
                markdown = f"{indent}> {emoji} **{text}**\n\n"
                
            elif block_type == "image":
                if subpage_data:
                    caption = format_rich_text_with_subpages(content.get("caption", []), subpage_data)
                else:
                    caption = format_rich_text(content.get("caption", []))
                # Try to get the URL from various sources
                url = ""
                image_type = content.get("type", "")
                
                if image_type == "external":
                    url = content.get("external", {}).get("url", "")
                elif image_type == "file":
                    url = content.get("file", {}).get("url", "")
                
                markdown = f"{indent}![{caption}]({url})\n\n"
                if caption:
                    markdown += f"{indent}*{caption}*\n\n"
            
            elif block_type == "bookmark":
                url = content.get("url", "")
                if subpage_data:
                    caption = format_rich_text_with_subpages(content.get("caption", []), subpage_data)
                else:
                    caption = format_rich_text(content.get("caption", []))
                markdown = f"{indent}[{url}]({url})\n"
                if caption:
                    markdown += f"{indent}*{caption}*\n\n"
            
            elif block_type == "embed":
                url = content.get("url", "")
                markdown = f"{indent}<{url}>\n\n"
                
            elif block_type == "table":
                # Delegate to a helper function for table conversion
                markdown = _table_to_markdown(block, content, indent)
            
            elif block_type == "column_list" or block_type == "column":
                # These blocks are just containers, so we'll skip them
                # and rely on processing their children
                pass
            
            elif block_type == "child_page":
                # Special handling for child_page blocks
                page_id = block.get("id", "")
                title = content.get("title", "")  # Get title directly from the child_page object
                
                if title:
                    markdown = f"{indent}📄 **[Sub-page]** {title}\n\n"
                else:
                    # Fallback if no title is available at all
                    markdown = f"{indent}📄 **[Sub-page]** {page_id[:8] if page_id else 'Unknown'}\n\n"
            
            elif block_type == "transcript":
                # Special handling for transcript blocks
                # Check if we have the full transcript from MCP enhancement
                if block.get("_meeting_transcript"):
                    # We have the full transcript from MCP
                    transcript_text = block["_meeting_transcript"]
                    markdown = f"{indent}📝 **Meeting Transcript:**\n\n"
                    markdown += f"{indent}```\n"
                    for line in transcript_text.split('\n'):
                        markdown += f"{indent}{line}\n"
                    markdown += f"{indent}```\n\n"
                else:
                    # Fallback to summary if available
                    summary_text = ""

                    # Handle different possible formats for the summary
                    if content.get("summary"):
                        summary_data = content.get("summary")

                        # If summary is rich text format (like other Notion content)
                        if isinstance(summary_data, list):
                            if subpage_data:
                                summary_text = format_rich_text_with_subpages(summary_data, subpage_data)
                            else:
                                summary_text = format_rich_text(summary_data)
                        # If summary is plain text
                        elif isinstance(summary_data, str):
                            summary_text = summary_data

                    if summary_text.strip():
                        # Format the summary as a highlighted section
                        markdown = f"{indent}📝 **Transcript Summary:**\n{indent}{summary_text}\n\n"
                    else:
                        # Fallback if no summary is available
                        markdown = f"{indent}📝 **[Transcript block - no content accessible via API]**\n\n"
            
            elif block_type == "unsupported":
                # Handle unsupported blocks, which often include transcription blocks
                # Check if we have meeting transcript from MCP enhancement
                if block.get("_meeting_transcript"):
                    # We have the full transcript from MCP
                    transcript_text = block["_meeting_transcript"]
                    markdown = f"{indent}📝 **Meeting Transcript:**\n\n"
                    markdown += f"{indent}```\n"
                    for line in transcript_text.split('\n'):
                        markdown += f"{indent}{line}\n"
                    markdown += f"{indent}```\n\n"
                elif block.get("has_children"):
                    # If it has children, it might be a transcription block with accessible content
                    markdown = f"{indent}📝 **[Meeting block - attempting to fetch transcript...]**\n\n"
                    # Note: child blocks will be processed separately and may contain accessible content
                else:
                    # Generic unsupported block
                    markdown = f"{indent}*[Unsupported block type - content not accessible]*\n\n"
            
            else:
                # Default for unhandled block types
                markdown = f"{indent}*[{block_type} block]*: {text}\n\n"
    except Exception as e:
        # If there's an error processing this block, at least give a hint
        markdown = f"{indent}*[Error processing {block_type} block: {str(e)}]*\n\n"
    
    # Process children blocks if they exist
    if block.get("children"):
        child_markdown = ""
        # For tables, children (rows) are handled by _table_to_markdown, so skip generic child processing.
        if block_type != "table": 
            for child in block["children"]:
                child_markdown += block_to_markdown(child, level + 1, subpage_data)
        
        # For some block types, we want to indent the children differently
        if block_type in ["bulleted_list_item", "numbered_list_item"]:
            # Replace the existing double newline with a single one before adding children
            markdown = markdown.rstrip() + "\n" + child_markdown
        else:
            markdown += child_markdown
            
    return markdown

def format_rich_text(rich_text_array):
    """
    Format rich text array from Notion into markdown.
    
    Args:
        rich_text_array: Array of rich text objects from Notion
        
    Returns:
        Formatted markdown text
    """
    if not rich_text_array:
        return ""
    
    result = ""
    for text_obj in rich_text_array:
        text_type = text_obj.get("type", "")
        annotations = text_obj.get("annotations", {})
        
        # Handle different types of rich text objects
        if text_type == "text":
            # Regular text content
            text = text_obj.get("text", {}).get("content", "")
        elif text_type == "mention":
            # Handle mention objects (pages, users, dates, etc.)
            text = _format_mention(text_obj)
        else:
            # For other types (equation, etc.), try to get plain_text or fallback to empty
            text = text_obj.get("plain_text", "")
        
        # Apply text annotations (bold, italic, etc.)
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("underline"):
            # Markdown doesn't directly support underline, using emphasis instead
            text = f"_{text}_"
        
        # Handle links
        if text_obj.get("href"):
            text = f"[{text}]({text_obj['href']})"
        
        result += text
    
    return result


def _format_mention(mention_obj):
    """
    Format a mention object into markdown text.
    
    Args:
        mention_obj: Notion mention object
        
    Returns:
        Formatted mention text
    """
    mention = mention_obj.get("mention", {})
    mention_type = mention.get("type", "")
    
    # Use plain_text as fallback if available
    fallback_text = mention_obj.get("plain_text", "")
    
    if mention_type == "template_mention":
        # Handle temporal mentions like @Today, @Now
        template_mention = mention.get("template_mention", {})
        template_type = template_mention.get("type", "")
        
        if template_type == "template_mention_date":
            date_type = template_mention.get("template_mention_date", "")
            if date_type == "today":
                return "@Today"
            elif date_type == "now":
                return "@Now"
        elif template_type == "template_mention_user":
            user_type = template_mention.get("template_mention_user", "")
            if user_type == "me":
                return "@me"
        
        # If we have a fallback, use it
        if fallback_text:
            return fallback_text
        
        # Otherwise, format as generic template mention
        return f"@{template_type}"
    
    elif mention_type == "page":
        # Handle page mentions
        page_id = mention.get("page", {}).get("id", "")
        if fallback_text:
            return fallback_text
        return f"@page-{page_id[:8]}" if page_id else "@page"
    
    elif mention_type == "user":
        # Handle user mentions
        user_id = mention.get("user", {}).get("id", "")
        if fallback_text:
            return fallback_text
        return f"@user-{user_id[:8]}" if user_id else "@user"
    
    elif mention_type == "date":
        # Handle date mentions
        date_obj = mention.get("date", {})
        start_date = date_obj.get("start", "")
        if fallback_text:
            return fallback_text
        return f"@{start_date}" if start_date else "@date"
    
    else:
        # For unknown mention types, try to use plain_text or create a generic mention
        if fallback_text:
            return fallback_text
        return f"@{mention_type}"

def format_rich_text_with_subpages(rich_text_array, subpage_data: Optional[Dict[str, str]] = None):
    """
    Format rich text array from Notion into markdown, replacing subpage links with their content.
    
    Args:
        rich_text_array: Array of rich text objects from Notion.
        subpage_data: Dictionary mapping sub-page URLs to their markdown content.
        
    Returns:
        Formatted markdown text with subpages inlined.
    """
    if not rich_text_array:
        return ""
    
    result = ""
    for text_obj in rich_text_array:
        # Check if the text object is a mention that should be expanded
        href = text_obj.get("href")
        if (subpage_data and href and href in subpage_data):
            # It's a subpage, so we replace the link with its content
            result += subpage_data[href]
            continue

        # If it's not a subpage to be expanded, format it normally
        text_type = text_obj.get("type", "")
        annotations = text_obj.get("annotations", {})
        
        if text_type == "text":
            text = text_obj.get("text", {}).get("content", "")
        elif text_type == "mention":
            text = _format_mention(text_obj)
        else:
            text = text_obj.get("plain_text", "")
        
        # Apply annotations
        if annotations.get("bold"): text = f"**{text}**"
        if annotations.get("italic"): text = f"*{text}*"
        if annotations.get("strikethrough"): text = f"~~{text}~~"
        if annotations.get("code"): text = f"`{text}`"
        if annotations.get("underline"): text = f"_{text}_"
        
        # Handle regular links (that are not subpages)
        if href:
            text = f"[{text}]({href})"
        
        result += text
            
    return result

def _table_to_markdown(table_block: Dict[str, Any], table_content: Dict[str, Any], indent: str) -> str:
    """
    Converts a Notion table block to a Markdown table.
    Assumes table_block["children"] contains the table_row blocks.
    """
    markdown_rows = []
    has_column_header = table_content.get("has_column_header", False)
    # Notion API table_width or table_content.get("table_width") might indicate number of columns.
    # We infer it from the first row if possible, or assume consistent cell counts.
    
    table_rows = table_block.get("children", [])
    if not table_rows:
        return f"{indent}[Empty Table]\n\n"

    num_columns = 0
    if table_rows and table_rows[0].get("type") == "table_row":
        first_row_cells = table_rows[0].get("table_row", {}).get("cells", [])
        num_columns = len(first_row_cells)
    
    if num_columns == 0:
        return f"{indent}[Table with no columns or malformed rows]\n\n" 

    # Process header row
    if has_column_header and table_rows:
        header_row_block = table_rows[0]
        if header_row_block.get("type") == "table_row":
            header_cells_data = header_row_block.get("table_row", {}).get("cells", [])
            header_texts = [format_rich_text(cell_content) for cell_content in header_cells_data]
            markdown_rows.append(f"{indent}| {" | ".join(header_texts)} |")
            markdown_rows.append(f"{indent}|{"---" * num_columns}|") # Correct separator line
            table_data_rows = table_rows[1:]
        else:
            # No valid header row found, treat all rows as data
            table_data_rows = table_rows
            # Add a placeholder separator if we expected a header but didn't find one of type table_row
            markdown_rows.append(f"{indent}| {" | ".join(['Header?' for _ in range(num_columns)])} |")
            markdown_rows.append(f"{indent}|{"---" * num_columns}|")
    else:
        # No header row, treat all rows as data. 
        # Markdown tables require a header, so we might create a default one or skip separator.
        # For simplicity, we'll skip the separator if no explicit header.
        # Or, let's add a default header and separator to make it valid Markdown.
        if table_rows: # Only add default header if there are rows
            # Create a default header row like "Col 1 | Col 2 | ..."
            default_headers = [f"Col {i+1}" for i in range(num_columns)]
            markdown_rows.append(f"{indent}| {" | ".join(default_headers)} |")
            markdown_rows.append(f"{indent}|{"---" * num_columns}|")
        table_data_rows = table_rows

    # Process data rows
    for row_block in table_data_rows:
        if row_block.get("type") == "table_row":
            row_cells_data = row_block.get("table_row", {}).get("cells", [])
            # Ensure all rows have the same number of columns as the header for valid Markdown
            row_texts = [format_rich_text(cell_content).replace("\n", " <br> ") for cell_content in row_cells_data]
            # Pad rows that have fewer cells than num_columns
            while len(row_texts) < num_columns:
                row_texts.append("") 
            # Truncate rows that have more cells (less common)
            row_texts = row_texts[:num_columns]
            markdown_rows.append(f"{indent}| {" | ".join(row_texts)} |")
        else:
            # This child is not a table_row, should not happen for a well-formed table's children
            markdown_rows.append(f"{indent}| [Malformed row: {row_block.get('type')}] |")

    return "\n".join(markdown_rows) + "\n\n" if markdown_rows else f"{indent}[Empty or Malformed Table]\n\n"

def format_notion_properties(properties: Dict[str, Any], excluded_properties: List[str] = None) -> str:
    """
    Format Notion page properties as markdown.
    
    Args:
        properties: Dictionary of Notion page properties
        excluded_properties: List of property names to exclude from output
        
    Returns:
        Formatted markdown string with properties
    """
    if not properties:
        return ""
    
    # Default to empty list if no exclusions provided
    if excluded_properties is None:
        excluded_properties = []
    
    property_lines = []
    
    for prop_name, prop_data in properties.items():
        # Skip excluded properties
        if prop_name in excluded_properties:
            continue
            
        prop_type = prop_data.get("type", "unknown")
        value = extract_property_value(prop_data)
        
        if value is not None and value != "":
            # Format different property types appropriately
            if prop_type == "url" and value:
                property_lines.append(f"{prop_name}: {value}")
            elif prop_type == "date" and value:
                # Format date nicely
                try:
                    from datetime import datetime
                    if isinstance(value, str):
                        # Try to parse and reformat the date
                        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        formatted_date = dt.strftime("%B %d, %Y %I:%M %p")
                        property_lines.append(f"{prop_name}: {formatted_date}")
                    else:
                        property_lines.append(f"{prop_name}: {value}")
                except:
                    property_lines.append(f"{prop_name}: {value}")
            elif prop_type == "relation" and isinstance(value, list):
                # Handle relation properties (links to other pages)
                if value:
                    relation_text = ", ".join([str(v) for v in value if v])
                    property_lines.append(f"{prop_name}: {relation_text}")
            elif isinstance(value, list):
                # Handle multi-select and other list properties
                if value:
                    list_text = ", ".join([str(v) for v in value if v])
                    property_lines.append(f"{prop_name}: {list_text}")
            else:
                property_lines.append(f"{prop_name}: {value}")
    
    if property_lines:
        return "\n".join(property_lines) + "\n\n"
    return ""

def extract_property_value(property_data: Dict[str, Any]) -> Any:
    """Extract the actual value from a Notion property data structure."""
    prop_type = property_data.get("type")
    
    if prop_type == "title" and property_data.get("title"):
        return "".join([t.get("plain_text", "") for t in property_data["title"]])
    elif prop_type == "rich_text" and property_data.get("rich_text"):
        return "".join([t.get("plain_text", "") for t in property_data["rich_text"]])
    elif prop_type == "select" and property_data.get("select"):
        return property_data["select"].get("name")
    elif prop_type == "status" and property_data.get("status"):
        return property_data["status"].get("name")
    elif prop_type == "multi_select" and property_data.get("multi_select"):
        return [item.get("name") for item in property_data["multi_select"]]
    elif prop_type == "date" and property_data.get("date"):
        return property_data["date"].get("start")
    elif prop_type == "checkbox":
        return property_data.get("checkbox", False)
    elif prop_type == "number":
        return property_data.get("number")
    elif prop_type == "url":
        return property_data.get("url")
    elif prop_type == "email":
        return property_data.get("email")
    elif prop_type == "phone_number":
        return property_data.get("phone_number")
    elif prop_type == "created_time":
        return property_data.get("created_time")
    elif prop_type == "last_edited_time":
        return property_data.get("last_edited_time")
    elif prop_type == "created_by" and property_data.get("created_by"):
        user = property_data["created_by"]
        return user.get("name", user.get("id", "Unknown"))
    elif prop_type == "last_edited_by" and property_data.get("last_edited_by"):
        user = property_data["last_edited_by"]
        return user.get("name", user.get("id", "Unknown"))
    elif prop_type == "relation" and property_data.get("relation"):
        # For relations, we might want to show the linked page titles
        # For now, just return the count or IDs
        relations = property_data["relation"]
        return [rel.get("id", "") for rel in relations] if relations else []
    elif prop_type == "people" and property_data.get("people"):
        people = property_data["people"]
        return [person.get("name", person.get("id", "Unknown")) for person in people]
    elif prop_type == "files" and property_data.get("files"):
        files = property_data["files"]
        return [f.get("name", f.get("file", {}).get("url", "")) for f in files]
    elif prop_type == "formula" and property_data.get("formula"):
        formula_result = property_data["formula"]
        return formula_result.get("string") or formula_result.get("number") or formula_result.get("boolean")
    elif prop_type == "rollup" and property_data.get("rollup"):
        rollup_result = property_data["rollup"]
        return rollup_result.get("array", []) or rollup_result.get("number") or rollup_result.get("date")
    
    return None

def page_to_markdown(blocks: List[Dict[str, Any]], properties: Dict[str, Any] = None, include_properties: bool = True, excluded_properties: List[str] = None) -> str:
    """
    Convert a list of Notion blocks to a complete markdown document.
    
    Args:
        blocks: List of Notion blocks
        properties: Optional dictionary of page properties
        include_properties: Whether to include properties in the output
        excluded_properties: List of property names to exclude from output
        
    Returns:
        Complete markdown document as a string
    """
    markdown = ""
    
    # Add properties at the top if available and requested
    if include_properties and properties:
        property_markdown = format_notion_properties(properties, excluded_properties)
        if property_markdown:
            markdown += property_markdown
    
    # Add content blocks
    for block in blocks:
        markdown += block_to_markdown(block)
    
    return markdown

async def page_to_markdown_with_subpages(blocks: List[Dict[str, Any]], properties: Dict[str, Any] = None, include_properties: bool = True, excluded_properties: List[str] = None, parent_page_id: str = None) -> str:
    """
    Convert a list of Notion blocks to a complete markdown document with subpage content inlined.
    
    Args:
        blocks: List of Notion blocks
        properties: Optional dictionary of page properties
        include_properties: Whether to include properties in the output
        excluded_properties: List of property names to exclude from output
        parent_page_id: ID of the parent page (required for proper child page detection)
        
    Returns:
        Complete markdown document as a string with subpages inlined
    """
    markdown = ""
    
    subpage_data = {}
    if parent_page_id:
        from promaia.notion.pages import detect_child_pages_in_blocks
        
        # Reliably detect true child pages (sub-pages) by checking parent relationships
        child_page_ids = await detect_child_pages_in_blocks(blocks, parent_page_id)
        
        # Fetch content only for the true child pages
        if child_page_ids:
            page_urls = [f"https://www.notion.so/{cid.replace('-', '')}" for cid in child_page_ids]
            subpage_data = await fetch_subpage_content(page_urls)

    # Add properties at the top if available and requested
    if include_properties and properties:
        property_markdown = format_notion_properties(properties, excluded_properties)
        if property_markdown:
            markdown += property_markdown
    
    # Add content blocks with subpage data
    for block in blocks:
        markdown += block_to_markdown(block, subpage_data=subpage_data)
    
    return markdown 

def extract_child_page_urls_from_blocks(blocks: List[Dict[str, Any]]) -> List[str]:
    """
    Extract URLs from actual child_page blocks and empty page links in rich text.
    This distinguishes between true sub-pages and external page references.
    
    Args:
        blocks: List of Notion blocks to scan
        
    Returns:
        List of unique page URLs found from child pages and empty links
    """
    page_urls = set()
    
    def scan_rich_text(rich_text_array):
        """Scan rich text array for empty page links (true sub-pages)."""
        if not rich_text_array:
            return
            
        for text_obj in rich_text_array:
            text = text_obj.get("text", {}).get("content", "")
            href = text_obj.get("href")
            
            # Only include empty links - these are typically sub-pages
            # Links with text are external references and should not be expanded
            if not text.strip() and href and "notion.so" in href:
                page_urls.add(href)
    
    def scan_block(block: Dict[str, Any]):
        """Recursively scan a block for child pages and empty page links."""
        block_type = block.get("type")
        
        # Check for child_page blocks (true sub-pages)
        if block_type == "child_page":
            child_page_id = block.get("id")
            child_page_title = block.get("child_page", {}).get("title", "")
            
            # Only include child pages that have a title (skip empty ones)
            if child_page_id and child_page_title.strip():
                # Convert block ID to Notion URL format
                clean_id = child_page_id.replace('-', '')
                notion_url = f"https://www.notion.so/{clean_id}"
                page_urls.add(notion_url)
        
        # Check for empty page links in rich text
        content = block.get(block_type, {})
        if "rich_text" in content:
            scan_rich_text(content["rich_text"])
        if "caption" in content:
            scan_rich_text(content["caption"])
            
        # Recursively scan children
        if "children" in block:
            for child_block in block["children"]:
                scan_block(child_block)
    
    # Scan all blocks
    for block in blocks:
        scan_block(block)
    
    return list(page_urls)

async def fetch_subpage_content(page_urls: List[str]) -> Dict[str, str]:
    """
    Fetch content for a list of page URLs and return a mapping of URL -> content.
    
    Args:
        page_urls: List of Notion page URLs to fetch
        
    Returns:
        Dictionary mapping page URL to its markdown content
    """
    from promaia.notion.pages import get_block_content, get_page_title
    from promaia.notion.client import notion_client
    import re
    
    subpage_data = {}
    
    for url in page_urls:
        try:
            # Extract page ID from URL
            # URLs like: https://www.notion.so/20dd13396967805eb3bdeb5093a257fd
            page_id_match = re.search(r'([a-f0-9]{32})', url)
            if not page_id_match:
                 page_id_match = re.search(r'([a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12})', url)
            if not page_id_match:
                continue
                
            page_id = page_id_match.group(1).replace('-', '')
            
            # Format page ID properly (add hyphens)
            if len(page_id) == 32:
                page_id = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"
            
            # Fetch page title and content
            try:
                page_title = await get_page_title(page_id)
                page_blocks = await get_block_content(page_id)
                
                # Convert to markdown
                subpage_markdown = page_to_markdown(page_blocks)
                
                # Format with title - only add content if we have meaningful content
                if page_title and page_title.strip():
                    formatted_content = f"\n\n📄 **[Sub-page]** {page_title}\n\n{subpage_markdown}"
                    subpage_data[url] = formatted_content
                else:
                    # Skip pages with empty titles
                    continue
                
            except Exception as e:
                # If we can't fetch the page, leave it as an empty link
                print(f"Warning: Could not fetch subpage {page_id}: {e}")
                
        except Exception as e:
            print(f"Warning: Error processing subpage URL {url}: {e}")
    
    return subpage_data


def conversation_to_markdown(thread_data: Dict[str, Any]) -> str:
    """
    Convert a conversation thread to markdown format.

    Args:
        thread_data: Dictionary containing thread information with:
            - id: Thread ID
            - properties: Dict with thread_name, message_count, created_at, etc.
            - messages: List of message dicts with role and content
            - context: Dict with context information

    Returns:
        Formatted markdown representation of the conversation
    """
    from datetime import datetime

    properties = thread_data.get('properties', {})
    messages = thread_data.get('messages', [])
    context = thread_data.get('context', {})

    # Extract metadata
    thread_name = properties.get('thread_name', 'Untitled Conversation')
    created_at = properties.get('created_at', '')
    last_accessed = properties.get('last_accessed', '')
    message_count = properties.get('message_count', len(messages))
    context_type = properties.get('context_type', 'general')
    sql_query_prompt = properties.get('sql_query_prompt', '')

    # Start building markdown
    markdown = f"# {thread_name}\n\n"

    # Add metadata section
    markdown += "## Metadata\n\n"
    markdown += f"- **Thread ID**: {thread_data.get('id', 'unknown')}\n"
    markdown += f"- **Message Count**: {message_count}\n"
    markdown += f"- **Context Type**: {context_type}\n"

    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            formatted_date = dt.strftime("%B %d, %Y at %I:%M %p")
            markdown += f"- **Created**: {formatted_date}\n"
        except:
            markdown += f"- **Created**: {created_at}\n"

    if last_accessed and last_accessed != created_at:
        try:
            dt = datetime.fromisoformat(last_accessed)
            formatted_date = dt.strftime("%B %d, %Y at %I:%M %p")
            markdown += f"- **Last Accessed**: {formatted_date}\n"
        except:
            markdown += f"- **Last Accessed**: {last_accessed}\n"

    if sql_query_prompt:
        markdown += f"- **SQL Query**: `{sql_query_prompt}`\n"

    markdown += "\n"

    # Add context information if available
    if context and len(context) > 1:  # More than just empty dict
        markdown += "## Context\n\n"
        for key, value in context.items():
            if key not in ['sql_query_prompt'] and value:  # Skip already shown items
                markdown += f"- **{key.replace('_', ' ').title()}**: {value}\n"
        markdown += "\n"

    # Add conversation messages
    markdown += "## Conversation\n\n"

    if not messages:
        markdown += "*No messages in this conversation.*\n\n"
    else:
        for i, message in enumerate(messages, 1):
            role = message.get('role', 'unknown')
            content = message.get('content', '')

            # Format role nicely
            if role == 'user':
                role_emoji = "👤"
                role_label = "User"
            elif role == 'assistant':
                role_emoji = "🤖"
                role_label = "Assistant"
            elif role == 'system':
                role_emoji = "⚙️"
                role_label = "System"
            else:
                role_emoji = "💬"
                role_label = role.title()

            # Add message header
            markdown += f"### {role_emoji} {role_label}\n\n"

            # Add message content
            if content:
                # Format code blocks if present
                markdown += f"{content}\n\n"
            else:
                markdown += "*[Empty message]*\n\n"

            # Add separator between messages (except for last one)
            if i < len(messages):
                markdown += "---\n\n"

    return markdown
