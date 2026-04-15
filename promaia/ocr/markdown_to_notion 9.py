"""
Convert markdown text to Notion blocks.

Parses markdown structure and creates appropriate Notion block types
(headings, paragraphs, dividers, lists, etc.)
"""
import re
from typing import List, Dict, Any


def markdown_to_notion_blocks(markdown_text: str, max_blocks: int = 100) -> List[Dict[str, Any]]:
    """
    Convert markdown text to Notion blocks.

    Args:
        markdown_text: Text with markdown formatting
        max_blocks: Maximum number of blocks to create

    Returns:
        List of Notion block objects
    """
    blocks = []
    lines = markdown_text.split('\n')

    i = 0
    while i < len(lines) and len(blocks) < max_blocks:
        line = lines[i]

        # Skip empty lines
        if not line.strip():
            i += 1
            continue

        # Heading 1: # Text
        if line.startswith('# ') and not line.startswith('## '):
            blocks.append(create_heading_block(line[2:].strip(), 1))
        # Heading 2: ## Text
        elif line.startswith('## ') and not line.startswith('### '):
            blocks.append(create_heading_block(line[3:].strip(), 2))
        # Heading 3: ### Text
        elif line.startswith('### '):
            blocks.append(create_heading_block(line[4:].strip(), 3))
        # Divider: --- or ***
        elif line.strip() in ['---', '***', '___']:
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        # Bullet list: - Text or * Text
        elif line.strip().startswith(('- ', '* ')):
            rich_text = parse_inline_markdown(line.strip()[2:])
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich_text}
            })
        # Numbered list: 1. Text
        elif re.match(r'^\d+\.\s', line.strip()):
            text = re.sub(r'^\d+\.\s', '', line.strip())
            rich_text = parse_inline_markdown(text)
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": rich_text}
            })
        # Code block: ```
        elif line.strip().startswith('```'):
            # Collect code block lines
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": '\n'.join(code_lines)}}],
                    "language": "plain text"
                }
            })
        # Quote: > Text
        elif line.strip().startswith('> '):
            rich_text = parse_inline_markdown(line.strip()[2:])
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": rich_text}
            })
        # Regular paragraph
        else:
            rich_text = parse_inline_markdown(line.strip())
            if rich_text:  # Only add if there's content
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": rich_text}
                })

        i += 1

    return blocks


def create_heading_block(text: str, level: int) -> Dict[str, Any]:
    """Create a Notion heading block."""
    rich_text = parse_inline_markdown(text)
    heading_type = f"heading_{level}"

    return {
        "object": "block",
        "type": heading_type,
        heading_type: {"rich_text": rich_text}
    }


def parse_inline_markdown(text: str) -> List[Dict[str, Any]]:
    """
    Parse inline markdown formatting (bold, italic, strikethrough, code).

    Returns list of rich_text objects with annotations.
    """
    if not text:
        return []

    rich_text = []
    current_pos = 0

    # Pattern for markdown inline formatting
    # Matches: **bold**, *italic*, ~~strikethrough~~, `code`
    pattern = r'(\*\*.*?\*\*|\*(?!\*).*?\*(?!\*)|~~.*?~~|`.*?`|\[.*?\]\(.*?\))'

    matches = list(re.finditer(pattern, text))

    for match in matches:
        # Add plain text before match
        if match.start() > current_pos:
            plain = text[current_pos:match.start()]
            if plain:
                rich_text.append(create_text_object(plain))

        # Add formatted text
        matched_text = match.group(0)

        # Bold: **text**
        if matched_text.startswith('**') and matched_text.endswith('**'):
            content = matched_text[2:-2]
            rich_text.append(create_text_object(content, bold=True))

        # Italic: *text*
        elif matched_text.startswith('*') and matched_text.endswith('*') and not matched_text.startswith('**'):
            content = matched_text[1:-1]
            rich_text.append(create_text_object(content, italic=True))

        # Strikethrough: ~~text~~
        elif matched_text.startswith('~~') and matched_text.endswith('~~'):
            content = matched_text[2:-2]
            rich_text.append(create_text_object(content, strikethrough=True))

        # Inline code: `code`
        elif matched_text.startswith('`') and matched_text.endswith('`'):
            content = matched_text[1:-1]
            rich_text.append(create_text_object(content, code=True))

        # Link: [text](url)
        elif matched_text.startswith('[') and ')' in matched_text:
            link_match = re.match(r'\[(.*?)\]\((.*?)\)', matched_text)
            if link_match:
                link_text, url = link_match.groups()
                rich_text.append(create_text_object(link_text, url=url))
            else:
                # Failed to parse link, treat as plain text
                rich_text.append(create_text_object(matched_text))

        current_pos = match.end()

    # Add remaining plain text
    if current_pos < len(text):
        plain = text[current_pos:]
        if plain:
            rich_text.append(create_text_object(plain))

    # If no formatting found, return as plain text
    if not rich_text:
        rich_text = [create_text_object(text)]

    return rich_text


def create_text_object(
    content: str,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    underline: bool = False,
    code: bool = False,
    url: str = None
) -> Dict[str, Any]:
    """Create a Notion rich_text object with annotations."""
    # Notion has a 2000 character limit per rich_text object
    content = content[:2000]

    text_obj = {
        "type": "text",
        "text": {"content": content},
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": strikethrough,
            "underline": underline,
            "code": code,
            "color": "default"
        }
    }

    if url:
        text_obj["text"]["link"] = {"url": url}

    return text_obj
