"""
Context serialization for passing Promaia's loaded content to Claude Code agents.

Converts Promaia's internal data structures into markdown format that
Claude Code agents can understand and use effectively.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime


def serialize_context_for_agent(
    loaded_content: Dict[str, List[Dict[str, Any]]],
    max_pages_per_database: Optional[int] = None
) -> str:
    """
    Convert Promaia's loaded content into markdown format for Claude Code agent.

    Args:
        loaded_content: Dict mapping database names to lists of pages
                       Format: {"workspace.database": [page1, page2, ...]}
        max_pages_per_database: Optional limit on pages per database (for context size)

    Returns:
        Formatted markdown string with all context

    Example:
        >>> content = {
        ...     "koii.journal": [
        ...         {"title": "2024-01-15", "content": "Worked on API", ...},
        ...         {"title": "2024-01-14", "content": "Bug fixes", ...}
        ...     ]
        ... }
        >>> print(serialize_context_for_agent(content))
        # Context from Promaia

        ## Database: koii.journal (2 entries)

        ### Entry 1: 2024-01-15
        Worked on API
        ...
    """
    if not loaded_content:
        return "# Context from Promaia\n\nNo context loaded."

    sections = ["# Context from Promaia", ""]

    # Add summary
    total_pages = sum(len(pages) for pages in loaded_content.values())
    sections.append(f"**Total entries loaded:** {total_pages}")
    sections.append(f"**Databases:** {len(loaded_content)}")
    sections.append("")

    # Add each database
    for db_name, pages in loaded_content.items():
        if not pages:
            continue

        # Limit pages if specified
        pages_to_show = pages
        if max_pages_per_database and len(pages) > max_pages_per_database:
            pages_to_show = pages[:max_pages_per_database]
            truncated = len(pages) - max_pages_per_database
        else:
            truncated = 0

        sections.append(f"## Database: {db_name} ({len(pages)} entries)")
        if truncated > 0:
            sections.append(f"*Showing first {max_pages_per_database}, {truncated} more available*")
        sections.append("")

        # Add each page
        for i, page in enumerate(pages_to_show, 1):
            title = page.get('title', 'Untitled')
            sections.append(f"### Entry {i}: {title}")

            # Add metadata if available
            if 'created_time' in page:
                created = page['created_time']
                if isinstance(created, str):
                    sections.append(f"**Created:** {created}")
            if 'last_edited_time' in page:
                edited = page['last_edited_time']
                if isinstance(edited, str):
                    sections.append(f"**Last edited:** {edited}")

            # Add properties if available
            if 'properties' in page:
                props = page['properties']
                if props:
                    sections.append("**Properties:**")
                    for key, value in props.items():
                        if value and key not in ['title', 'Title']:
                            # Format property value
                            if isinstance(value, list):
                                value_str = ", ".join(str(v) for v in value)
                            else:
                                value_str = str(value)
                            sections.append(f"  - {key}: {value_str}")

            # Add content
            content = page.get('content', '')
            if content:
                sections.append("")
                sections.append(content)

            sections.append("")  # Blank line between entries

        sections.append("---")  # Separator between databases
        sections.append("")

    return "\n".join(sections)


def create_agent_system_prompt(
    context: str,
    task: str,
    workspace: Optional[str] = None
) -> str:
    """
    Create system prompt for Claude Code agent with Promaia's context.

    Args:
        context: Serialized context from serialize_context_for_agent()
        task: The user's action request
        workspace: Optional workspace name for context

    Returns:
        Complete system prompt for the agent
    """
    prompt_parts = [
        "You are a Claude Code agent spawned by Promaia to handle an action request.",
        "",
        "Promaia has already loaded relevant context from the user's databases.",
        "This context is provided below. Use it to inform your actions.",
        "",
    ]

    if workspace:
        prompt_parts.append(f"**User's workspace:** {workspace}")
        prompt_parts.append("")

    prompt_parts.extend([
        "# Your Task",
        "",
        task,
        "",
        "---",
        "",
        context,
        "",
        "---",
        "",
        "# Instructions",
        "",
        "1. Use the context provided by Promaia to inform your actions",
        "2. You have access to MCPs (Gmail, Notion, etc.) via tools",
        "3. If you need more context, you can ask Promaia (the user will relay)",
        "4. Be concise and action-oriented",
        "5. When done, summarize what you accomplished",
        "",
        "Begin working on the task now."
    ])

    return "\n".join(prompt_parts)


def format_context_summary(loaded_content: Dict[str, List[Dict[str, Any]]]) -> str:
    """
    Create a brief summary of loaded context for display to user.

    Args:
        loaded_content: Dict mapping database names to lists of pages

    Returns:
        Short summary string

    Example:
        >>> content = {"koii.journal": [{...}, {...}], "koii.stories": [{...}]}
        >>> format_context_summary(content)
        "Loaded 3 entries from 2 databases (journal: 2, stories: 1)"
    """
    if not loaded_content:
        return "No context loaded"

    total_pages = sum(len(pages) for pages in loaded_content.values())
    db_count = len(loaded_content)

    # Create breakdown by database
    breakdown = []
    for db_name, pages in loaded_content.items():
        # Extract just the database name without workspace prefix
        short_name = db_name.split('.')[-1] if '.' in db_name else db_name
        breakdown.append(f"{short_name}: {len(pages)}")

    breakdown_str = ", ".join(breakdown)

    return f"Loaded {total_pages} entries from {db_count} databases ({breakdown_str})"


def extract_key_info_for_agent(
    loaded_content: Dict[str, List[Dict[str, Any]]],
    query: str
) -> Dict[str, Any]:
    """
    Extract key information from loaded content that's most relevant to the query.

    This is useful for providing focused context to the agent without
    overwhelming it with all loaded data.

    Args:
        loaded_content: Dict mapping database names to lists of pages
        query: The user's action request

    Returns:
        Dict with key information extracted
    """
    # TODO: Implement smart extraction based on query
    # For now, just return basic stats
    return {
        'total_entries': sum(len(pages) for pages in loaded_content.values()),
        'databases': list(loaded_content.keys()),
        'date_range': _extract_date_range(loaded_content),
        'key_topics': _extract_key_topics(loaded_content, query)
    }


def _extract_date_range(loaded_content: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    """Extract the date range of loaded content."""
    dates = []
    for pages in loaded_content.values():
        for page in pages:
            if 'created_time' in page:
                dates.append(page['created_time'])
            if 'last_edited_time' in page:
                dates.append(page['last_edited_time'])

    if not dates:
        return {}

    # Parse dates (assuming ISO format)
    try:
        parsed_dates = [datetime.fromisoformat(d.replace('Z', '+00:00')) for d in dates if d]
        if parsed_dates:
            return {
                'earliest': min(parsed_dates).isoformat(),
                'latest': max(parsed_dates).isoformat()
            }
    except Exception:
        pass

    return {}


def _extract_key_topics(
    loaded_content: Dict[str, List[Dict[str, Any]]],
    query: str
) -> List[str]:
    """Extract key topics/keywords from loaded content relevant to query."""
    # TODO: Implement NLP-based topic extraction
    # For now, just extract common words from titles
    topics = set()
    query_words = set(query.lower().split())

    for pages in loaded_content.values():
        for page in pages:
            title = page.get('title', '').lower()
            # Find words that appear in both title and query
            title_words = set(title.split())
            common = title_words & query_words
            topics.update(common)

    return list(topics)[:10]  # Limit to top 10


# For testing
if __name__ == "__main__":
    # Sample test data
    test_content = {
        "koii.journal": [
            {
                "title": "2024-01-15 - Monday",
                "content": "Worked on the new API endpoints. Fixed bug in authentication.",
                "created_time": "2024-01-15T09:00:00Z",
                "properties": {
                    "mood": "productive",
                    "tags": ["work", "api"]
                }
            },
            {
                "title": "2024-01-14 - Sunday",
                "content": "Relaxed day. Read about agent architectures.",
                "created_time": "2024-01-14T18:00:00Z",
                "properties": {
                    "mood": "calm"
                }
            }
        ],
        "koii.stories": [
            {
                "title": "API v2 Launch",
                "content": "Planning the launch timeline for API v2",
                "properties": {
                    "status": "In Progress",
                    "priority": "P1"
                }
            }
        ]
    }

    print("Context Serialization Test")
    print("=" * 60)
    print()

    # Test serialization
    serialized = serialize_context_for_agent(test_content)
    print(serialized)
    print()
    print("=" * 60)
    print()

    # Test summary
    summary = format_context_summary(test_content)
    print(f"Summary: {summary}")
    print()

    # Test system prompt
    task = "Send Federico an email about the API launch"
    system_prompt = create_agent_system_prompt(serialized, task, workspace="koii")
    print("System Prompt Preview:")
    print(system_prompt[:500] + "...")
