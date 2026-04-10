"""
Convert Notion blocks directly to HTML format.
"""
from typing import Dict, Any, List
import re
import html

def block_to_html(block: Dict[str, Any]) -> str:
    """
    Convert a Notion block to HTML format.
    
    Args:
        block: Notion block object to convert
        
    Returns:
        HTML representation of the block
    """
    block_type = block["type"]
    content = block[block_type]
    html_output = ""

    try:
        # Handle different block types with direct HTML conversion
        if block_type == "paragraph":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<p>{text}</p>\n"
            
        elif block_type == "heading_1":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<h1>{text}</h1>\n"
            
        elif block_type == "heading_2":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<h2>{text}</h2>\n"
            
        elif block_type == "heading_3":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<h3>{text}</h3>\n"
            
        elif block_type == "bulleted_list_item":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<li>{text}"

            # Process children (can be nested lists, callouts, or other blocks)
            if block.get("children"):
                # Separate list items from other content
                nested_items = []
                other_children = []
                for child in block["children"]:
                    child_type = child.get("type", "")
                    if child_type in ["bulleted_list_item", "numbered_list_item"]:
                        nested_items.append(child)
                    else:
                        other_children.append(child)

                # Process non-list children first (like callouts)
                for child in other_children:
                    html_output += "\n" + block_to_html(child)

                # Wrap nested list items in appropriate list tags
                if nested_items:
                    # Determine nested list type from first item
                    first_type = nested_items[0].get("type", "")
                    nested_list_tag = "ul" if first_type == "bulleted_list_item" else "ol"
                    html_output += f"\n<{nested_list_tag}>\n"
                    for child in nested_items:
                        html_output += block_to_html(child)
                    html_output += f"</{nested_list_tag}>\n"

            html_output += "</li>\n"

        elif block_type == "numbered_list_item":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<li>{text}"

            # Process children (can be nested lists, callouts, or other blocks)
            if block.get("children"):
                # Separate list items from other content
                nested_items = []
                other_children = []
                for child in block["children"]:
                    child_type = child.get("type", "")
                    if child_type in ["bulleted_list_item", "numbered_list_item"]:
                        nested_items.append(child)
                    else:
                        other_children.append(child)

                # Process non-list children first (like callouts)
                for child in other_children:
                    html_output += "\n" + block_to_html(child)

                # Wrap nested list items in appropriate list tags
                if nested_items:
                    # Determine nested list type from first item
                    first_type = nested_items[0].get("type", "")
                    nested_list_tag = "ul" if first_type == "bulleted_list_item" else "ol"
                    html_output += f"\n<{nested_list_tag}>\n"
                    for child in nested_items:
                        html_output += block_to_html(child)
                    html_output += f"</{nested_list_tag}>\n"

            html_output += "</li>\n"
            
        elif block_type == "to_do":
            text = format_rich_text_html(content.get("rich_text", []))
            checked = content.get("checked", False)
            checkbox = f'<input type="checkbox" {"checked" if checked else ""} disabled> '
            html_output = f"<div class=\"todo-item\">{checkbox}{text}</div>\n"
            
        elif block_type == "toggle":
            text = format_rich_text_html(content.get("rich_text", []))
            # Create a disclosure element for the toggle
            html_output = f"""<details>
                <summary>{text}</summary>
                <div class="toggle-content">
            """
            
            # Process children if they exist
            if block.get("children"):
                for child in block["children"]:
                    html_output += block_to_html(child)
                    
            html_output += """
                </div>
            </details>\n"""
            
            # Return early since we already processed children
            return html_output
            
        elif block_type == "quote":
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<blockquote>{text}</blockquote>\n"
            
        elif block_type == "divider":
            html_output = "<hr>\n"
            
        elif block_type == "callout":
            text = format_rich_text_html(content.get("rich_text", []))
            emoji = content.get("icon", {}).get("emoji", "")
            html_output = f"""<div class="callout">
                <div class="callout-emoji">{emoji}</div>
                <div class="callout-text">{text}"""

            # Process children if they exist
            if block.get("children"):
                for child in block["children"]:
                    html_output += block_to_html(child)

            html_output += """</div>
            </div>\n"""

            # Return early since we already processed children
            return html_output
            
        elif block_type == "code":
            text = "".join([
                span.get("text", {}).get("content", "")
                for span in content.get("rich_text", [])
            ])
            language = content.get("language", "")
            html_output = f"""<pre><code class="language-{language}">
{html.escape(text)}
</code></pre>\n"""
            
            # Add caption if present
            caption_text = ""
            if content.get("caption"):
                caption_text = "".join([
                    span.get("text", {}).get("content", "")
                    for span in content["caption"]
                ])
            if caption_text:
                html_output += f"<div class=\"code-caption\">{caption_text}</div>\n"
            
        elif block_type == "image":
            caption = format_rich_text_html(content.get("caption", []))
            url = ""
            image_type = content.get("type", "")
            
            if image_type == "external":
                url = content.get("external", {}).get("url", "")
            elif image_type == "file":
                url = content.get("file", {}).get("url", "")
            
            html_output = f"""<figure>
                <img src="{url}" alt="{caption}">
                {f'<figcaption>{caption}</figcaption>' if caption else ''}
            </figure>\n"""
            
        elif block_type == "bookmark" or block_type == "link_preview":
            url = content.get("url", "")
            caption = format_rich_text_html(content.get("caption", []))
            html_output = f"""<div class="bookmark">
                <a href="{url}" target="_blank">{url}</a>
                {f'<div class="bookmark-caption">{caption}</div>' if caption else ''}
            </div>\n"""
            
        elif block_type == "embed":
            url = content.get("url", "")
            html_output = f"""<div class="embed-container">
                <iframe src="{url}" frameborder="0" allowfullscreen></iframe>
            </div>\n"""
            
        elif block_type == "table":
            # Start table
            html_output = "<table>\n"
            
            # Tables are complex, we'll handle their content in dedicated code
            # Process table rows (children)
            if block.get("has_children") and block.get("children"):
                # Determine if first row is header
                has_header = content.get("has_column_header", False)
                
                # Process each row
                for i, row in enumerate(block.get("children", [])):
                    if row["type"] == "table_row":
                        row_cells = row["table_row"]["cells"]
                        cell_tag = "th" if i == 0 and has_header else "td"
                        
                        html_output += "  <tr>\n"
                        for cell in row_cells:
                            cell_text = "".join([
                                format_rich_text_html([text_obj]) 
                                for text_obj in cell
                            ])
                            html_output += f"    <{cell_tag}>{cell_text}</{cell_tag}>\n"
                        html_output += "  </tr>\n"
                
            html_output += "</table>\n"
            
        elif block_type == "column_list":
            # Start a flex container for columns
            html_output = "<div class=\"column-list\">\n"
            
            # Process columns (children)
            if block.get("children"):
                for column_block in block["children"]:
                    if column_block["type"] == "column":
                        html_output += "<div class=\"column\">\n"
                        
                        # Process all blocks in this column
                        if column_block.get("children"):
                            for child in column_block["children"]:
                                html_output += block_to_html(child)
                                
                        html_output += "</div>\n"
            
            html_output += "</div>\n"
            
            # Return early as we already processed all children
            return html_output
            
        else:
            # Default for unhandled block types
            text = format_rich_text_html(content.get("rich_text", []))
            html_output = f"<div class=\"{block_type}-block\">{text}</div>\n"
    
    except Exception as e:
        # If there's an error processing this block, at least give a hint
        html_output = f"<!-- Error processing {block_type} block: {str(e)} -->\n"
    
    # Process children blocks if they exist (and weren't already processed above)
    if block.get("children") and block_type not in ["toggle", "column_list", "table", "callout", "bulleted_list_item", "numbered_list_item"]:
        for child in block["children"]:
            html_output += block_to_html(child)
            
    return html_output

def format_rich_text_html(rich_text_array):
    """
    Format rich text array from Notion into HTML.
    
    Args:
        rich_text_array: Array of rich text objects from Notion
        
    Returns:
        Formatted HTML text
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
            text = _format_mention_html(text_obj)
        else:
            # For other types (equation, etc.), try to get plain_text or fallback to empty
            text = text_obj.get("plain_text", "")
        
        text = html.escape(text)  # Escape HTML special characters
        
        # Apply text annotations (bold, italic, etc.)
        if annotations.get("bold"):
            text = f"<strong>{text}</strong>"
        if annotations.get("italic"):
            text = f"<em>{text}</em>"
        if annotations.get("strikethrough"):
            text = f"<s>{text}</s>"
        if annotations.get("code"):
            text = f"<code>{text}</code>"
        if annotations.get("underline"):
            text = f"<u>{text}</u>"
        
        # Apply color if specified
        color = annotations.get("color")
        if color and color != "default":
            text = f"<span class=\"color-{color}\">{text}</span>"
        
        # Handle links
        if text_obj.get("href"):
            text = f"<a href=\"{text_obj['href']}\" target=\"_blank\">{text}</a>"
        
        result += text
    
    return result


def _format_mention_html(mention_obj):
    """
    Format a mention object into HTML text.
    
    Args:
        mention_obj: Notion mention object
        
    Returns:
        Formatted mention text (before HTML escaping)
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

def page_to_html(blocks: List[Dict[str, Any]]) -> str:
    """
    Convert a list of Notion blocks to a complete HTML document.
    
    Args:
        blocks: List of Notion block objects
        
    Returns:
        Complete HTML content as a string
    """
    html_content = ""
    current_list_type = None
    
    # Process blocks one by one
    i = 0
    while i < len(blocks):
        block = blocks[i]
        block_type = block["type"]
        
        # Handle special cases for lists (grouping list items)
        if block_type in ["bulleted_list_item", "numbered_list_item"]:
            # Determine the list type
            list_type = "ul" if block_type == "bulleted_list_item" else "ol"
            
            # If we're not in a list or in a different type of list, start a new one
            if current_list_type != list_type:
                if current_list_type:
                    html_content += f"</{current_list_type}>\n"
                html_content += f"<{list_type}>\n"
                current_list_type = list_type
            
            # Add the list item
            html_content += block_to_html(block)
            
            # Look ahead to see if the next block is also a list item
            if i == len(blocks) - 1 or blocks[i + 1]["type"] != block_type:
                html_content += f"</{list_type}>\n"
                current_list_type = None
        else:
            # Close any open list before adding a non-list block
            if current_list_type:
                html_content += f"</{current_list_type}>\n"
                current_list_type = None
            
            # Add the block
            html_content += block_to_html(block)
        
        i += 1
    
    # Close any open list at the end
    if current_list_type:
        html_content += f"</{current_list_type}>\n"
    
    return html_content

def get_html_document(blocks: List[Dict[str, Any]], title: str = "Notion Page") -> str:
    """
    Generate a complete HTML document with proper head and metadata.
    
    Args:
        blocks: List of Notion block objects
        title: Title for the HTML document
        
    Returns:
        Complete HTML document as a string
    """
    html_content = page_to_html(blocks)
    
    html_document = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        /* Base styles */
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.5;
            color: #37352f;
            margin: 0;
            padding: 20px;
        }}
        
        /* Callout styles */
        .callout {{
            padding: 16px;
            border-radius: 3px;
            display: flex;
            background-color: #f1f1f0;
            margin: 8px 0;
        }}
        
        .callout-emoji {{
            margin-right: 12px;
        }}
        
        /* Column layout */
        .column-list {{
            display: flex;
            gap: 20px;
            margin: 8px 0;
        }}
        
        .column {{
            flex: 1;
        }}
        
        /* Code styles */
        pre {{
            background-color: #f7f6f3;
            padding: 16px;
            border-radius: 3px;
            overflow-x: auto;
        }}
        
        code {{
            font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
        }}
        
        /* Image and figure styles */
        figure {{
            margin: 16px 0;
        }}
        
        img {{
            max-width: 100%;
            height: auto;
        }}
        
        figcaption {{
            color: #787774;
            font-size: 0.9em;
            text-align: center;
            margin-top: 8px;
        }}
        
        /* To-do item styles */
        .todo-item {{
            display: flex;
            align-items: center;
            margin: 4px 0;
        }}
        
        /* Table styles */
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }}
        
        th, td {{
            border: 1px solid #e3e2e0;
            padding: 8px 16px;
            text-align: left;
        }}
        
        th {{
            background-color: #f7f6f3;
        }}
        
        /* Color classes */
        .color-gray {{
            color: #787774;
        }}
        
        .color-brown {{
            color: #9f6b53;
        }}
        
        .color-orange {{
            color: #d9730d;
        }}
        
        .color-yellow {{
            color: #cb912f;
        }}
        
        .color-green {{
            color: #448361;
        }}
        
        .color-blue {{
            color: #337ea9;
        }}
        
        .color-purple {{
            color: #9065b0;
        }}
        
        .color-pink {{
            color: #c14c8a;
        }}
        
        .color-red {{
            color: #d44c47;
        }}
        
        /* Background color classes */
        .color-gray_background {{
            background-color: #f1f1f0;
        }}
        
        .color-brown_background {{
            background-color: #f4eeee;
        }}
        
        .color-orange_background {{
            background-color: #fbecdd;
        }}
        
        .color-yellow_background {{
            background-color: #fbf3db;
        }}
        
        .color-green_background {{
            background-color: #edf3ec;
        }}
        
        .color-blue_background {{
            background-color: #e7f3f8;
        }}
        
        .color-purple_background {{
            background-color: #f4f0f7;
        }}
        
        .color-pink_background {{
            background-color: #f9ecf1;
        }}
        
        .color-red_background {{
            background-color: #fdebec;
        }}
    </style>
</head>
<body>
    {html_content}
</body>
</html>
"""
    
    return html_document 