"""
Functions for creating and managing AI model system prompts.
"""
import os
import datetime
from typing import List, Dict, Any, Optional
import logging

from pathlib import Path
from promaia.utils.env_writer import get_prompts_dir
from promaia.config.paths import get_project_root

logger = logging.getLogger(__name__)

# Default prompts ship in {repo}/prompts/, user customizations live in maia-data
_REPO_PROMPTS_DIR = Path(get_project_root()) / "prompts"


def _resolve_prompt(filename: str) -> Path:
    """Return maia-data path if it exists, otherwise fall back to repo default."""
    user_path = get_prompts_dir() / filename
    if user_path.exists():
        return user_path
    default_path = _REPO_PROMPTS_DIR / filename
    if default_path.exists():
        return default_path
    return user_path  # return expected path for error messages


PROMPT_FILE_PATH = _resolve_prompt("prompt.md")
ARTIFACT_GUIDELINES_PATH = _resolve_prompt("artifact_guidelines.md")
NOTION_PROMPT_PAGE_ID = "292d1339-6967-80a5-84ed-cc171358ccb7"  # Main prompt page ID


def fetch_prompt_from_notion(workspace: Optional[str] = None) -> Optional[str]:
    """
    Fetch the main prompt. Uses synced local file for speed.

    The prompt is synced from Notion during database sync operations.
    This function just loads the local cached version for fast access.

    Args:
        workspace: Optional workspace name. If not provided, uses default workspace.

    Returns:
        Prompt text from local file, or None if not available
    """
    try:
        # Use environment variable to enable/disable Notion prompts
        use_notion_prompts = os.getenv("PROMAIA_USE_NOTION_PROMPTS", "true").lower() == "true"

        if not use_notion_prompts:
            logger.debug("Notion prompts disabled via PROMAIA_USE_NOTION_PROMPTS env var")
            return None

        # Import here to avoid circular dependencies
        from promaia.notion.prompts import get_main_prompt_from_file

        # Load from local synced file (fast!)
        prompt_text = get_main_prompt_from_file()
        if prompt_text:
            logger.debug("Loaded prompt from synced local file")
            return prompt_text
        else:
            logger.debug("No synced prompt file found, will use default %s", PROMPT_FILE_PATH)
            return None

    except ImportError:
        logger.debug("Notion prompts module not available, using local file")
        return None
    except Exception as e:
        logger.warning(f"Error loading prompt from local file: {e}")
        return None


def format_context_data(
    multi_source_data: Dict[str, List[Dict[str, Any]]],
    mcp_tools_info: Optional[str] = None,
) -> str:
    """
    Format context data from multiple sources into a string.

    This can be appended to any base prompt to provide context.
    """
    if not multi_source_data and not mcp_tools_info:
        return ""

    context_str = ""

    if multi_source_data:
        # Append data sources
        context_str += f"\n\n## Context ({sum(len(pages) for pages in multi_source_data.values())} total entries):"
    
        for database_name, pages in multi_source_data.items():
            context_str += f"\n\n### === {database_name.upper()} DATABASE ({len(pages)} entries) ===\n"

            # Add database-specific descriptions
            if 'journal' in database_name.lower():
                context_str += "These are personal journal entries and daily reflections:\n"
            elif 'cms' in database_name.lower():
                context_str += "These are blog posts and published content:\n"
            elif _is_discord_database(database_name):
                context_str += "These are Discord server messages:\n"
            elif 'shopify' in database_name.lower():
                context_str += "These are Shopify store records (orders, products, or inventory):\n"
            else:
                context_str += f"These are {database_name} entries:\n"

            if not pages:
                context_str += "No entries found for this database.\n"
            else:
                if _is_discord_database(database_name):
                    # Group Discord messages by channel
                    channels = {}
                    for page in pages:
                        channel_name = "unknown_channel"

                        # Handle metadata that might be a JSON string or dict
                        metadata = page.get('metadata')
                        if metadata:
                            if isinstance(metadata, str):
                                try:
                                    import json
                                    metadata = json.loads(metadata)
                                except (json.JSONDecodeError, TypeError):
                                    metadata = {}

                            # Try multiple ways to get channel name
                            if metadata.get('discord_channel_name'):
                                channel_name = metadata['discord_channel_name']
                            elif metadata.get('channel_name'):
                                # Remove # prefix if present
                                channel_name = metadata['channel_name'].lstrip('#')
                            elif metadata.get('properties', {}).get('channel_name'):
                                channel_name = metadata['properties']['channel_name'].lstrip('#')

                        if channel_name not in channels:
                            channels[channel_name] = []
                        channels[channel_name].append(page)

                    # Format content with channel subheadings
                    for channel_name, channel_pages in channels.items():
                        context_str += f"\n#### Channel: #{channel_name}\n"
                        for page in channel_pages:
                            page_filename = page.get('filename', 'Unknown File')
                            page_content = page.get('content', '')

                            # Extract timestamp and author from metadata for minimal format
                            metadata = page.get('metadata')
                            timestamp_display = "unknown_time"
                            author_display = "unknown_author"

                            if metadata:
                                if isinstance(metadata, str):
                                    try:
                                        import json
                                        metadata = json.loads(metadata)
                                    except (json.JSONDecodeError, TypeError):
                                        metadata = {}

                                # Get timestamp in readable format
                                timestamp_str = metadata.get('timestamp') or metadata.get('created_time')
                                if timestamp_str:
                                    try:
                                        dt = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                                        timestamp_display = dt.strftime("%Y-%m-%d %H:%M:%S")
                                    except ValueError:
                                        pass

                                # Get author name (try multiple field names)
                                author_display = (metadata.get('author_name') or
                                                metadata.get('author') or
                                                metadata.get('properties', {}).get('author_name') or
                                                'unknown_author')

                            # Use minimal format: **`timestamp author #channel filename`**
                            context_str += f"\n**`{timestamp_display}  {author_display}  #{channel_name}  {page_filename}`**\n\n{page_content}\n"
                elif _is_shopify_data(pages):
                    # Shopify direct-data: raw SQL rows, not page content
                    for page in pages:
                        label = _shopify_row_label(page)
                        details = "\n".join(
                            f"  {k}: {v}" for k, v in page.items()
                            if v is not None and v != ''
                        )
                        context_str += f"\n**{label}**\n{details}\n"
                else:
                    for page in pages:
                        page_filename = (page.get('filename') or
                                       page.get('title') or
                                       page.get('name') or
                                       'Unknown File')
                        page_content = page.get('content', '')
                        # Include date if available so the agent knows when entries are from
                        page_date = page.get('date') or page.get('created_time', '')
                        if page_date:
                            # Normalize to just the date portion
                            date_display = str(page_date)[:10]
                            context_str += f"\n**{database_name}** entry (Date: {date_display}, File: `{page_filename}`):\n{page_content}\n"
                        else:
                            context_str += f"\n**{database_name}** entry (File: `{page_filename}`):\n{page_content}\n"

    # Add MCP tools information if provided
    if mcp_tools_info:
        context_str += f"\n\n{mcp_tools_info}"

    return context_str


def create_system_prompt(
    multi_source_data: Dict[str, List[Dict[str, Any]]],
    mcp_tools_info: Optional[str] = None,
    include_query_tools: bool = True,
    workspace: Optional[str] = None,
) -> str:
    """
    Create a system prompt that includes content from multiple data sources.

    Args:
        multi_source_data: Dict mapping database names to lists of page data
        mcp_tools_info: Optional formatted MCP tools information
        include_query_tools: Whether to include built-in query tools (default: True)
        workspace: Current workspace for database preview (default: None)
    """
    today = datetime.datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    current_time_str = today.strftime("%H:%M")

    # Try to fetch from Notion first
    base_prompt = fetch_prompt_from_notion(workspace=workspace)

    # Fall back to local file if Notion fails
    if not base_prompt:
        try:
            with open(PROMPT_FILE_PATH, 'r', encoding='utf-8') as f:
                base_prompt = f.read()
            logger.debug(f"Loaded system prompt from {PROMPT_FILE_PATH}")
        except FileNotFoundError:
            logger.error(f"System prompt file not found at {PROMPT_FILE_PATH}. Using a fallback prompt.")
            base_prompt = "You are a helpful AI assistant. Today's date is {today_date}."

    base_prompt = base_prompt.replace("{today_date}", today_str)
    base_prompt = base_prompt.replace("{current_time}", current_time_str)

    # Artifact guidelines removed — now inline in conversation_mode.md

    # Add query tools if enabled (non-agentic path only)
    if include_query_tools:
        base_prompt += "\n\n" + format_query_tools_for_prompt()

    # Database preview — shows available data sources regardless of query tools
    loaded_databases = list(multi_source_data.keys())
    db_preview = generate_database_preview(workspace=workspace, exclude_databases=loaded_databases)
    if db_preview:
        base_prompt += "\n\n" + db_preview

    # Append context data
    base_prompt += format_context_data(multi_source_data, mcp_tools_info)

    return base_prompt


def _is_discord_database(database_name: str) -> bool:
    """Check if a database is a Discord source based on its name or content."""
    return 'discord' in database_name.lower() or database_name.lower().endswith('.ds')


# Columns that indicate a row comes from a Shopify table
_SHOPIFY_MARKER_KEYS = {'order_number', 'financial_status', 'fulfillment_status',
                        'product_type', 'variant_id', 'inventory_item_id', 'sku',
                        'cost'}


def _is_shopify_data(pages: list) -> bool:
    """Detect if pages are raw Shopify SQL rows rather than page-content dicts."""
    if not pages:
        return False
    first = pages[0]
    return bool(set(first.keys()) & _SHOPIFY_MARKER_KEYS)


def _shopify_row_label(row: dict) -> str:
    """Build a human-readable label for a Shopify row."""
    # Order row
    if 'order_number' in row:
        parts = [f"Order #{row['order_number']}"]
        if row.get('total_price'):
            parts.append(f"${row['total_price']} {row.get('currency', '')}")
        if row.get('financial_status'):
            parts.append(row['financial_status'])
        return ' — '.join(parts)
    # Product row
    if 'product_type' in row or 'handle' in row:
        return row.get('title') or row.get('handle') or 'Product'
    # Inventory snapshot row
    if 'variant_id' in row or 'inventory_item_id' in row:
        parts = []
        if row.get('product_title'):
            parts.append(row['product_title'])
        if row.get('variant_title'):
            parts.append(row['variant_title'])
        if row.get('sku'):
            parts.append(f"SKU {row['sku']}")
        if row.get('available') is not None:
            parts.append(f"qty {row['available']}")
        return ' — '.join(parts) if parts else 'Inventory Snapshot'
    # Fallback
    return row.get('title') or row.get('name') or 'Shopify Entry'


def generate_database_preview(
    workspace: Optional[str] = None,
    exclude_databases: Optional[List[str]] = None,
    limit_to_databases: Optional[List[str]] = None,
    max_examples: int = 3
) -> str:
    """
    Generate a preview/map of available databases with sample content.

    Similar to what the SQL query AI sees, this gives the chat AI a "heads up display"
    showing what data sources exist and what they contain.

    Args:
        workspace: Optional workspace to filter databases (None for all)
        exclude_databases: List of database names already in loaded context (to avoid duplication)
        limit_to_databases: Only include these databases (permission filter)
        max_examples: Number of sample entries per database (default: 3)

    Returns:
        Formatted database preview string with samples
    """
    import sqlite3
    import json
    from datetime import datetime

    from promaia.utils.env_writer import get_db_path
    db_path = str(get_db_path())

    if exclude_databases is None:
        exclude_databases = []

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get list of all databases in workspace
        if workspace:
            cursor.execute("""
                SELECT DISTINCT database_name, workspace
                FROM unified_content
                WHERE workspace = ?
                ORDER BY database_name
            """, (workspace,))
        else:
            cursor.execute("""
                SELECT DISTINCT database_name, workspace
                FROM unified_content
                ORDER BY workspace, database_name
            """)

        databases = cursor.fetchall()

        if not databases:
            return ""

        # Filter out databases from archived workspaces
        from promaia.config.workspaces import get_workspace_manager
        workspace_manager = get_workspace_manager()
        
        active_databases = []
        for db_name, db_workspace in databases:
            workspace_obj = workspace_manager.get_workspace(db_workspace)
            if workspace_obj and not workspace_obj.archived:
                active_databases.append((db_name, db_workspace))
        
        databases = active_databases

        if not databases:
            return ""

        preview = "## Available Data Sources"
        if workspace:
            preview += f" (Workspace: {workspace})"
        preview += "\n\n"

        # Add workspace overview to help AI understand workspace structure
        if not workspace:  # Only show overview when not filtering by workspace
            # Group databases by workspace
            workspace_map = {}
            for db_name, db_workspace in databases:
                if db_name not in exclude_databases:
                    if db_workspace not in workspace_map:
                        workspace_map[db_workspace] = []
                    workspace_map[db_workspace].append(db_name)

            if workspace_map:
                preview += "📁 **Workspace Overview**:\n"
                for ws_name, db_list in sorted(workspace_map.items()):
                    preview += f"   • **{ws_name}**: {', '.join(db_list)} ({len(db_list)} database{'s' if len(db_list) != 1 else ''})\n"
                preview += "\n**IMPORTANT**: When querying, always specify the workspace (e.g., 'trass.stories') or use the workspace parameter.\n\n"

        for db_name, db_workspace in databases:
            # Skip if already in loaded context
            if db_name in exclude_databases:
                continue

            # Skip if not in allowed list (permission filter)
            if limit_to_databases and db_name not in limit_to_databases:
                continue

            # Get count
            cursor.execute("""
                SELECT COUNT(*)
                FROM unified_content
                WHERE database_name = ? AND workspace = ?
            """, (db_name, db_workspace))
            count = cursor.fetchone()[0]

            # Get date range
            cursor.execute("""
                SELECT MIN(created_time), MAX(created_time)
                FROM unified_content
                WHERE database_name = ? AND workspace = ?
                AND created_time IS NOT NULL
            """, (db_name, db_workspace))
            date_range = cursor.fetchone()
            date_min = date_range[0] if date_range[0] else "unknown"
            date_max = date_range[1] if date_range[1] else "unknown"

            # Format dates
            if date_min != "unknown":
                try:
                    date_min = datetime.fromisoformat(date_min.replace('Z', '+00:00')).strftime("%Y-%m-%d")
                except:
                    date_min = "unknown"
            if date_max != "unknown":
                try:
                    date_max = datetime.fromisoformat(date_max.replace('Z', '+00:00')).strftime("%Y-%m-%d")
                except:
                    date_max = "unknown"

            # Get most recent samples (configurable)
            cursor.execute("""
                SELECT page_id, title, created_time, metadata
                FROM unified_content
                WHERE database_name = ? AND workspace = ?
                ORDER BY created_time DESC
                LIMIT ?
            """, (db_name, db_workspace, max_examples))
            samples = cursor.fetchall()

            # Determine emoji based on database type
            if 'gmail' in db_name.lower():
                emoji = "📧"
            elif 'discord' in db_name.lower() or db_name.endswith('.ds'):
                emoji = "💬"
            elif 'journal' in db_name.lower():
                emoji = "📓"
            elif 'stories' in db_name.lower() or 'cms' in db_name.lower():
                emoji = "📝"
            else:
                emoji = "📁"

            # Build database section with workspace prefix
            preview += f"{emoji} **{db_workspace}.{db_name}** ({count:,} entries | workspace: {db_workspace}"
            if date_min != "unknown" and date_max != "unknown":
                preview += f" | {date_min} to {date_max}"
            preview += ")\n"

            # Add sample entries
            if samples:
                preview += "\n  Recent examples:\n"
                for i, (page_id, title, created, metadata_str) in enumerate(samples, 1):
                    # Truncate title
                    title_display = title[:80] + "..." if title and len(title) > 80 else (title or "Untitled")

                    # Format date
                    date_display = "unknown"
                    if created:
                        try:
                            date_display = datetime.fromisoformat(created.replace('Z', '+00:00')).strftime("%b %d, %Y")
                        except:
                            pass

                    preview += f"  {i}. \"{title_display}\" ({date_display})\n"

                    # Parse metadata for key properties
                    if metadata_str:
                        try:
                            metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                            properties = metadata.get('properties', {})

                            # Show top 3 interesting properties
                            prop_display = []
                            interesting_props = ['status', 'sender', 'author', 'tags', 'epic', 'channel', 'mood']

                            for prop_name in interesting_props:
                                if prop_name in properties:
                                    prop_value = properties[prop_name]
                                    # Handle different property value formats
                                    if isinstance(prop_value, dict):
                                        # Notion property format
                                        if 'select' in prop_value and prop_value['select']:
                                            prop_display.append(f"{prop_name}: {prop_value['select']['name']}")
                                        elif 'multi_select' in prop_value and prop_value['multi_select']:
                                            tags = [t['name'] for t in prop_value['multi_select'][:2]]
                                            prop_display.append(f"{prop_name}: {', '.join(tags)}")
                                    elif isinstance(prop_value, str) and prop_value:
                                        prop_display.append(f"{prop_name}: {prop_value}")

                                if len(prop_display) >= 3:
                                    break

                            if prop_display:
                                preview += f"     {' | '.join(prop_display)}\n"

                        except:
                            pass  # Skip metadata parsing errors

                preview += "\n"
            else:
                preview += "  (No recent entries)\n\n"

        conn.close()

        if not preview.strip().endswith("## Available Data Sources"):
            return preview
        else:
            return ""  # No databases found

    except Exception as e:
        logger.debug(f"Database preview unavailable: {e}")
        return ""


def format_query_tools_for_prompt() -> str:
    """
    Format built-in query tools for inclusion in system prompt.

    These tools allow the AI to query and load additional context as needed.

    Returns:
        Formatted query tools information
    """
    tools_section = """<!-- QUERY_TOOLS_START -->
## Built-in Query Tools

You have access to built-in tools that allow you to query and load additional context from the user's databases. Use these tools when you need more information to answer the user's question effectively.

**CRITICAL - Tool Format Distinction**:
- **MCP Tools** (external servers) use: `<tool_code>server.tool(param="value")</tool_code>`
- **Built-in Query Tools** (query_sql, query_vector, query_source) use XML format: `<tool_call><tool_name>...</tool_name><parameters>...</parameters></tool_call>`

DO NOT confuse these two formats! Built-in query tools MUST use the `<tool_call>` XML format shown below.

**Making Multiple Queries in One Response**:
You can include MULTIPLE `<tool_call>` blocks in a SINGLE response when:
- You need information from different databases or time periods
- You want to try multiple search strategies simultaneously (casting a wide net)
- You're uncertain which query type will work best

**When to use multiple queries**:
✅ Good: Searching for a person who might be in emails OR messages (gmail + discord)
✅ Good: Trying SQL for exact name AND vector for fuzzy match (hedging your strategy)
✅ Good: Searching across different time periods or workspaces simultaneously
❌ Bad: Running SQL AND vector for the SAME abstract question where SQL won't work (wasteful)
❌ Bad: Making queries "just to be safe" when one strategy is clearly sufficient

**Benefits of multiple queries**:
- Faster: All queries execute in parallel instead of separate iterations
- More efficient: User approves all at once instead of multiple interruptions
- Better results: Can combine complementary search strategies

**Important**: Multiple queries should be COMPLEMENTARY (different strategies, databases, or time periods), not redundant. Don't run SQL if you know it won't find anything useful.

Example - Multiple queries in one response:
```
I'll search for Eddie's emails using multiple strategies:

<tool_call>
  <tool_name>query_sql</tool_name>
  <parameters>
    <query>trass gmail from eddie last 7 days</query>
    <reasoning>Searching for exact sender name "eddie" in email metadata...</reasoning>
  </parameters>
</tool_call>

<tool_call>
  <tool_name>query_vector</tool_name>
  <parameters>
    <query>eddie email communication messages</query>
    <reasoning>Semantic search in case name is spelled differently (Eddie vs Edward) or in content...</reasoning>
    <top_k>100</top_k>
    <min_similarity>0.15</min_similarity>
  </parameters>
</tool_call>

<tool_call>
  <tool_name>query_source</tool_name>
  <parameters>
    <source>gmail:14</source>
    <reasoning>Loading recent emails to scan through if specific searches fail...</reasoning>
  </parameters>
</tool_call>
```

### Available Query Tools

**Quick Selection Guide**:
- Filtering by properties (status, priority, assignee, dates) → Use **query_vector**
- User says "I think", "something like", "might be" → Use **query_vector**
- Searching for exact text/keywords in content → Use **query_sql**
- Shopify data (orders, revenue, products, inventory, fulfillment) → Use **query_sql**
- Know exact database + time range needed → Use **query_source**

**⚠️ Workspace Awareness**:
- ALWAYS specify the workspace when querying (e.g., workspace="trass" parameter OR "trass gmail" in query text)
- Check the "Workspace Overview" in the data sources section to see which databases belong to which workspaces
- If user mentions "trass", search trass.* databases, NOT default.* or koii.* databases
- Example: User asks about "trass stories" → Use workspace="trass" or include "trass stories" in query text

**🔍 Property vs Content Search - CRITICAL DISTINCTION**:

**Database Properties** (use query_vector):
- Status (in-progress, done, blocked, etc.)
- Priority (P0, P1, P2, P3)
- Assignee (who owns it)
- Tags, labels, categories
- Dates (created, updated, due date)
- Sender, recipient (for emails)
- Channel (for Discord/Telegram)

**Text Content** (use query_sql):
- Words/phrases in email body
- Text in journal entries
- Story descriptions and comments
- Any text you want to search for with exact keywords

**Rule of Thumb**: If you're asking "show me items WHERE [property] = [value]", use **query_vector**. If you're asking "show me items that MENTION [keyword]", use **query_sql**.

#### 1. query_sql
**Description**: Search for **EXACT TEXT/KEYWORDS** in content using natural language that converts to SQL. This searches the actual text content, NOT database properties.

**When to use**:
- Searching for SPECIFIC, CONCRETE keywords that appear in content (names like "Federico", "Product X", specific phrases)
- Finding messages FROM a specific person by name (e.g., "emails from Federico")
- Finding content that MENTIONS specific topics with concrete terms (e.g., "mentions quarterly results")
- **Shopify store data**: orders, revenue, sales performance, product catalog, inventory levels, fulfillment status. Query text should mention "shopify" plus what you need (e.g., "shopify orders today total revenue", "shopify products low inventory")
- **Important**: SQL works best with SPECIFIC, SEARCHABLE terms like names, products, projects, not abstract concepts

**When NOT to use**:
- ❌ **Abstract questions** like "who am I?", "what's my purpose?", "identity", "values" → Use **query_vector** for philosophical/abstract queries
- ❌ **Filtering by database properties** like status, priority, tags, assignee → Use **query_vector** instead
- ❌ **Fuzzy/uncertain searches** → Use **query_vector** instead
- ❌ **Concepts without specific keywords** → If you can't identify 2-3 concrete keywords that would appear in text, don't use SQL
- Example: "stories with status in-progress" → This is a PROPERTY filter, use query_vector NOT query_sql
- Example: "who am I?" → This is abstract/philosophical, use query_vector NOT query_sql (SQL would search for literal phrase "who am I" which won't exist)

**SQL Success Check**: Before using query_sql, ask yourself: "What specific words/phrases will appear in the text?" If you can't identify concrete keywords, use query_vector instead.

**Parameters**:
- `query`* (string): Natural language description specifying workspace, database, and search terms that appear in content. Format: "{workspace} {database} from/about {text_keywords} {time_filter}". Examples: "trass gmail from federico about launch last 30 days", "trass journal mentioning meeting last month"
- `reasoning`* (string): **REQUIRED** - Explain: (1) Why you need this information (what's missing from current context), (2) What you expect to find, (3) Why you formulated the query this way
- `workspace` (string): Optional workspace name to search in (defaults to current workspace if not in query)
- `max_results` (integer): Optional maximum number of results to return (default: 50)

**Example**:
```
<tool_call>
  <tool_name>query_sql</tool_name>
  <parameters>
    <query>trass gmail from federico about launch last 30 days</query>
    <reasoning>User asked "What did Federico say about the product launch?" My current context doesn't contain any emails from Federico. I'm querying the gmail database because Federico communicates via email. I'm searching for messages where Federico is the sender AND the content mentions "product launch" or "launch" from the last 30 days. I expect to find 5-15 recent emails with his feedback, concerns, and updates about the launch timeline and strategy.</reasoning>
    <max_results>20</max_results>
  </parameters>
</tool_call>
```

#### 2. query_vector
**Description**: Search databases using semantic similarity AND filter by database properties. This is the MOST VERSATILE tool - it handles both semantic search and property filtering.

**When to use**:
- **Filtering by database properties** (status, priority, assignee, tags, dates) - THIS IS THE KEY USE CASE
- **Uncertain or fuzzy names/titles** (user says "I think", "something like", "might be called")
- Finding content by semantic meaning rather than exact keywords
- When different wording might be used for the same concept
- Searching for themes, topics, or concepts across content

**Examples of property filtering (USE QUERY_VECTOR FOR THESE)**:
- "stories with status in-progress" → query_vector can filter by status property
- "P3 priority tasks updated last week" → query_vector handles both priority filter and date filter
- "blocked stories" → query_vector filters by status property
- "emails from federico" → query_vector can filter by sender property

**Parameters**:
- `query`* (string): Text to search for semantically similar content
- `reasoning`* (string): **REQUIRED** - Explain: (1) Why you need this information (what's missing from current context), (2) What you expect to find, (3) Why semantic search is appropriate for this query
- `workspace` (string): Optional workspace name to search in (defaults to current workspace)
- `top_k` (integer): Maximum number of results to return (default: 60, range: 50-300). **Choose strategically based on search type**:
  - **Fuzzy/uncertain searches** (user says "I think", "might be"): Use 100-300 to cast a very wide net
  - **Broad topic searches** (themes, concepts across content): Use 60-100
  - **Targeted searches** (specific known items): Use 50-80
  - **Refinement queries** (narrowing previous results): Use 30-50
- `min_similarity` (float): Minimum similarity threshold 0.0-1.0 (default: 0.2). **For fuzzy title searches like "I think it's in X", use 0.15-0.2 to cast a very wide net. For targeted searches, use 0.4-0.6 for higher precision**

**Examples**:

**Example 1: Property Filtering (MOST COMMON USE CASE)**
```
<tool_call>
  <tool_name>query_vector</tool_name>
  <parameters>
    <query>trass stories status in-progress blocked priority P3 P2</query>
    <reasoning>User asked what's most important for trass right now. I need to find high-priority stories that are actively being worked on or blocked. Using query_vector because I'm filtering by DATABASE PROPERTIES: status (in-progress, blocked) and priority (P3, P2). The vector system can filter by these structured properties. I expect to find 10-30 active high-priority stories representing current focus areas.</reasoning>
    <workspace>trass</workspace>
    <top_k>50</top_k>
  </parameters>
</tool_call>
```

**Example 2: Fuzzy Title Search**
```
<tool_call>
  <tool_name>query_vector</tool_name>
  <parameters>
    <query>technical assets promo code dashboard</query>
    <reasoning>User said "I think it's in the technical assets trass story" - the phrase "I think" indicates uncertainty about the exact title. Using semantic search with very low threshold (0.2) and large top_k (60) to cast a very wide net and find stories with similar titles like "Technical Assets", "Tech Assets", "Technical Resources", even if wording differs significantly. I expect to find the story containing dashboard links among the results.</reasoning>
    <top_k>60</top_k>
    <min_similarity>0.2</min_similarity>
  </parameters>
</tool_call>
```

**Example 3: Semantic Search**
```
<tool_call>
  <tool_name>query_vector</tool_name>
  <parameters>
    <query>international expansion strategy</query>
    <reasoning>User asked about our plans for international markets. I'm using semantic search because the relevant content might use different terminology like "global growth", "overseas markets", "foreign markets", etc. I expect to find 10-20 documents discussing market expansion and geographic strategy.</reasoning>
    <top_k>15</top_k>
  </parameters>
</tool_call>
```

#### 3. query_source
**Description**: Load content directly from specific databases and time ranges. Most direct way to add context.

**When to use**: When you know exactly which database and time range you need (e.g., "last 7 days of Gmail", "30 days of journal entries")

**Parameters**:
- `source`* (string): Database specification in format "database_name:days" (e.g., "gmail:7", "journal:30")
- `reasoning`* (string): **REQUIRED** - Explain: (1) Why you need this specific database (what information is it expected to contain), (2) Why you chose this time range, (3) What you expect to find
- `workspace` (string): Optional workspace name (defaults to current workspace)
- `filters` (object): Optional property filters to apply

**Example**:
```
<tool_call>
  <tool_name>query_source</tool_name>
  <parameters>
    <source>gmail:7</source>
    <reasoning>User asked "Any important emails this week?" My context doesn't have recent email data. I'm loading the last 7 days of gmail because the user specifically said "this week" and emails are the communication channel where important updates come through. I expect to find 20-50 recent emails including project updates, meeting invitations, and urgent requests that need the user's attention.</reasoning>
  </parameters>
</tool_call>
```

### Important Notes About Query Tools

1. **Reasoning is REQUIRED**: Every query tool call MUST include a `reasoning` parameter that explains:
   - WHY you need this information (what's missing from your current context)
   - WHAT you expect to find
   - HOW/WHY you formulated the query this way

   The reasoning will be shown to the user during approval, so be specific and clear.

2. **Permission Required**: When you use query tools, they execute in parallel first, then the user reviews results and decides whether to load each into context. They can approve (Enter), skip (s), modify (m), or decline (n) each query individually. Your reasoning helps them make this decision.

3. **Context Updates**: After queries execute and are approved, the results are merged into your context. You'll receive a summary of what was loaded.

4. **Iterative Querying**: You can make multiple query tool calls **in a SINGLE response OR across multiple iterations**. **Use a multi-strategy approach**:
   - **Preferred**: Include 2-3 query tool calls in your INITIAL response when you want to cast a wide net (they execute in parallel)
   - **Alternative**: Start with one query, see results, then make follow-up queries if needed (iterative approach)
   - **When uncertain**: Use multiple query types simultaneously (query_sql + query_vector + query_source) to maximize chance of finding data
   - **If 0 results from all queries**: Try different keywords, broader time ranges, or different databases in next iteration
   - Each query requires user approval and uses tokens, but casting a wide net initially is more efficient than sequential attempts

5. **Deduplication**: If a query returns content already in context, it will be deduplicated automatically. You won't see duplicate entries.

6. **When to Query**:
   - User asks about something not in your current context
   - User's question would benefit from more recent or more specific information
   - You notice you're missing key information to give a complete answer

7. **When NOT to Query**:
   - The answer is already in your context
   - The question doesn't require database content (general knowledge questions)
   - You're unsure what to query for (ask the user to clarify first)

### Query Tool Usage Pattern

The recommended pattern for using query tools is:

1. **Assess**: Determine if you have sufficient context to answer the user's question
2. **Decide**: If not, determine what specific information you need
3. **Query**: Use the appropriate query tool to request that information
4. **Wait**: Wait for the tool result and updated context
5. **Reassess**: Evaluate your situation with these criteria:
   - **Do I have sufficient information to answer completely?** → If yes, proceed to Answer
   - **Are there more places/strategies left to try?** → If yes and results were 0/irrelevant, try different strategy
   - **Is this data likely to exist in the system?** → If yes and I haven't tried 3+ strategies, keep searching
   - **Have I tried 3-4 different approaches?** → If yes and still insufficient, explain what's missing and why not found
   - **Is the user's question unclear or ambiguous?** → Ask for clarification rather than guessing
6. **Answer**: Once you have sufficient context, provide your answer

**Example conversation flow**:
```
User: "What did Federico say about the product launch?"

AI: I need to search for emails from Federico about the product launch.

<tool_call>
  <tool_name>query_sql</tool_name>
  <parameters>
    <query>emails from Federico about product launch</query>
    <reasoning>User asked what Federico said about the product launch. My current context doesn't contain any emails from Federico. I'm querying the gmail database because that's where Federico's communications would be stored. I'm searching for messages where Federico is the sender and the content mentions "product launch" or "launch". I expect to find several emails with his feedback, questions, or updates about the launch.</reasoning>
  </parameters>
</tool_call>

[User sees reasoning, approves, context is loaded]

AI: Based on the emails I found, Federico mentioned...
```

### Search Strategy Decision Tree

When a query doesn't return sufficient results, use this decision tree to determine your next strategy:

**Scenario 1: 0 results returned**
- ✅ **Try**: Increase top_k to 200-300, lower min_similarity to 0.15
- ✅ **Try**: Rephrase query with synonyms or broader terms
- ✅ **Try**: Switch from query_vector to query_sql with exact terms user provided
- ✅ **Try**: query_source to load entire database for recent time period
- ❌ **If still 0 after 3 strategies**: Explain to user that data doesn't appear to exist in system

**Scenario 2: Results returned but seem irrelevant**
- ✅ **Try**: Different phrasing focusing on key terms
- ✅ **Try**: query_sql with exact property filters
- ✅ **Try**: Different database (e.g., from "stories" to "journal" or "gmail")
- ❌ **If still irrelevant after 3 strategies**: Explain what you found and why it doesn't match

**Scenario 3: Partial information found**
- ✅ **Try**: Follow-up query targeting specific gaps (e.g., if found item but missing details)
- ✅ **Try**: Broader time range (e.g., from "last 7 days" to "last 30 days")
- ✅ **Try**: Related databases that might have complementary info
- ✅ **Continue**: Until you have complete answer or exhausted strategies

**Scenario 4: Sufficient information found**
- ❌ **Don't query again** - proceed directly to answering the user's question
- ✅ **Present**: Clear, complete answer based on the context you loaded

**Remember**: The system allows up to 8 query iterations. Use them wisely, but don't give up after just 1 attempt if the data likely exists. Try 3-4 different strategies before concluding the information isn't available.
<!-- QUERY_TOOLS_END -->"""

    return tools_section