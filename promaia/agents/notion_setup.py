"""
Setup and creation of Promaia structures in Notion.

This module handles:
- Creating the unified Promaia page (prompts DB, Agents DB, Main prompt)
- Discovering existing components from a partially-configured page
- Falling back to template duplication when API creation fails
- Creating agent pages with substructure (System Prompt, Instructions, Journal)
- Converting markdown prompts to Notion blocks
"""

import asyncio
import logging
import webbrowser
from typing import Optional, List, Dict, Any, Tuple
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


def _render_qr(url: str) -> None:
    """Render a QR code in the terminal in light grey. Silently skipped on failure."""
    try:
        import io
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf)
        text = buf.getvalue()
        for line in text.splitlines():
            if line.strip():
                console.print(f"    {line}", style="bright_black", highlight=False)
    except Exception:
        pass


async def _select_parent_page(
    pages: List[Tuple[str, str]],
) -> Optional[str]:
    """Interactive arrow-key selector for choosing a parent page.

    Args:
        pages: List of (title, page_id) tuples.

    Returns:
        Selected page_id, or None if cancelled.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout

    current_focus = 0
    confirmed = False

    def get_entry_display(index: int) -> str:
        title, _ = pages[index]
        display = title[:60] if len(title) > 60 else title
        indicator = "\u2192" if index == current_focus else " "
        return f" {indicator}  {display}"

    def get_status_display():
        return " \u2191\u2193 Navigate   ENTER Select   ESC Cancel"

    def create_layout():
        title_window = Window(
            FormattedTextControl(
                text=" Where should Promaia create its page?"
            ),
            height=1,
        )
        entry_windows = [
            Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
            )
            for i in range(len(pages))
        ]
        status_window = Window(
            FormattedTextControl(text=get_status_display),
            height=1,
            style="fg:gray",
        )
        return Layout(HSplit([
            title_window,
            Window(height=1),
            *entry_windows,
            Window(height=1),
            status_window,
        ]))

    layout = create_layout()
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(pages) - 1:
            current_focus += 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()

    if confirmed:
        title, page_id = pages[current_focus]
        console.print(f"  Selected: {title}", style="magenta")
        return page_id
    return None


async def _create_promaia_structure(
    client,
    parent_page_id: str,
) -> Dict[str, str]:
    """Create the complete Promaia page structure via Notion API.

    Creates (in order -- Notion renders children in creation order):
    1. "Promaia" page under parent with octopus icon
    2. Description paragraph
    3. "Prompts" database with Name title column
    4. "Main prompt" page inside prompts database
    5. "Agents" database with Name, Agent ID, Last Run, Status
    6. "Info" h3 heading + bullet list help text

    Args:
        client: Notion API client
        parent_page_id: ID of the page to create Promaia under

    Returns:
        Dict with promaia_page_id, main_prompt_page_id,
        prompts_database_id, agents_database_id, page_url
    """
    # 1. Create Promaia page
    console.print("   Creating Promaia page...", style="dim")
    promaia_page = await client.pages.create(
        parent={"page_id": parent_page_id},
        icon={"type": "emoji", "emoji": "\U0001F419"},
        properties={
            "title": {"title": [{"text": {"content": "Promaia"}}]}
        },
    )
    promaia_page_id = promaia_page["id"]

    # 2. Append description paragraph
    await client.blocks.children.append(
        block_id=promaia_page_id,
        children=[
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "text": {
                                "content": (
                                    "This database contains all your Maia agents. "
                                    "Each agent can be scheduled to run automatically "
                                    "or triggered via calendar events."
                                )
                            }
                        }
                    ]
                },
            },
        ],
    )

    # 3. Create "Prompts" database (inline so it renders on the page)
    console.print("   Creating Prompts database...", style="dim")
    prompts_db = await client.databases.create(
        parent={"page_id": promaia_page_id},
        is_inline=True,
        title=[{"text": {"content": "Prompts"}}],
        properties={
            "Name": {"title": {}},
        },
    )
    prompts_db_id = prompts_db["id"]

    # 4. Create "Main prompt" page inside prompts database
    console.print("   Creating Main prompt page...", style="dim")
    main_prompt_page = await client.pages.create(
        parent={"database_id": prompts_db_id},
        properties={
            "Name": {"title": [{"text": {"content": "Main prompt"}}]}
        },
    )
    main_prompt_page_id = main_prompt_page["id"]

    # 5. Create "Agents" database (inline so it renders on the page)
    console.print("   Creating Agents database...", style="dim")
    agents_db = await client.databases.create(
        parent={"page_id": promaia_page_id},
        is_inline=True,
        title=[{"text": {"content": "Agents"}}],
        properties={
            "Name": {"title": {}},
            "Agent ID": {"rich_text": {}},
            "Last Run": {"date": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Active", "color": "green"},
                        {"name": "Inactive", "color": "gray"},
                        {"name": "Archived", "color": "red"},
                    ]
                }
            },
        },
    )
    agents_db_id = agents_db["id"]

    # 6. Append "Info" heading + bullet list
    await client.blocks.children.append(
        block_id=promaia_page_id,
        children=[
            {
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"text": {"content": "Info"}}]
                },
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Edit the Main prompt page above to customize your agent's personality"
                            }
                        }
                    ]
                },
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Add agents with: maia agent add"
                            }
                        }
                    ]
                },
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Schedule agents with: maia agent schedule <name>"
                            }
                        }
                    ]
                },
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Run an agent manually: maia agent run <name>"
                            }
                        }
                    ]
                },
            },
        ],
    )

    return {
        "promaia_page_id": promaia_page_id,
        "main_prompt_page_id": main_prompt_page_id,
        "prompts_database_id": prompts_db_id,
        "agents_database_id": agents_db_id,
        "page_url": promaia_page.get("url", ""),
    }


async def _discover_promaia_components(
    client,
    promaia_page_id: str,
) -> Dict[str, Optional[str]]:
    """Inspect children of a Promaia page to find existing components.

    Handles both API-created and template-duplicated pages by checking
    child_database blocks (for inline DBs) and child_page blocks (for
    legacy template "Main prompt" subpages).

    Args:
        client: Notion API client
        promaia_page_id: ID of the Promaia page to inspect

    Returns:
        Dict with discovered IDs (None for anything not found):
        prompts_database_id, agents_database_id, main_prompt_page_id
    """
    blocks = await client.blocks.children.list(block_id=promaia_page_id)

    prompts_db_id = None
    agents_db_id = None
    main_prompt_page_id = None

    for block in blocks.get("results", []):
        block_type = block.get("type")

        if block_type == "child_database":
            db_id = block["id"]
            try:
                db_info = await client.databases.retrieve(database_id=db_id)
                db_title = "".join(
                    t.get("plain_text", "") for t in db_info.get("title", [])
                )

                if "prompts" in db_title.lower() and not prompts_db_id:
                    prompts_db_id = db_id
                    console.print(f"   Found database: {db_title}", style="dim")
                elif "agents" in db_title.lower() and not agents_db_id:
                    agents_db_id = db_id
                    console.print(f"   Found database: {db_title}", style="dim")
            except Exception as e:
                logger.debug("Could not retrieve database %s: %s", db_id, e)

        elif block_type == "child_page":
            page_title = block.get("child_page", {}).get("title", "")
            if "main prompt" in page_title.lower() and not main_prompt_page_id:
                main_prompt_page_id = block["id"]
                console.print(f"   Found page: {page_title}", style="dim")

    # If prompts DB found, look for Main prompt page inside it
    if prompts_db_id and not main_prompt_page_id:
        try:
            query = await client.databases.query(
                database_id=prompts_db_id,
                filter={"property": "Name", "title": {"equals": "Main prompt"}},
            )
            results = query.get("results", [])
            if results:
                main_prompt_page_id = results[0]["id"]
                console.print("   Found Main prompt in prompts database", style="dim")
        except Exception as e:
            logger.debug("Could not query prompts database: %s", e)

    return {
        "prompts_database_id": prompts_db_id,
        "agents_database_id": agents_db_id,
        "main_prompt_page_id": main_prompt_page_id,
    }


async def _template_fallback_method(workspace: str) -> Dict[str, str]:
    """Unified template-duplication fallback for Promaia page setup.

    Shows QR code + link for the full Promaia template, asks user to
    duplicate it, then discovers all components from the duplicated page.

    Args:
        workspace: Workspace name

    Returns:
        Dict with promaia_page_id, main_prompt_page_id,
        agents_database_id, prompts_database_id
    """
    from promaia.config.workspaces import get_workspace_manager
    from promaia.notion.client import get_client

    workspace_mgr = get_workspace_manager()
    workspace_config = workspace_mgr.get_workspace(workspace)
    client = get_client(workspace)

    template_url = "https://www.notion.so/koii/Promaia-2f2d133969678183b4b4c6d6931168f5"

    console.print("\nFallback: Manual template setup", style="bold yellow")
    console.print()
    console.print("1. Open this Notion template:", style="white")
    console.print(f"   {template_url}", style="cyan")
    _render_qr(template_url)
    console.print()
    console.print("2. Click 'Duplicate' in the top right corner", style="white")
    console.print()
    console.print("3. Choose where to save it in your workspace", style="white")
    console.print()
    console.print(
        "4. Share the duplicated page with your Notion integration",
        style="white",
    )
    console.print(
        "   (Click '...' menu -> Add connections -> Select your integration)",
        style="dim",
    )
    console.print()
    console.print("5. Copy the URL of the duplicated page", style="white")
    console.print()

    duplicated_page_input = input(
        "Paste the URL of your duplicated 'Promaia' page: "
    ).strip()

    if not duplicated_page_input:
        console.print("\nPage URL is required", style="red")
        raise ValueError("Promaia page URL is required")

    page_id = _extract_page_id_from_url(duplicated_page_input)
    console.print(f"   Page ID: {page_id}", style="dim")

    console.print("\nDiscovering Promaia components...", style="cyan")
    discovered = await _discover_promaia_components(client, page_id)

    # Validate we found the essentials
    if not discovered.get("agents_database_id"):
        console.print(
            "\nCould not find 'Agents' database in the page", style="red"
        )
        console.print(
            "   Make sure you duplicated the template correctly", style="dim"
        )
        raise ValueError("Agents database not found in duplicated page")

    if not discovered.get("main_prompt_page_id"):
        console.print(
            "\nCould not find 'Main prompt' page in the page", style="red"
        )
        console.print(
            "   Make sure you duplicated the template correctly", style="dim"
        )
        raise ValueError("Main prompt page not found in duplicated page")

    # Save all discovered IDs
    workspace_config.promaia_page_id = page_id
    workspace_config.main_prompt_page_id = discovered["main_prompt_page_id"]
    workspace_config.agents_database_id = discovered["agents_database_id"]
    workspace_config.agents_page_id = page_id
    if discovered.get("prompts_database_id"):
        workspace_config.prompts_database_id = discovered["prompts_database_id"]
    workspace_mgr.save_config()

    console.print("\nPromaia page configured!", style="green")

    return {
        "promaia_page_id": page_id,
        "main_prompt_page_id": discovered["main_prompt_page_id"],
        "agents_database_id": discovered["agents_database_id"],
        "prompts_database_id": discovered.get("prompts_database_id"),
    }


async def ensure_promaia_page_exists(workspace: str) -> Dict[str, str]:
    """Ensure the complete Promaia page structure exists in Notion.

    This is the single entry point for all Promaia Notion setup. It handles:
    - Early return if everything is already configured
    - Partial recovery if only some IDs are set
    - Full interactive creation via API
    - Template fallback on API failure

    Args:
        workspace: Workspace name

    Returns:
        Dict with promaia_page_id, main_prompt_page_id,
        agents_database_id, prompts_database_id
    """
    from promaia.config.workspaces import get_workspace_manager
    from promaia.notion.client import get_client

    workspace_mgr = get_workspace_manager()
    workspace_config = workspace_mgr.get_workspace(workspace)

    # Early return if all IDs are set
    if (
        workspace_config
        and workspace_config.promaia_page_id
        and workspace_config.main_prompt_page_id
        and workspace_config.agents_database_id
    ):
        # Backfill prompts_database_id for existing users who predate this field
        if not workspace_config.prompts_database_id:
            try:
                client = get_client(workspace)
                discovered = await _discover_promaia_components(
                    client, workspace_config.promaia_page_id
                )
                if discovered.get("prompts_database_id"):
                    workspace_config.prompts_database_id = discovered["prompts_database_id"]
                    workspace_mgr.save_config()
            except Exception as e:
                logger.debug("Could not backfill prompts_database_id: %s", e)

        return {
            "promaia_page_id": workspace_config.promaia_page_id,
            "main_prompt_page_id": workspace_config.main_prompt_page_id,
            "agents_database_id": workspace_config.agents_database_id,
            "prompts_database_id": workspace_config.prompts_database_id,
        }

    client = get_client(workspace)

    try:
        # --- Partial recovery ---
        if workspace_config and workspace_config.promaia_page_id:
            console.print("\nChecking existing Promaia page...", style="cyan")
            discovered = await _discover_promaia_components(
                client, workspace_config.promaia_page_id
            )

            # Fill in from discovery (keep existing values, fill gaps)
            if not workspace_config.main_prompt_page_id and discovered.get("main_prompt_page_id"):
                workspace_config.main_prompt_page_id = discovered["main_prompt_page_id"]
            if not workspace_config.agents_database_id and discovered.get("agents_database_id"):
                workspace_config.agents_database_id = discovered["agents_database_id"]
            prompts_db_id = getattr(workspace_config, "prompts_database_id", None)
            if not prompts_db_id and discovered.get("prompts_database_id"):
                workspace_config.prompts_database_id = discovered["prompts_database_id"]

            # Create anything still missing under the existing Promaia page
            if not getattr(workspace_config, "prompts_database_id", None):
                console.print("   Creating missing Prompts database...", style="cyan")
                prompts_db = await client.databases.create(
                    parent={"page_id": workspace_config.promaia_page_id},
                    is_inline=True,
                    title=[{"text": {"content": "Prompts"}}],
                    properties={"Name": {"title": {}}},
                )
                workspace_config.prompts_database_id = prompts_db["id"]

            if not workspace_config.main_prompt_page_id:
                console.print("   Creating missing Main prompt page...", style="cyan")
                db_id = workspace_config.prompts_database_id
                main_prompt = await client.pages.create(
                    parent={"database_id": db_id},
                    properties={
                        "Name": {"title": [{"text": {"content": "Main prompt"}}]}
                    },
                )
                workspace_config.main_prompt_page_id = main_prompt["id"]

            if not workspace_config.agents_database_id:
                console.print("   Creating missing Agents database...", style="cyan")
                agents_db = await client.databases.create(
                    parent={"page_id": workspace_config.promaia_page_id},
                    is_inline=True,
                    title=[{"text": {"content": "Agents"}}],
                    properties={
                        "Name": {"title": {}},
                        "Agent ID": {"rich_text": {}},
                        "Last Run": {"date": {}},
                        "Status": {
                            "select": {
                                "options": [
                                    {"name": "Active", "color": "green"},
                                    {"name": "Inactive", "color": "gray"},
                                    {"name": "Archived", "color": "red"},
                                ]
                            }
                        },
                    },
                )
                workspace_config.agents_database_id = agents_db["id"]

            # Keep agents_page_id in sync for backward compat
            workspace_config.agents_page_id = workspace_config.promaia_page_id
            workspace_mgr.save_config()

            console.print("\nPromaia page is ready!", style="green")
            return {
                "promaia_page_id": workspace_config.promaia_page_id,
                "main_prompt_page_id": workspace_config.main_prompt_page_id,
                "agents_database_id": workspace_config.agents_database_id,
                "prompts_database_id": getattr(workspace_config, "prompts_database_id", None),
            }

        # --- Full creation ---
        console.print("\nFirst-time setup: Promaia Page", style="bold cyan")
        console.print("   (This only happens once per workspace)", style="dim")
        console.print()

        console.print("Searching for accessible Notion pages...", style="cyan")
        search_results = await client.search(
            filter={"value": "page", "property": "object"}
        )

        # Filter to top-level (workspace-parented) pages
        top_level_pages: List[Tuple[str, str]] = []
        for page in search_results.get("results", []):
            if page.get("parent", {}).get("type") != "workspace":
                continue
            title_prop = page.get("properties", {}).get("title", {})
            title_parts = title_prop.get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts) or "Untitled"
            top_level_pages.append((title, page["id"]))

        if not top_level_pages:
            console.print(
                "\nNo pages accessible. Share a page with your Notion "
                "integration and try again.",
                style="red",
            )
            raise RuntimeError(
                "No accessible top-level pages found in Notion workspace"
            )

        if len(top_level_pages) == 1:
            selected_page_id = top_level_pages[0][1]
            console.print(
                f"   Found one page: {top_level_pages[0][0]} (auto-selected)",
                style="dim",
            )
        else:
            console.print(
                f"   Found {len(top_level_pages)} accessible pages\n",
                style="dim",
            )
            selected_page_id = await _select_parent_page(top_level_pages)
            if selected_page_id is None:
                raise RuntimeError("Page selection cancelled")

        console.print("\nCreating Promaia page structure...", style="cyan")
        result = await _create_promaia_structure(client, selected_page_id)

        # Save all IDs to config
        workspace_config.promaia_page_id = result["promaia_page_id"]
        workspace_config.main_prompt_page_id = result["main_prompt_page_id"]
        workspace_config.agents_database_id = result["agents_database_id"]
        workspace_config.prompts_database_id = result["prompts_database_id"]
        workspace_config.agents_page_id = result["promaia_page_id"]
        workspace_mgr.save_config()

        console.print(f"\nPromaia page created!", style="green")
        if result.get("page_url"):
            console.print(f"   {result['page_url']}", style="cyan")
            _render_qr(result["page_url"])

        return result

    except Exception as e:
        logger.warning("Programmatic setup failed (%s), falling back to template", e)
        return await _template_fallback_method(workspace)


async def ensure_agents_database_exists(workspace: str) -> str:
    """Ensure Agents database exists, creating the full Promaia page if needed.

    Thin wrapper around ensure_promaia_page_exists() that returns just the
    agents_database_id, preserving the signature expected by callers like
    create_agent_in_notion().

    Args:
        workspace: Workspace name

    Returns:
        Database ID of Agents database
    """
    result = await ensure_promaia_page_exists(workspace)
    return result["agents_database_id"]


async def setup_promaia_page(workspace: str) -> tuple[str, str]:
    """Set up Promaia page structure for a workspace.

    Thin wrapper around ensure_promaia_page_exists() that returns just the
    (promaia_page_id, main_prompt_page_id) tuple, preserving the signature
    expected by workspace_commands.py.

    Args:
        workspace: Workspace name

    Returns:
        Tuple of (promaia_page_id, main_prompt_page_id)
    """
    result = await ensure_promaia_page_exists(workspace)
    return result["promaia_page_id"], result["main_prompt_page_id"]


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

    console.print(f"\nCreating agent structure in Notion...", style="cyan")

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
        console.print(f"   Created agent page", style="dim")

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
        console.print(f"   Added system prompt", style="dim")

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
        console.print(f"   Created Instructions database", style="dim")

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
        console.print(f"   Created Journal database", style="dim")

        # Store IDs in agent config
        agent_config.system_prompt_page_id = system_prompt_page["id"]
        agent_config.instructions_db_id = instructions_db["id"]
        agent_config.journal_db_id = journal_db["id"]

        console.print(f"\nAgent structure created in Notion!", style="green")
        console.print(f"   Opening agent page in browser...", style="dim")

        return agent_page_id

    except Exception as e:
        logger.error(f"Failed to create agent structure: {e}")
        console.print(f"\nError creating agent structure: {e}", style="red")
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


def generate_agent_id(name: str, existing_agents: List[Any]) -> str:
    """
    Generate unique agent ID from name.

    Args:
        name: Agent name (e.g., "Grace", "Daily Summary")
        existing_agents: List of existing AgentConfig objects

    Returns:
        Unique agent ID (e.g., "grace", "daily-summary", "grace-2")
    """
    # Convert to lowercase, replace spaces/underscores with hyphens
    agent_id = name.lower().replace(' ', '-').replace('_', '-')
    # Remove any non-alphanumeric except hyphens
    import re
    agent_id = re.sub(r'[^a-z0-9-]', '', agent_id)

    # Get existing IDs
    existing_ids = {a.agent_id for a in existing_agents if hasattr(a, 'agent_id') and a.agent_id}

    # Check uniqueness
    if agent_id not in existing_ids:
        return agent_id

    # Add numeric suffix
    suffix = 2
    while f"{agent_id}-{suffix}" in existing_ids:
        suffix += 1

    return f"{agent_id}-{suffix}"
