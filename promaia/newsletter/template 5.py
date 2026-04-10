"""
Email template handling functions for newsletters - Simple Markdown Version.
"""
import html
import re
import os
from typing import Optional, Dict, Any, List

# Resend unsubscribe URL placeholder (will be replaced by Resend when sending)
RESEND_UNSUBSCRIBE_PLACEHOLDER = "{{{RESEND_UNSUBSCRIBE_URL}}}"

def escape_html(text: Optional[str]) -> str:
    """
    Escape HTML special characters.
    
    Args:
        text: Text to escape
        
    Returns:
        HTML-escaped text
    """
    if not text:
        return ""
    return html.escape(text)

def markdown_to_html(markdown_text: str) -> str:
    """
    Convert markdown text to HTML with proper formatting.
    
    This is a simple implementation that supports common markdown features.
    
    Args:
        markdown_text: Markdown formatted text
        
    Returns:
        HTML string
    """
    if not markdown_text:
        return ""
    
    # Split into lines for processing
    lines = markdown_text.split('\n')
    html_lines = []
    in_code_block = False
    code_block_lines = []
    in_list = False
    
    for line in lines:
        # Handle code blocks
        if line.strip().startswith('```'):
            if in_code_block:
                # End code block
                html_lines.append('<pre><code>' + escape_html('\n'.join(code_block_lines)) + '</code></pre>')
                code_block_lines = []
                in_code_block = False
            else:
                # Start code block
                in_code_block = True
            continue
        
        if in_code_block:
            code_block_lines.append(line)
            continue
        
        # Handle headings
        if line.startswith('# '):
            html_lines.append(f'<h1>{format_inline_markdown(line[2:])}</h1>')
            in_list = False
        elif line.startswith('## '):
            html_lines.append(f'<h2>{format_inline_markdown(line[3:])}</h2>')
            in_list = False
        elif line.startswith('### '):
            html_lines.append(f'<h3>{format_inline_markdown(line[4:])}</h3>')
            in_list = False
        # Handle blockquotes
        elif line.startswith('> '):
            html_lines.append(f'<blockquote><p>{format_inline_markdown(line[2:])}</p></blockquote>')
            in_list = False
        # Handle unordered lists
        elif line.startswith('- ') or line.startswith('* '):
            if not in_list:
                html_lines.append('<ul>')
                in_list = 'ul'
            html_lines.append(f'<li>{format_inline_markdown(line[2:])}</li>')
        # Handle ordered lists
        elif re.match(r'^\d+\.\s', line):
            content = re.sub(r'^\d+\.\s', '', line)
            if in_list != 'ol':
                if in_list:
                    html_lines.append(f'</{in_list}>')
                html_lines.append('<ol>')
                in_list = 'ol'
            html_lines.append(f'<li>{format_inline_markdown(content)}</li>')
        # Handle horizontal rules
        elif line.strip() == '---' or line.strip() == '***':
            if in_list:
                html_lines.append(f'</{in_list}>')
                in_list = False
            html_lines.append('<hr />')
        # Handle empty lines
        elif not line.strip():
            if in_list:
                html_lines.append(f'</{in_list}>')
                in_list = False
            html_lines.append('')
        # Regular paragraphs
        else:
            if in_list:
                html_lines.append(f'</{in_list}>')
                in_list = False

            # Pass through HTML tags (callouts and nested lists are already formatted as HTML)
            if line.strip().startswith('<div') or line.strip().startswith('</div>') or \
               line.strip().startswith('<li') or line.strip().startswith('</li>') or \
               line.strip().startswith('<ul>') or line.strip().startswith('</ul>') or \
               line.strip().startswith('<ol>') or line.strip().startswith('</ol>'):
                html_lines.append(line)
            else:
                html_lines.append(f'<p>{format_inline_markdown(line)}</p>')
    
    # Close any open lists
    if in_list:
        html_lines.append(f'</{in_list}>')
    
    return '\n'.join(html_lines)


def format_inline_markdown(text: str) -> str:
    """
    Format inline markdown elements like bold, italic, links, and code.

    Args:
        text: Text with inline markdown

    Returns:
        HTML-formatted text
    """
    # Handle images: ![alt](url) - must be before links
    text = re.sub(r'!\[([^\]]*)\]\(([^\)]+)\)', r'<img src="\2" alt="\1" style="width: 100%; max-width: 600px; height: auto; display: block; margin: 16px 0; border-radius: 8px;" />', text)

    # Handle links: [text](url) including those with nested brackets like [[1]]
    # Match the brackets and everything inside, including nested brackets
    text = re.sub(r'\[(.+?)\]\(([^\)]+)\)', r'<a href="\2" style="color: #007acc; text-decoration: none;">\1</a>', text)

    # Handle bold: **text** or __text__
    text = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__([^_]+)__', r'<strong>\1</strong>', text)

    # Handle italic: *text* or _text_
    text = re.sub(r'\*([^\*]+)\*', r'<em>\1</em>', text)
    text = re.sub(r'_([^_]+)_', r'<em>\1</em>', text)

    # Handle strikethrough: ~~text~~
    text = re.sub(r'~~([^~]+)~~', r'<del>\1</del>', text)

    # Handle inline code: `code`
    text = re.sub(r'`([^`]+)`', lambda m: f'<code>{escape_html(m.group(1))}</code>', text)

    return text

def create_simple_newsletter_html(
    content_markdown: str,
    title: str,
    header_image_url: Optional[str] = None,
    subtitle: Optional[str] = None,
    post_link: Optional[str] = None
) -> str:
    """
    Create a simple newsletter HTML from markdown content.
    
    This template is intentionally simple to maximize deliverability.
    It includes:
    - Optional header image (shows as actual image, not link)
    - Title in h1 below the header image
    - Properly formatted markdown content
    - Footer with website link
    
    Args:
        content_markdown: Markdown content for the newsletter body
        title: Newsletter title (will be shown in h1)
        header_image_url: Optional URL for header image
        subtitle: Optional subtitle text
        post_link: Optional link to read on website
        
    Returns:
        Complete HTML email ready to send
    """
    # Convert markdown content to HTML
    content_html = markdown_to_html(content_markdown)
    
    # Build header image section if provided
    header_image_html = ""
    if header_image_url:
        header_image_html = f'''
        <img src="{header_image_url}" alt="Header Image" style="width: 100%; max-width: 600px; height: auto; display: block; margin: 0 0 24px 0; border-radius: 8px;" />
        '''
    
    # Build subtitle section if provided
    subtitle_html = ""
    if subtitle:
        subtitle_html = f'<p style="font-size: 18px; color: #666; margin: 0 0 20px 0; font-style: italic;">{escape_html(subtitle)}</p>'
    
    # Build footer link section
    footer_link_html = ""
    if post_link:
        footer_link_html = f'''
        <p style="margin: 20px 0 10px 0; font-size: 14px;">
            <a href="{post_link}" style="color: #007acc; text-decoration: none;">Read on website →</a>
        </p>
        '''
    
    # Create the complete HTML email
    html_email = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape_html(title)}</title>
    <style>
        /* Force all list items to use filled bullets */
        ul {{ list-style-type: disc; }}
        ul ul {{ list-style-type: disc; }}
        ul ul ul {{ list-style-type: disc; }}

        /* Reduce indentation for nested lists */
        ul, ol {{
            padding-left: 20px;
            margin: 8px 0;
        }}

        /* Ensure list items have proper spacing */
        li {{
            margin: 4px 0;
            padding-left: 4px;
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background-color: #ffffff;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 40px 30px;">
        {header_image_html}

        <h1 style="font-size: 28px; font-weight: 600; color: #1a1a1a; margin: 0 0 10px 0; line-height: 1.3;">{escape_html(title)}</h1>

        {subtitle_html}

        {footer_link_html}

        <div style="color: #333; font-size: 16px; line-height: 1.6; margin: 20px 0;">
            {content_html}
        </div>

        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #888;">
            <p style="margin: 0;">Forwarded this email? <a href="https://www.koiibenvenutto.com/" style="color: #007acc; text-decoration: none;">Subscribe here</a></p>
        </div>

        <div style="margin-top: 20px; font-size: 12px; color: #888;">
            <a href="{RESEND_UNSUBSCRIBE_PLACEHOLDER}" style="color: #888; text-decoration: underline;">Unsubscribe</a>
        </div>
    </div>
</body>
</html>'''
    
    return html_email


def format_rich_text_for_email(rich_text: List[Dict[str, Any]]) -> str:
    """
    Format Notion rich text array as HTML for emails.

    Args:
        rich_text: Array of Notion rich text objects

    Returns:
        HTML formatted text with inline styles
    """
    if not rich_text:
        return ""

    html_parts = []
    for text_obj in rich_text:
        if not text_obj or "plain_text" not in text_obj:
            continue

        text = escape_html(text_obj["plain_text"])
        annotations = text_obj.get("annotations", {})

        # Apply HTML formatting based on annotations
        if annotations.get("bold"):
            text = f"<strong>{text}</strong>"
        if annotations.get("italic"):
            text = f"<em>{text}</em>"
        if annotations.get("strikethrough"):
            text = f"<del>{text}</del>"
        if annotations.get("code"):
            text = f"<code>{text}</code>"

        # Handle links
        if text_obj.get("href"):
            text = f'<a href="{text_obj["href"]}" style="color: #007acc; text-decoration: none;">{text}</a>'

        html_parts.append(text)

    return "".join(html_parts)

def notion_blocks_to_markdown(blocks: List[Dict[str, Any]]) -> str:
    """
    Convert Notion blocks to markdown format.

    Args:
        blocks: List of Notion block objects

    Returns:
        Markdown representation of the blocks
    """
    if not blocks:
        return ""

    markdown_parts = []

    for block in blocks:
        block_type = block.get("type", "")
        content = block.get(block_type, {})

        # Extract rich text content with proper formatting
        rich_text = content.get("rich_text", [])
        text_content = ""

        for text_obj in rich_text:
            if text_obj and "plain_text" in text_obj:
                plain_text = text_obj["plain_text"]
                annotations = text_obj.get("annotations", {})

                # Handle mentions
                if text_obj.get("type") == "mention":
                    mention = text_obj.get("mention", {})
                    if mention.get("type") == "page":
                        page_id = mention.get("page", {}).get("id", "")
                        plain_text = f"[{plain_text}](https://notion.so/{page_id.replace('-', '')})"
                    elif mention.get("type") == "database":
                        database_id = mention.get("database", {}).get("id", "")
                        plain_text = f"[{plain_text}](https://notion.so/{database_id.replace('-', '')})"
                    elif mention.get("type") == "link_preview":
                        url = mention.get("link_preview", {}).get("url", "")
                        plain_text = f"[{plain_text}]({url})"

                # Apply markdown formatting (skip bold/italic for headings - they're already visually prominent)
                is_heading = block_type in ("heading_1", "heading_2", "heading_3")
                if annotations.get("bold") and not is_heading:
                    plain_text = f"**{plain_text}**"
                if annotations.get("italic") and not is_heading:
                    plain_text = f"*{plain_text}*"
                if annotations.get("strikethrough"):
                    plain_text = f"~~{plain_text}~~"
                if annotations.get("code"):
                    plain_text = f"`{plain_text}`"

                # Handle links
                if text_obj.get("href"):
                    plain_text = f"[{plain_text}]({text_obj['href']})"

                text_content += plain_text

        if not text_content.strip():
            # Handle blocks that don't use rich_text
            if block_type == "divider":
                markdown_parts.append("\n---\n")
                continue
            elif block_type == "image":
                # Image blocks store their URL in external/file, not rich_text
                image_url = ""
                if content.get("type") == "external":
                    image_url = content.get("external", {}).get("url", "")
                elif content.get("type") == "file":
                    image_url = content.get("file", {}).get("url", "")

                if image_url:
                    caption = ""
                    if content.get("caption"):
                        caption_parts = [t.get("plain_text", "") for t in content.get("caption", []) if t]
                        caption = "".join(caption_parts)
                    markdown_parts.append(f"\n![{caption}]({image_url})\n")
                continue
            else:
                continue

        # Format based on block type
        if block_type == "heading_1":
            markdown_parts.append(f"\n# {text_content}\n")
        elif block_type == "heading_2":
            markdown_parts.append(f"\n## {text_content}\n")
        elif block_type == "heading_3":
            markdown_parts.append(f"\n### {text_content}\n")
        elif block_type == "paragraph":
            markdown_parts.append(f"\n{text_content}\n")
        elif block_type == "bulleted_list_item":
            # Check for nested items or callouts
            if block.get("children"):
                # Output as HTML for proper nesting support
                list_html = f"<li>{text_content}"

                # Process nested children (can be list items or callouts)
                nested_items = []
                nested_callouts = []
                for child in block["children"]:
                    if not child:
                        continue
                    child_type = child.get("type", "")
                    if child_type in ["bulleted_list_item", "numbered_list_item"]:
                        nested_items.append(child)
                    elif child_type == "callout":
                        nested_callouts.append(child)

                # Add nested callouts first
                if nested_callouts:
                    for callout_child in nested_callouts:
                        callout_content = callout_child.get("callout", {})
                        icon_obj = callout_content.get("icon") or {}
                        emoji = icon_obj.get("emoji", "💡") if isinstance(icon_obj, dict) else "💡"
                        callout_rich_text = callout_content.get("rich_text", [])
                        callout_text = format_rich_text_for_email(callout_rich_text)

                        # Process callout children (multi-paragraph callouts)
                        if callout_child.get("children"):
                            callout_child_parts = [callout_text] if callout_text else []
                            for callout_grandchild in callout_child.get("children", []):
                                if not callout_grandchild:
                                    continue
                                gc_type = callout_grandchild.get("type", "")
                                gc_content = callout_grandchild.get(gc_type, {})
                                gc_rich_text = gc_content.get("rich_text", [])
                                gc_text = format_rich_text_for_email(gc_rich_text)
                                if gc_text:
                                    callout_child_parts.append(gc_text)
                            callout_text = "<br><br>".join(callout_child_parts)

                        # Output inline callout within list item
                        list_html += f'''
<div style="border: 1px solid #1a1a1a; border-radius: 8px; padding: 12px; margin: 8px 0; display: block;">
    <span style="font-size: 1.2em; margin-right: 8px;">{emoji}</span>{callout_text}
</div>'''

                # Wrap nested list items in proper list tags
                if nested_items:
                    nested_list_tag = "ul" if nested_items[0].get("type") == "bulleted_list_item" else "ol"
                    list_html += f"\n<{nested_list_tag}>\n"
                    for child in nested_items:
                        child_content = child.get(child.get("type", ""), {})
                        child_rich_text = child_content.get("rich_text", [])
                        child_text = "".join([t.get("plain_text", "") for t in child_rich_text if t])
                        list_html += f"<li>{child_text}</li>\n"
                    list_html += f"</{nested_list_tag}>\n"

                list_html += "</li>\n"
                markdown_parts.append(list_html)
            else:
                markdown_parts.append(f"- {text_content}\n")

        elif block_type == "numbered_list_item":
            # Check for nested items or callouts
            if block.get("children"):
                # Output as HTML for proper nesting support
                list_html = f"<li>{text_content}"

                # Process nested children (can be list items or callouts)
                nested_items = []
                nested_callouts = []
                for child in block["children"]:
                    if not child:
                        continue
                    child_type = child.get("type", "")
                    if child_type in ["bulleted_list_item", "numbered_list_item"]:
                        nested_items.append(child)
                    elif child_type == "callout":
                        nested_callouts.append(child)

                # Add nested callouts first
                if nested_callouts:
                    for callout_child in nested_callouts:
                        callout_content = callout_child.get("callout", {})
                        icon_obj = callout_content.get("icon") or {}
                        emoji = icon_obj.get("emoji", "💡") if isinstance(icon_obj, dict) else "💡"
                        callout_rich_text = callout_content.get("rich_text", [])
                        callout_text = format_rich_text_for_email(callout_rich_text)

                        # Process callout children (multi-paragraph callouts)
                        if callout_child.get("children"):
                            callout_child_parts = [callout_text] if callout_text else []
                            for callout_grandchild in callout_child.get("children", []):
                                if not callout_grandchild:
                                    continue
                                gc_type = callout_grandchild.get("type", "")
                                gc_content = callout_grandchild.get(gc_type, {})
                                gc_rich_text = gc_content.get("rich_text", [])
                                gc_text = format_rich_text_for_email(gc_rich_text)
                                if gc_text:
                                    callout_child_parts.append(gc_text)
                            callout_text = "<br><br>".join(callout_child_parts)

                        # Output inline callout within list item
                        list_html += f'''
<div style="border: 1px solid #1a1a1a; border-radius: 8px; padding: 12px; margin: 8px 0; display: block;">
    <span style="font-size: 1.2em; margin-right: 8px;">{emoji}</span>{callout_text}
</div>'''

                # Wrap nested list items in proper list tags
                if nested_items:
                    nested_list_tag = "ul" if nested_items[0].get("type") == "bulleted_list_item" else "ol"
                    list_html += f"\n<{nested_list_tag}>\n"
                    for child in nested_items:
                        child_content = child.get(child.get("type", ""), {})
                        child_rich_text = child_content.get("rich_text", [])
                        child_text = "".join([t.get("plain_text", "") for t in child_rich_text if t])
                        list_html += f"<li>{child_text}</li>\n"
                    list_html += f"</{nested_list_tag}>\n"

                list_html += "</li>\n"
                markdown_parts.append(list_html)
            else:
                markdown_parts.append(f"1. {text_content}\n")
        elif block_type == "quote":
            markdown_parts.append(f"\n> {text_content}\n")
        elif block_type == "code":
            language = content.get("language", "")
            markdown_parts.append(f"\n```{language}\n{text_content}\n```\n")
        elif block_type == "divider":
            markdown_parts.append("\n---\n")
        elif block_type == "callout":
            # Handle callouts with emoji - output as HTML directly for emails
            icon_obj = content.get("icon") or {}
            emoji = icon_obj.get("emoji", "💡") if isinstance(icon_obj, dict) else "💡"

            # Build full callout content with children
            callout_lines = []
            if text_content and text_content.strip():
                callout_lines.append(text_content)

            # Process children if they exist
            if block.get("children"):
                for child in block["children"]:
                    if not child:
                        continue
                    child_type = child.get("type", "")
                    if not child_type:
                        continue
                    child_content = child.get(child_type) or {}
                    if not isinstance(child_content, dict):
                        continue
                    child_rich_text = child_content.get("rich_text") or []
                    child_text = "".join([t.get("plain_text", "") for t in child_rich_text if t])
                    if child_text and child_text.strip():
                        callout_lines.append(child_text)

            # Only create callout if we have content
            if callout_lines:
                # Join all lines and format as inline-styled HTML for emails
                callout_html = f'''<div style="border: 1px solid #1a1a1a; border-radius: 8px; padding: 16px; margin: 16px 0; display: block;">
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333;">
                        <span style="font-size: 1.2em; margin-right: 8px;">{emoji}</span>{callout_lines[0]}'''

                # Add remaining lines as separate paragraphs within the callout
                if len(callout_lines) > 1:
                    for line in callout_lines[1:]:
                        callout_html += f'<br><br>{line}'

                callout_html += '''</div>
                </div>'''

                markdown_parts.append(f"\n{callout_html}\n")
        elif block_type == "image":
            # Image blocks are handled in the empty text_content check above
            pass
        else:
            # Default formatting for other block types
            if text_content.strip():
                markdown_parts.append(f"\n{text_content}\n")

    # Join all parts
    full_markdown = "".join(markdown_parts)

    # Clean up excessive newlines
    full_markdown = re.sub(r'\n{3,}', '\n\n', full_markdown)

    return full_markdown.strip()


def notion_blocks_to_plain_text(blocks: List[Dict[str, Any]]) -> str:
    """
    Convert Notion blocks to plain text format (deprecated - use notion_blocks_to_markdown instead).
    
    This function is kept for backward compatibility.

    Args:
        blocks: List of Notion block objects

    Returns:
        Plain text representation of the blocks
    """
    # Just convert to markdown which can be rendered as plain text if needed
    return notion_blocks_to_markdown(blocks)


def create_plain_text_newsletter(
    content_text: str,
    newsletter_title: str,
    subtitle: Optional[str] = None,
    post_link: Optional[str] = None,
    from_name: Optional[str] = None,
    cover_image_url: Optional[str] = None
) -> str:
    """
    Create a plain text newsletter from content (deprecated - kept for backward compatibility).
    
    Args:
        content_text: Plain text/markdown content
        newsletter_title: Title of the newsletter
        subtitle: Optional subtitle
        post_link: Optional link to full post
        from_name: Optional sender name
        cover_image_url: Optional cover image URL
        
    Returns:
        Plain text newsletter ready to send
    """
    # Basic input validation
    if not content_text:
        raise ValueError("Missing required input: content is required")
    
    # Build the plain text email
    email_parts = []
    
    # Title
    email_parts.append(newsletter_title)
    email_parts.append("")
    
    # Include cover image if provided (as URL in plain text)
    if cover_image_url:
        email_parts.append(f"🖼️ {cover_image_url}")
        email_parts.append("")
    
    # Subtitle if provided
    if subtitle:
        email_parts.append(subtitle)
        email_parts.append("")
    
    # Content
    email_parts.append(content_text)
    email_parts.append("")
    
    # Footer
    email_parts.append("")
    if post_link:
        email_parts.append(f"Read on website: {post_link}")
    email_parts.append("")
    email_parts.append("Forwarded this email? Subscribe: https://www.koiibenvenutto.com/")
    
    return "\n".join(email_parts)