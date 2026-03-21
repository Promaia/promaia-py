"""
Setup and creation of agent structures in Notion.

This module handles:
- Auto-creating Agents database at workspace root
- Creating agent pages with substructure (System Prompt, Instructions, Journal)
- Converting markdown prompts to Notion blocks
"""

import asyncio
import logging
import webbrowser
from typing import Optional, List, Dict, Any
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


async def ensure_agents_database_exists(workspace: str) -> str:
    """
    Ensure Agents database exists in workspace, create if not.

    Args:
        workspace: Workspace name

    Returns:
        Database ID of Agents database
    """
    from promaia.config.workspaces import get_workspace_manager
    from promaia.notion.client import get_client

    workspace_mgr = get_workspace_manager()
    workspace_config = workspace_mgr.get_workspace(workspace)

    # Check if already exists
    if workspace_config and workspace_config.agents_database_id:
        return workspace_config.agents_database_id

    # Need to set up - use template duplication method
    console.print("\n📊 First-time setup: Agents Database", style="bold cyan")
    console.print("   (This only happens once per workspace)", style="dim")
    console.print()

    client = get_client(workspace)

    try:
        # Step 1: Provide template link
        template_url = "https://koii.notion.site/Promaia-Agents-Template-2f2d1339696780eaaf59f2311c8cf9a8"

        console.print("📋 Setup Instructions:", style="bold white")
        console.print()
        console.print("1. Open this Notion template in your browser:", style="white")
        console.print(f"   {template_url}", style="cyan")
        console.print()
        console.print("2. Click 'Duplicate' in the top right corner", style="white")
        console.print()
        console.print("3. Choose where to save it in your workspace", style="white")
        console.print()
        console.print("4. Share the duplicated page with your Notion integration", style="white")
        console.print("   (Click '...' menu → Add connections → Select your integration)", style="dim")
        console.print()
        console.print("5. Copy the URL of the duplicated page", style="white")
        console.print()

        # Open template in browser
        console.print("Opening template in browser...", style="dim")
        webbrowser.open(template_url)
        console.print()

        # Step 2: Ask for duplicated page URL
        duplicated_page_input = input("Paste the URL of your duplicated 'Agents' page: ").strip()

        if not duplicated_page_input:
            console.print("\n❌ Page URL is required", style="red")
            raise ValueError("Agents page URL is required")

        # Extract page ID from URL
        page_id = _extract_page_id_from_url(duplicated_page_input)
        console.print(f"   Page ID: {page_id}", style="dim")

        # Step 3: Find the Agents database inside the page
        console.print("\n⏳ Finding Agents database in page...", style="cyan")

        # Get all blocks in the page
        blocks = await client.blocks.children.list(block_id=page_id)

        agents_db_id = None
        for block in blocks.get("results", []):
            if block.get("type") == "child_database":
                # Found a database - check if it's titled "Agents"
                db_id = block["id"]
                db_info = await client.databases.retrieve(database_id=db_id)

                db_title = ""
                if db_info.get("title"):
                    db_title = "".join([t.get("plain_text", "") for t in db_info["title"]])

                if "agents" in db_title.lower():
                    agents_db_id = db_id
                    console.print(f"   ✓ Found Agents database: {db_title}", style="dim")
                    break

        if not agents_db_id:
            console.print("\n❌ Could not find 'Agents' database in the page", style="red")
            console.print("   Make sure you duplicated the template correctly", style="dim")
            raise ValueError("Agents database not found in page")

        # Step 4: Save to workspace config
        workspace_config.agents_database_id = agents_db_id
        workspace_config.agents_page_id = page_id  # Save parent page ID for linking
        workspace_mgr.save_config()

        console.print(f"\n✅ Agents database configured!", style="green")
        console.print(f"   You can now create agents!", style="dim")

        return agents_db_id

    except Exception as e:
        logger.error(f"Failed to set up Agents database: {e}")
        console.print(f"\n❌ Error setting up Agents database: {e}", style="red")
        raise


def _extract_page_id_from_url(url: str) -> str:
    """
    Extract page ID from a Notion URL.

    Examples:
        https://notion.so/Page-Name-abc123 -> abc123
        https://www.notion.so/workspace/Page-abc123?v=... -> abc123
    """
    # Handle full URL
    if "notion.so/" in url or "notion.site/" in url:
        # Extract the path part after domain
        url_path = url.split("notion.so/")[-1] if "notion.so/" in url else url.split("notion.site/")[-1]

        # Remove query params and hash
        url_path = url_path.split("?")[0].split("#")[0]

        # The page ID is the last segment, possibly after dashes
        # Format: Page-Name-ID or just ID
        if "-" in url_path:
            # Split by dash and take last part (the UUID)
            segments = url_path.split("-")
            return segments[-1]
        else:
            # Just the ID
            return url_path.split("/")[-1]
    else:
        # Already just an ID, clean it up
        return url.split("?")[0].split("#")[0].replace("/", "").strip()


async def create_agent_in_notion(agent_config, workspace: str) -> str:
    """
    Create complete agent page structure in Notion.

    Creates:
    - Agent page in Agents database
    - System Prompt subpage
    - Instructions sub-database
    - Journal sub-database

    Args:
        agent_config: AgentConfig object
        workspace: Workspace name

    Returns:
        Agent page ID
    """
    from promaia.notion.client import get_client

    agents_db_id = await ensure_agents_database_exists(workspace)
    client = get_client(workspace)

    console.print(f"\n⏳ Creating agent structure in Notion...", style="cyan")

    try:
        # 1. Create agent PAGE in Agents database
        agent_page = await client.pages.create(
            parent={"database_id": agents_db_id},
            properties={
                "Name": {"title": [{"text": {"content": agent_config.name}}]},
                "Agent ID": {"rich_text": [{"text": {"content": agent_config.agent_id}}]},
                "Status": {"select": {"name": "Active"}}
            }
        )

        agent_page_id = agent_page["id"]
        console.print(f"   ✓ Created agent page", style="dim")

        # 2. Create "System Prompt" subpage
        system_prompt_page = await client.pages.create(
            parent={"page_id": agent_page_id},
            properties={
                "title": {"title": [{"text": {"content": "System Prompt"}}]}
            }
        )

        # Add prompt content as blocks
        prompt_blocks = markdown_to_notion_blocks(agent_config.prompt_file)
        await client.blocks.children.append(
            block_id=system_prompt_page["id"],
            children=prompt_blocks[:100]  # Notion limit: 100 blocks per request
        )
        console.print(f"   ✓ Added system prompt", style="dim")

        # 3. Create "Instructions" sub-database
        instructions_db = await client.databases.create(
            parent={"page_id": agent_page_id},
            title=[{"text": {"content": "Instructions"}}],
            properties={
                "Name": {"title": {}},
                "Category": {
                    "select": {
                        "options": [
                            {"name": "Procedure", "color": "blue"},
                            {"name": "Template", "color": "green"},
                            {"name": "Reference", "color": "gray"}
                        ]
                    }
                },
                "Content": {"rich_text": {}}
            }
        )
        console.print(f"   ✓ Created Instructions database", style="dim")

        # 4. Create "Journal" sub-database
        journal_db = await client.databases.create(
            parent={"page_id": agent_page_id},
            title=[{"text": {"content": "Journal"}}],
            properties={
                "Entry": {"title": {}},  # Required: Every database must have a title property
                "Date": {"date": {}},
                "Type": {
                    "select": {
                        "options": [
                            {"name": "Execution", "color": "blue"},
                            {"name": "Note", "color": "green"},
                            {"name": "Error", "color": "red"}
                        ]
                    }
                },
                "Content": {"rich_text": {}},
                "Execution ID": {"number": {}}
            }
        )
        console.print(f"   ✓ Created Journal database", style="dim")

        # Store IDs in agent config
        agent_config.system_prompt_page_id = system_prompt_page["id"]
        agent_config.instructions_db_id = instructions_db["id"]
        agent_config.journal_db_id = journal_db["id"]

        console.print(f"\n✅ Agent structure created in Notion!", style="green")
        console.print(f"   Opening agent page in browser...", style="dim")

        return agent_page_id

    except Exception as e:
        logger.error(f"Failed to create agent structure: {e}")
        console.print(f"\n❌ Error creating agent structure: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise


def markdown_to_notion_blocks(markdown: str) -> List[Dict[str, Any]]:
    """
    Convert markdown to Notion block format.

    Args:
        markdown: Markdown content

    Returns:
        List of Notion block objects
    """
    blocks = []
    lines = markdown.split('\n')

    for line in lines:
        line = line.rstrip()

        if not line:
            # Skip empty lines
            continue

        # Headings
        if line.startswith('### '):
            blocks.append({
                "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": line[4:]}}]}
            })
        elif line.startswith('## '):
            blocks.append({
                "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": line[3:]}}]}
            })
        elif line.startswith('# '):
            blocks.append({
                "type": "heading_1",
                "heading_1": {"rich_text": [{"text": {"content": line[2:]}}]}
            })
        # Lists
        elif line.startswith('- ') or line.startswith('* '):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"text": {"content": line[2:]}}]}
            })
        elif line.strip() and line.lstrip()[0].isdigit() and '. ' in line:
            # Numbered list
            content = line.split('. ', 1)[1] if '. ' in line else line
            blocks.append({
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": [{"text": {"content": content}}]}
            })
        # Regular paragraph
        else:
            # Truncate to Notion's limit (2000 chars per rich_text)
            content = line[:2000] if len(line) > 2000 else line
            blocks.append({
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": content}}]}
            })

    return blocks


async def setup_promaia_page(workspace: str) -> tuple[str, str]:
    """
    Set up Promaia page structure for a workspace.

    This discovers the Main prompt page and other essential pages from the
    duplicated Promaia template.

    Args:
        workspace: Workspace name

    Returns:
        Tuple of (promaia_page_id, main_prompt_page_id)
    """
    from promaia.config.workspaces import get_workspace_manager
    from promaia.notion.client import get_client

    workspace_mgr = get_workspace_manager()
    workspace_config = workspace_mgr.get_workspace(workspace)

    # Check if already configured
    if workspace_config and workspace_config.promaia_page_id and workspace_config.main_prompt_page_id:
        console.print(f"\n✓ Promaia page already configured for workspace '{workspace}'", style="green")
        return workspace_config.promaia_page_id, workspace_config.main_prompt_page_id

    console.print("\n📄 First-time setup: Promaia Page", style="bold cyan")
    console.print("   This sets up your main prompt and other Promaia resources", style="dim")
    console.print()

    client = get_client(workspace)

    try:
        # Step 1: Provide template link
        template_url = "https://www.notion.so/koii/Promaia-2f2d133969678183b4b4c6d6931168f5"

        console.print("📋 Setup Instructions:", style="bold white")
        console.print()
        console.print("1. Open this Notion Promaia template in your browser:", style="white")
        console.print(f"   {template_url}", style="cyan")
        console.print()
        console.print("2. Click 'Duplicate' in the top right corner", style="white")
        console.print()
        console.print("3. Choose where to save it in your workspace", style="white")
        console.print()
        console.print("4. Share the duplicated page with your Notion integration", style="white")
        console.print("   (Click '...' menu → Add connections → Select your integration)", style="dim")
        console.print()
        console.print("5. Copy the URL of the duplicated Promaia page", style="white")
        console.print()

        # Open template in browser
        console.print("Opening template in browser...", style="dim")
        webbrowser.open(template_url)
        console.print()

        # Step 2: Ask for duplicated page URL
        duplicated_page_input = input("Paste the URL of your duplicated 'Promaia' page: ").strip()

        if not duplicated_page_input:
            console.print("\n❌ Page URL is required", style="red")
            raise ValueError("Promaia page URL is required")

        # Extract page ID from URL
        promaia_page_id = _extract_page_id_from_url(duplicated_page_input)
        console.print(f"   Page ID: {promaia_page_id}", style="dim")

        # Step 3: Discover child pages (especially "Main prompt")
        console.print("\n⏳ Discovering child pages...", style="cyan")

        # Get all blocks in the Promaia page
        blocks = await client.blocks.children.list(block_id=promaia_page_id)

        main_prompt_page_id = None
        discovered_pages = []

        for block in blocks.get("results", []):
            block_type = block.get("type")

            # Look for child_page blocks (subpages)
            if block_type == "child_page":
                page_title = block.get("child_page", {}).get("title", "Untitled")
                page_id = block["id"]
                discovered_pages.append((page_title, page_id))

                # Check if this is the Main prompt page
                if "main prompt" in page_title.lower():
                    main_prompt_page_id = page_id
                    console.print(f"   ✓ Found: {page_title}", style="green")
                else:
                    console.print(f"   ✓ Found: {page_title}", style="dim")

            # Also check for linked pages in other block types
            elif block_type == "link_to_page":
                page_id = block.get("link_to_page", {}).get("page_id")
                if page_id:
                    try:
                        # Fetch page details to get title
                        page_info = await client.pages.retrieve(page_id=page_id)
                        page_title = ""
                        if page_info.get("properties", {}).get("title"):
                            title_prop = page_info["properties"]["title"]
                            if title_prop.get("title"):
                                page_title = "".join([t.get("plain_text", "") for t in title_prop["title"]])

                        discovered_pages.append((page_title or "Untitled", page_id))

                        if "main prompt" in page_title.lower():
                            main_prompt_page_id = page_id
                            console.print(f"   ✓ Found (linked): {page_title}", style="green")
                        else:
                            console.print(f"   ✓ Found (linked): {page_title}", style="dim")
                    except Exception as e:
                        logger.debug(f"Could not fetch linked page details: {e}")

        if not main_prompt_page_id:
            console.print("\n❌ Could not find 'Main prompt' page", style="red")
            console.print("   Please ensure your Promaia page contains a 'Main prompt' subpage", style="dim")
            console.print(f"   Discovered pages: {', '.join([t for t, _ in discovered_pages])}", style="dim")
            raise ValueError("Main prompt page not found")

        # Step 4: Save to workspace config
        workspace_config.promaia_page_id = promaia_page_id
        workspace_config.main_prompt_page_id = main_prompt_page_id
        workspace_mgr.save_config()

        console.print(f"\n✅ Promaia page configured!", style="green")
        console.print(f"   Main prompt will now sync from Notion", style="dim")
        console.print(f"   Discovered {len(discovered_pages)} page(s) in your Promaia page", style="dim")

        return promaia_page_id, main_prompt_page_id

    except Exception as e:
        logger.error(f"Failed to set up Promaia page: {e}")
        console.print(f"\n❌ Error setting up Promaia page: {e}", style="red")
        raise


def generate_agent_id(name: str, existing_agents: List[Any]) -> str:
    """
    Generate a stable UUID-based agent ID.

    Agent IDs are random UUIDs so they never need to change when the
    agent's display name is updated. This keeps file paths, SQLite
    tables, and other derived identifiers stable across renames.

    Args:
        name: Agent name (unused, kept for backwards compatibility)
        existing_agents: List of existing AgentConfig objects (unused)

    Returns:
        Random UUID string (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    """
    import uuid
    return str(uuid.uuid4())
