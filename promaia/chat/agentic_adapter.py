"""
Adapter bridging terminal chat (interface.py) to the agentic loop (agentic_turn.py).

Provides a lightweight agent shim, tool detection, prompt enhancement,
and a terminal-friendly activity callback so `maia chat` can use the same
autonomous multi-tool loop that Slack/Discord agents use.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from promaia.agents.agentic_turn import (
    AgenticTurnResult,
    ToolExecutor,
    _build_tool_suite_registry,
    agentic_turn,
    build_tool_definitions,
)

logger = logging.getLogger(__name__)


# ── Agent shim ────────────────────────────────────────────────────────────

@dataclass
class TerminalAgentShim:
    """Minimal agent object satisfying the interface that ToolExecutor,
    build_tool_definitions read via getattr."""

    agent_id: str = "terminal-user"
    name: str = "Maia"
    workspace: str = ""
    databases: List[str] = field(default_factory=list)
    mcp_tools: List[str] = field(default_factory=list)
    agentic_loop_enabled: bool = True
    agent_calendars: Dict[str, str] = field(default_factory=dict)  # agent_name → calendar_id

    def get_queryable_sources(self) -> List[str]:
        """Return database names (strip day suffixes like 'journal:7' → 'journal')."""
        return [db.split(":")[0] for db in self.databases]


# ── Tool detection ────────────────────────────────────────────────────────

def _resolve_workspace(workspace: str) -> str:
    """Return the given workspace or fall back to the default."""
    if workspace:
        return workspace
    from promaia.config.workspaces import get_default_workspace
    return get_default_workspace() or ""


def _load_agent_calendars(workspace: str) -> Dict[str, str]:
    """Load agent_name → calendar_id mapping for agents with calendars.

    If no agents have calendars and Google Calendar creds exist, auto-creates
    a 'maia' agent with a dedicated calendar so scheduling works out of the box.
    """
    try:
        from promaia.agents.agent_config import AgentConfig, load_agents, save_agent

        agents = load_agents()
        calendars = {a.name: a.calendar_id for a in agents if a.calendar_id}

        if calendars:
            return calendars

        # No agents have calendars — try to auto-create a "maia" agent calendar
        from promaia.auth import get_integration
        google = get_integration("google")
        if not google:
            return {}
        creds = google.get_google_credentials()
        if not creds:
            for acct in google.list_authenticated_accounts():
                creds = google.get_google_credentials(acct)
                if creds:
                    break
        if not creds:
            return {}

        # Find or create "maia" agent. Auto-creating only happens if there's
        # no maia at all. If maia is missing because the config-wipe bug
        # nuked her (see memory/project_config_wipe_bug.md), we'd rather
        # FAIL LOUDLY than silently resurrect a bare-bones agent with
        # databases=[] and a hardcoded mcp_tools list — that pattern was
        # the smoking gun for today's nested-field wipe.
        maia_agent = next((a for a in agents if a.name == "maia"), None)
        if not maia_agent:
            # Check whether agents.json has a maia we just failed to load —
            # if so, refuse to overwrite, force human investigation.
            try:
                from promaia.config.atomic_io import read_section
                section = read_section("agents") or {}
                if isinstance(section, dict):
                    on_disk = section.get("agents", [])
                else:
                    on_disk = section if isinstance(section, list) else []
                if any(a.get("name") == "maia" for a in on_disk):
                    logger.error(
                        "Refusing to auto-create maia: agents.json HAS a maia "
                        "but load_agents() did not return her. This is the "
                        "config-wipe failure mode. NOT overwriting. See "
                        "memory/project_config_wipe_bug.md."
                    )
                    return {}
            except Exception:
                logger.warning("agents.json check before auto-create failed", exc_info=True)
            maia_agent = AgentConfig(
                name="maia",
                agent_id="maia",
                workspace=workspace,
                databases=[],
                prompt_file="",
                mcp_tools=["calendar", "notion", "gmail", "google_sheets"],
                is_default_agent=True,
                description="Default Promaia agent",
            )

        # Create dedicated calendar
        from promaia.gcal.google_calendar import GoogleCalendarManager, google_account_for_workspace
        account = google_account_for_workspace(workspace)
        gcal = GoogleCalendarManager(account=account)
        calendar_id = gcal.get_or_create_agent_calendar(
            agent_name="maia",
            description="Maia agent schedule — events created from maia chat",
        )
        if not calendar_id:
            return {}

        maia_agent.calendar_id = calendar_id
        save_agent(maia_agent)
        logger.info(f"Auto-created maia agent calendar: {calendar_id}")
        return {"maia": calendar_id}

    except Exception:
        logger.warning("Failed to load agent calendars", exc_info=True)
        return {}


def detect_available_tools(workspace: str) -> List[str]:
    """Probe credentials to determine which MCP tools are available."""
    from promaia.auth import get_integration

    workspace = _resolve_workspace(workspace)
    tools: List[str] = []

    try:
        google = get_integration("google")
        if google:
            # Try legacy global token first, then each authenticated account
            creds = google.get_google_credentials()
            if not creds:
                for acct in google.list_authenticated_accounts():
                    creds = google.get_google_credentials(acct)
                    if creds:
                        break
            if creds:
                tools.append("gmail")
                tools.append("calendar")
                tools.append("google_sheets")
    except Exception:
        pass

    try:
        notion = get_integration("notion")
        if notion:
            notion_creds = notion.get_notion_credentials(workspace)
            if notion_creds:
                tools.append("notion")
    except Exception:
        pass

    # web_search is an Anthropic server-side tool — available when using
    # the Anthropic API directly.  OpenRouter does not proxy server tools,
    # so only enable when ANTHROPIC_API_KEY is set.
    try:
        import os
        if os.environ.get("ANTHROPIC_API_KEY"):
            tools.append("web_search")
    except Exception:
        pass

    return tools


# ── Prompt enhancement ────────────────────────────────────────────────────

def _strip_xml_query_tools(prompt: str) -> str:
    """Remove the XML-format query tools section from the base prompt.

    The agentic loop uses Anthropic native tool_use, so the XML <tool_call>
    format docs from format_query_tools_for_prompt() are misleading and
    redundant with conversation_mode.md's tool guidance.
    """
    import re
    return re.sub(
        r"<!-- QUERY_TOOLS_START -->.*?<!-- QUERY_TOOLS_END -->",
        "",
        prompt,
        flags=re.DOTALL,
    ).strip()


def build_agentic_system_prompt(
    base_prompt: str,
    workspace: str,
    mcp_tools: List[str],
    databases: List[str],
    agent_calendar_id: Optional[str] = None,
    agent_calendars: Optional[Dict[str, str]] = None,
    has_platform: bool = False,
    mcp_tool_descriptions: Optional[List[Dict]] = None,
) -> str:
    """Append conversation_mode.md tool guidance to the base terminal prompt.

    Strips the XML-format query tools section (used by the non-agentic path)
    since the agentic loop has its own tool guidance in conversation_mode.md.
    """
    from promaia.utils.env_writer import get_prompts_dir

    # Strip XML query tool docs — agentic loop uses native tool_use
    base_prompt = _strip_xml_query_tools(base_prompt)

    from promaia.ai.prompts import _resolve_prompt
    conv_prompt_path = _resolve_prompt("conversation_mode.md")
    try:
        template = conv_prompt_path.read_text()
    except FileNotFoundError:
        logger.warning(f"conversation_mode.md not found at {conv_prompt_path}")
        return base_prompt

    # Build queryable sources list
    sources_list = ", ".join(db.split(":")[0] for db in databases) if databases else "(none configured)"

    # Build conditional tool sections
    tool_sections_parts = []
    if has_platform:
        tool_sections_parts.append(
            "## Messaging Tools\n\n"
            "- **start_conversation**: DM a user. This is the only tool for "
            "messaging a user directly — use it for questions, confirmations, "
            "and notifications alike. It sends the opening message and hands "
            "off to the Slack reply handler, which continues the conversation "
            "when the user replies. The tool returns immediately; do not wait "
            "for a reply inside your turn. Call it once per user you want to "
            "reach, then wrap up."
        )
    if "gmail" in mcp_tools:
        tool_sections_parts.append(
            "## Gmail Tools (Write)\n\n"
            "- **send_email**: Send email (to, subject, body)\n"
            "- **create_email_draft**: Create draft (not sent)\n"
            "- **reply_to_email**: Reply to a thread (thread_id, message_id, body)\n"
            "  Always search for the thread first to get the thread_id and message_id."
        )
    if "calendar" in mcp_tools:
        cal_section = (
            "## Calendar Tools (Write)\n\n"
            "- **create_calendar_event**: Create event on the **user's** calendar (summary, start_time, end_time)\n"
            "- **update_calendar_event**: Update event (event_id + fields to change)\n"
            "- **delete_calendar_event**: Delete event (event_id)\n"
            "  All three accept an optional `calendar_id` (defaults to primary).\n"
            "  Check for conflicts with **list_calendar_events** before creating events.\n\n"
            "## Calendar Tools (Read)\n\n"
            "- **list_calendar_events**: List events on the **user's** primary calendar "
            "(or any calendar via optional `calendar_id`).\n"
            "- **get_calendar_event**: Get one event by `event_id` "
            "(optional `calendar_id`, defaults to primary).\n"
            "- **list_calendars**: Discover calendar IDs (primary, subscribed, agent calendars)."
        )
        if agent_calendar_id:
            cal_section += (
                "\n\n## Self-Scheduling\n\n"
                "- **schedule_self**: Schedule a future task for **yourself**. Creates an event "
                "on your own dedicated calendar that will trigger you to run at the specified time.\n"
                "  - Use for: reminders, follow-ups, multi-step workflows spanning hours/days\n"
                "  - Params: summary (required), start_time (required), end_time (optional), "
                "description (optional — include context for your future self)\n"
                "- **list_self_calendar_events**: List events on **your own** agent calendar — "
                "the same calendar schedule_self writes to. Use this when the user asks about "
                "your scheduled triggers or events on \"the agent calendar\". No calendar_id needed.\n"
                "- **get_self_calendar_event**: Get one event from your own calendar by `event_id`.\n\n"
                "### Which calendar tool to use\n\n"
                "- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's calendar)\n"
                "- You need to follow up later / check on something tomorrow / continue a workflow → **schedule_self** (your calendar)\n"
                "- User asks about your scheduled triggers / \"what's on the agent calendar\" → **list_self_calendar_events**\n"
                "- User asks about their own calendar → **list_calendar_events** (primary)\n\n"
                "### Important: scheduling ≠ executing\n\n"
                "When you create a calendar event for future execution, your job is DONE once the event is created. "
                "Do NOT execute the event's workflow immediately — it will be triggered automatically at the scheduled time. "
                "The event description should contain instructions for your future self, not a to-do list for right now."
            )
        # Agent calendar scheduling + read (for chat mode)
        if agent_calendars:
            if len(agent_calendars) == 1:
                name = next(iter(agent_calendars))
                cal_section += (
                    f"\n\n## Agent Calendar Scheduling & Reads\n\n"
                    f"- **schedule_agent_event**: Schedule events on **{name}**'s dedicated agent calendar.\n"
                    f"  No need to specify the agent parameter — {name} is the only agent with a calendar.\n"
                    f"- **list_agent_calendar_events**: List events on {name}'s agent calendar. "
                    f"No `calendar_id` needed — auto-resolves.\n"
                    f"- **get_agent_calendar_event**: Get one event from {name}'s agent calendar by `event_id`.\n\n"
                    f"### Which calendar tool to use\n\n"
                    f"- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's personal calendar)\n"
                    f"- User says \"schedule X on the agent calendar\" / \"add to {name}'s calendar\" → **schedule_agent_event** ({name}'s calendar)\n"
                    f"- User asks \"what's on {name}'s calendar\" / \"check the agent calendar\" → **list_agent_calendar_events**\n"
                    f"- User asks about their own calendar → **list_calendar_events**"
                )
            else:
                names = ", ".join(sorted(agent_calendars.keys()))
                cal_section += (
                    f"\n\n## Agent Calendar Scheduling & Reads\n\n"
                    f"- **schedule_agent_event**: Schedule events on an agent's dedicated calendar. "
                    f"Requires `agent` parameter. Available agents: {names}\n"
                    f"- **list_agent_calendar_events**: List events on a named agent's calendar. "
                    f"Requires `agent` parameter.\n"
                    f"- **get_agent_calendar_event**: Get one event from a named agent's calendar. "
                    f"Requires `agent` parameter.\n\n"
                    f"### Which calendar tool to use\n\n"
                    f"- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's personal calendar)\n"
                    f"- User says \"schedule X on the agent calendar\" / names a specific agent → **schedule_agent_event** (agent's calendar)\n"
                    f"- User asks \"what's on <agent>'s calendar\" → **list_agent_calendar_events**\n"
                    f"- User asks about their own calendar → **list_calendar_events**"
                )
        tool_sections_parts.append(cal_section)
    if "notion" in mcp_tools:
        tool_sections_parts.append(
            "## Notion Tools (Read & Write)\n\n"
            "- **notion_search**: Search Notion for pages/databases by title\n"
            "- **notion_create_page**: Create a new page in a Notion database\n"
            "- **notion_update_page**: Update an existing page's properties or content\n"
            "- **notion_query_database**: Query a Notion database with filters"
        )
    if "google_sheets" in mcp_tools:
        tool_sections_parts.append(
            "## Google Sheets Tools (Read & Write)\n\n"
            "- **sheets_find**: Search Google Drive for spreadsheets by name\n"
            "- **sheets_ingest**: Ingest a sheet into context (previews first ~10k tokens; use sheets_read_range for more)\n"
            "- **sheets_read_range**: Read a specific A1 range — results auto-save as toggleable context sources\n"
            "- **sheets_update_cells**: Write values to one or more ranges\n"
            "- **sheets_append_rows**: Append rows after the last row of data\n"
            "- **sheets_insert_rows**: Insert rows at a specific position (shifts existing rows down)\n"
            "- **sheets_create_spreadsheet**: Create a new spreadsheet\n"
            "- **sheets_manage_sheets**: Add/delete/rename tabs\n"
            "- **sheets_format_cells**: Bold, colors, number format, column width, merge\n\n"
            "### Discovery workflow\n\n"
            "If the user mentions a sheet that isn't in your context or synced sources:\n"
            "1. Use **sheets_find** to search for it by name\n"
            "2. Use **sheets_ingest** to load its full content into context\n"
            "3. Then use sheets_read_range / sheets_update_cells / etc. as normal\n\n"
            "Only ingest once per sheet per conversation. If you already ingested "
            "a sheet or it came from synced sources, do NOT ingest again.\n\n"
            "### Inserting vs appending rows\n\n"
            "- **sheets_insert_rows**: Use when adding data in the middle of a "
            "table (e.g. before a totals row). Existing rows shift down and "
            "formula references update automatically.\n"
            "- **sheets_append_rows**: Only use when adding to the very end of "
            "the data. Never use append to add items above a totals/summary row "
            "— that breaks formula references.\n\n"
            "### Reconciliation after edits\n\n"
            "After making changes to a sheet (insert, update, append), always "
            "reconcile: use sheets_read_range to read back the affected rows "
            "plus any dependent rows (totals, summaries, lookups). The read "
            "returns both formulas and display values so you can verify "
            "correctness. Compare against the ingested data and fix issues:\n\n"
            "- New rows missing formulas that the column pattern requires → "
            "write them (e.g. if column D has =B/C in every row, new rows need it too)\n"
            "- Total/summary formulas whose ranges didn't expand to cover "
            "inserted rows → update the formula\n"
            "- #REF!, #DIV/0!, or other errors caused by your edit → fix them\n\n"
            "Do NOT ask the user before fixing issues your edit caused — just "
            "fix them. If you notice pre-existing problems (circular deps, "
            "broken formulas that predate your edit), flag those to the user "
            "and ask before touching them.\n\n"
            "### Spreadsheet identification\n\n"
            "The `spreadsheet` parameter accepts:\n"
            "- Spreadsheet name (looked up in synced sheets, e.g. 'Margins')\n"
            "- Google Sheets URL (ID extracted automatically)\n"
            "- Raw spreadsheet ID\n\n"
            "### Local-first principle\n\n"
            "Synced sheets are available via query_sql for bulk reads. "
            "Use sheets_read_range only when you need fresh data from a "
            "specific range or the sheet isn't synced.\n\n"
            "### Value format\n\n"
            "Values are 2D arrays: `[[row1col1, row1col2], [row2col1, row2col2]]`. "
            "With `USER_ENTERED` (default), formulas like `=SUM(A1:A10)` are interpreted. "
            "Use `RAW` to store literal strings."
        )
    if "web_search" in mcp_tools:
        tool_sections_parts.append(
            "## Web Search & Fetch (Read)\n\n"
            "- **web_search**: Search the internet for current information. "
            "The search is performed automatically and results are synthesized "
            "directly into the response.\n"
            "- **web_fetch**: Fetch and read the full content of a specific web page "
            "by URL. Returns the page as clean extracted text.\n\n"
            "### Local-first principle\n\n"
            "Promaia syncs data from the user's workspace apps into local databases. "
            "Always check local sources first — query_sql, query_vector, query_source "
            "are faster, cheaper, and contain the user's private data. Web search is "
            "for information that lives **outside** the user's workspace:\n"
            "- Public facts, documentation, news, market data\n"
            "- Recent events or developments the user wouldn't have locally\n"
            "- Verification of claims, dates, or external references\n"
            "- Research on topics the user hasn't worked on before\n\n"
            "If the user asks about their own projects, emails, tasks, or calendar — "
            "that's local data, not a web search.\n\n"
            "### When to reach for web search\n\n"
            "- User explicitly asks: \"look up\", \"search for\", \"what's the latest on\"\n"
            "- User asks about something external: a company, a technology, "
            "a person they don't work with, public documentation\n"
            "- You need a fact you don't confidently know and isn't in local data\n"
            "- User provides a URL — use web_fetch directly (skip search)\n\n"
            "### Query formulation\n\n"
            "Web search queries should be concise and specific — think Google, "
            "not vector search. Short keyword phrases work best.\n\n"
            "Good: \"trafilatura python html extraction library\"\n"
            "Good: \"Anthropic Claude API tool use 2025\"\n"
            "Bad: \"I need to find information about how to extract content from "
            "HTML pages using Python libraries that are good at it\"\n\n"
            "### Research depth\n\n"
            "When you decide something is worth researching, research it thoroughly. "
            "Don't be afraid to take your time. A shallow search that returns a "
            "half-answer is worse than no search at all — it gives false confidence. "
            "If the user asked a question worth searching the web for, they deserve "
            "a real answer built from real sources.\n\n"
            "- Search from multiple angles if the first query doesn't cover it. "
            "Rephrase, try adjacent terms, narrow or broaden.\n"
            "- Fetch the actual pages. The search synthesis is a starting point, "
            "not the answer. Read the primary sources.\n"
            "- Read multiple sources when they might disagree or when you need "
            "to cross-reference. Don't trust a single page for anything important.\n"
            "- Follow the thread. If a page references something relevant that "
            "you don't have context on, fetch that too.\n"
            "- Summarize what you learned and where you learned it. The user "
            "should be able to trace your reasoning back to sources.\n\n"
            "The agentic loop gives you room to work. Use it. Three tool calls "
            "that build a complete picture are better than one that skims the surface.\n\n"
            "### Research workflow\n\n"
            "1. **web_search** to find relevant results and get a quick synthesized answer\n"
            "2. Review the search results — pick URLs that look most relevant\n"
            "3. **web_fetch** those URLs to read the full page content\n"
            "4. If gaps remain, search again from a different angle or fetch more pages\n"
            "5. Synthesize your answer from the full page content, citing sources\n\n"
            "For quick factual questions, web_search alone is often enough. "
            "Use the full search → fetch pipeline when you need article text, "
            "are comparing sources, or extracting specific details "
            "the synthesis didn't cover.\n\n"
            "### Authenticated URLs — don't web_fetch these\n\n"
            "web_fetch only works on **public** pages. These services require login "
            "and will return empty/useless content if you try to fetch them:\n"
            "- **Notion** (notion.so/*) — extract the page ID from the URL and use "
            "notion_get_blocks or notion_search instead\n"
            "- **Google Docs/Sheets/Drive** (docs.google.com/*) — use local query tools "
            "if the data is synced, otherwise tell the user you can't access it\n"
            "- **Jira, Linear, etc.** — anything behind a login wall that isn't synced locally\n"
            "  (Note: Slack and Discord ARE synced locally — use query_sql/query_vector for those)\n\n"
            "How to extract a Notion page ID from a URL:\n"
            "- URL format: notion.so/workspace/Page-Title-<32-hex-chars>\n"
            "- The page ID is the last 32 hex characters, formatted as a UUID with dashes: "
            "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\n"
            "- Example: notion.so/koii/One-Sheet-319d13396967806284d8e1ca1d90f9f5 "
            "→ page_id = 319d1339-6967-8062-84d8-e1ca1d90f9f5\n\n"
            "### Guidelines\n\n"
            "- If the user provides a public URL, use web_fetch — don't search for it\n"
            "- Don't web search for things you already know or that are in local data\n"
            "- When citing web results, include the source URL so the user can verify"
        )
    tool_sections = "\n\n".join(tool_sections_parts)

    # Notion-specific guidance
    notion_guidance = ""
    if "notion" in mcp_tools:
        notion_guidance = (
            "## Built-in query tools vs Notion tools\n\n"
            "- **Prefer built-in query tools** (query_sql, query_vector, query_source) "
            "for loading large chunks of context from synced data. They are cheaper, "
            "faster, and more effective for anything that has had time to be synced.\n"
            "- **Use Notion tools** for transient, specific pages — especially ones "
            "you are actively creating or editing in this session, or anything that "
            "may have been updated very recently and not yet synced.\n"
            "- For fetching a single specific page, prefer the Notion API "
            "(notion_get_page) as synced data may be stale.\n"
            "- After creating a Notion page, use the URL from the create response "
            "to share links — do not construct Notion URLs manually."
        )

    # Interview workflows disabled outside onboarding flow.
    workflow_section = ""

    # Apply template substitutions
    filled = template.replace("{agent_name}", "Maia")
    filled = filled.replace("{platform}", "terminal")
    filled = filled.replace("{sources}", sources_list)
    # Tool sections embedded in conversation_mode.md template — positioned before
    # context sources so the agent sees available tools early in the prompt.
    filled = filled.replace("{tool_sections}", tool_sections)
    filled = filled.replace("{notion_guidance}", notion_guidance)

    # Workflow/interview descriptions, MCP tool descriptions, and saved workflows
    # are NOT injected into the prompt. They appear in the suite index (Think mode)
    # and as loaded tool schemas (Act mode).

    return base_prompt + "\n\n" + filled


# ── Terminal activity callback ────────────────────────────────────────────

def make_terminal_activity_callback(
    print_text_fn: Callable[..., None],
) -> Callable[..., Any]:
    """Build a terminal-friendly callback for agentic loop progress updates."""

    async def on_tool_activity(
        tool_name: str,
        tool_input: Optional[Dict] = None,
        completed: bool = False,
        summary: Optional[str] = None,
        **kwargs,
    ):
        tool_input = tool_input or {}

        # Plan announcement
        if tool_name == "__plan__":
            steps = tool_input.get("steps", [])
            print_text_fn("\n📋 Plan:", style="bold cyan")
            for i, step in enumerate(steps, 1):
                print_text_fn(f"  {i}. {step}", style="dim cyan")
            print_text_fn("")
            return

        # Plan step marker
        if tool_name == "__plan_step__":
            current = tool_input.get("step", 0)
            total = tool_input.get("total", 0)
            print_text_fn(f"\n▶ Step {current}/{total}", style="bold yellow")
            return

        # Plan done
        if tool_name == "__plan_done__":
            return

        # Context trim
        if tool_name == "__context_trim__":
            print_text_fn("  ✂️  Context too large, trimming and retrying", style="dim yellow")
            return

        # Context toggle
        if tool_name == "context" and completed:
            action = (tool_input or {}).get("action", "")
            sources = (tool_input or {}).get("sources", []) or (tool_input or {}).get("shelves", [])
            name = (tool_input or {}).get("name", "")
            target = ", ".join(sources) if sources else name
            if action in ("on", "all_on"):
                print_text_fn(f"  📖 Context ON: {target or 'all'}", style="dim cyan")
            elif action in ("off", "all_off"):
                print_text_fn(f"  📕 Context OFF: {target or 'all'}", style="dim cyan")
            elif action == "add":
                print_text_fn(f"  📚 Context added: {name}", style="dim cyan")
            elif action == "remove":
                print_text_fn(f"  🗑️  Context removed: {target}", style="dim cyan")
            return

        # Think/Act mode switching
        if tool_name == "act" and completed:
            suites = (tool_input or {}).get("suites", [])
            print_text_fn(f"  🔧 Act mode ({', '.join(suites)})", style="dim yellow")
            return
        if tool_name == "done" and completed:
            print_text_fn("  📚 Think mode", style="dim cyan")
            return

        # Notepad update
        if tool_name == "notepad" and completed:
            action = (tool_input or {}).get("action", "")
            labels = {"write": "updated", "append": "appended", "clear": "cleared", "read": "read"}
            label = labels.get(action, action)
            print_text_fn(f"  📝 Notes {label}", style="dim cyan")
            return

        # Memory
        if tool_name == "memory" and completed:
            action = (tool_input or {}).get("action", "")
            name = (tool_input or {}).get("name", "")
            if action == "save":
                print_text_fn(f"  💾 Memory saved: {name}", style="dim cyan")
            elif action == "recall":
                print_text_fn(f"  🧠 Memory recalled: {name}", style="dim cyan")
            elif action == "delete":
                print_text_fn(f"  🗑️  Memory deleted: {name}", style="dim cyan")
            elif action == "list":
                print_text_fn("  🧠 Memory listed", style="dim cyan")
            return

        # Regular tool activity
        if not completed:
            # Tool starting
            input_summary = _summarize_tool_input(tool_name, tool_input)
            print_text_fn(f"  🔧 {tool_name}{input_summary}", style="dim yellow")
        else:
            # Tool completed
            if summary:
                # Truncate long summaries
                display = summary[:200] + "..." if len(summary) > 200 else summary
                # Detect errors in summary and use appropriate indicator
                is_error = "(error)" in display.lower() or display.startswith("Error")
                if is_error:
                    print_text_fn(f"  ✗ {display}", style="dim red")
                else:
                    print_text_fn(f"  ✓ {display}", style="dim green")

    return on_tool_activity


def _summarize_tool_input(tool_name: str, tool_input: Dict) -> str:
    """Create a short summary of tool input for terminal display."""
    if not tool_input:
        return ""

    if tool_name == "query_sql":
        query = tool_input.get("query", "")
        return f": {query[:80]}" if query else ""
    elif tool_name == "query_vector":
        query = tool_input.get("query", "")
        return f": {query[:80]}" if query else ""
    elif tool_name == "query_source":
        db = tool_input.get("database", "")
        days = tool_input.get("days", "")
        return f": {db}" + (f" ({days}d)" if days else "")
    elif tool_name in ("schedule_self", "schedule_agent_event"):
        summary = tool_input.get("summary", "")
        start = tool_input.get("start_time", "")
        agent = tool_input.get("agent", "")
        agent_label = f" [{agent}]" if agent else ""
        return f": '{summary[:40]}' at {start}{agent_label}" if summary else ""
    elif tool_name == "send_email":
        to = tool_input.get("to", "")
        subj = tool_input.get("subject", "")
        return f": → {to} '{subj[:40]}'" if to else ""
    elif tool_name in ("notion_search", "notion_query_database"):
        query = tool_input.get("query", tool_input.get("filter", ""))
        return f": {str(query)[:60]}" if query else ""
    elif tool_name == "web_search":
        query = tool_input.get("query", "")
        return f": {query[:80]}" if query else ""
    elif tool_name == "web_fetch":
        url = tool_input.get("url", "")
        return f": {url[:80]}" if url else ""
    elif tool_name.startswith("sheets_"):
        ss = tool_input.get("spreadsheet", tool_input.get("title", ""))
        rng = tool_input.get("range", "")
        parts = []
        if ss:
            parts.append(ss[:40])
        if rng:
            parts.append(rng)
        return f": {' '.join(parts)}" if parts else ""
    elif tool_name == "context":
        action = tool_input.get("action", "")
        sources = tool_input.get("sources", []) or tool_input.get("shelves", [])
        name = tool_input.get("name", "")
        target = ", ".join(sources) if sources else name
        return f": {action} {target}" if target else f": {action}"
    elif tool_name == "act":
        suites = tool_input.get("suites", [])
        return f": {', '.join(suites)}" if suites else ""
    elif tool_name == "done":
        return ""
    elif tool_name == "notepad":
        action = tool_input.get("action", "")
        return f": {action}"
    else:
        # Generic: show first key-value
        for k, v in tool_input.items():
            return f": {k}={str(v)[:50]}"
    return ""


# ── Main entry point ──────────────────────────────────────────────────────

async def run_agentic_turn(
    system_prompt: str,
    messages: List[Dict],
    workspace: str,
    mcp_tools: List[str],
    databases: List[str],
    print_text_fn: Callable[..., None],
    workflow_prompt: Optional[str] = None,
    notepad_content: Optional[str] = None,
    source_states: Optional[Dict[str, Dict]] = None,
    on_tool_activity: Optional[Callable] = None,
    messaging_enabled: bool = False,
) -> AgenticTurnResult:
    """Run an agentic turn using the full autonomous tool loop.

    This is the main bridge between interface.py and agentic_turn.py.

    Args:
        workflow_prompt: If set, prepended to system prompt for active interview workflows.
        messaging_enabled: If True, initialize a messaging platform (Slack/Discord)
            so the messaging suite is registered and its tools are executable.
            Threaded through from conversation_manager based on the agent's
            messaging_enabled config flag.
    """
    workspace = _resolve_workspace(workspace)

    # Load agent calendars if calendar tools are available
    agent_calendars: Dict[str, str] = {}
    if "calendar" in mcp_tools:
        agent_calendars = _load_agent_calendars(workspace)

    # Build agent shim
    shim = TerminalAgentShim(
        workspace=workspace,
        databases=databases,
        mcp_tools=mcp_tools,
        agent_calendars=agent_calendars,
    )
    # Reflect permissions on the shim so downstream code sees the truth.
    shim.messaging_enabled = messaging_enabled
    shim.is_default_agent = True  # conversation path is always the default agent (maia)

    # Initialize messaging platform if the agent has permission. Reuses the
    # same helper the scheduled/calendar-triggered path uses, so both code
    # paths share identical Slack-bot-token resolution via the auth module.
    platform = None
    if messaging_enabled:
        try:
            from promaia.agents.run_goal import _init_messaging_platform
            platform = _init_messaging_platform(shim)
        except Exception as e:
            logger.warning(f"Failed to initialize messaging platform: {e}")
        if platform is None:
            logger.warning(
                "messaging_enabled=True but no messaging platform could be "
                "initialized (no Slack/Discord bot token found). The "
                "messaging tool suite will not be available this turn."
            )
    has_platform = platform is not None

    # Build tool definitions (legacy, used as fallback)
    tools = build_tool_definitions(shim, has_platform=has_platform)

    # Build suite registry for Think/Act mode
    suite_registry = _build_tool_suite_registry(shim, has_platform=has_platform)

    # Create tool executor
    executor = ToolExecutor(agent=shim, workspace=workspace, platform=platform)

    # Restore notepad from previous turn
    if notepad_content:
        executor._notepad = notepad_content

    # Restore context sources from previous turn (content + on/off state)
    prev_sources = source_states or {}
    if prev_sources:
        for name, source_data in prev_sources.items():
            # Only restore query-created sources (browser sources get rebuilt from context)
            if source_data.get("source") != "browser":
                executor._sources[name] = dict(source_data)
                logger.info(f"Restored source '{name}': on={source_data.get('on')}, {len(source_data.get('content', ''))} chars")
    else:
        logger.info("No context source states to restore from previous turn")

    # Connect external MCP servers and discover their tools
    mcp_tool_defs = []
    mcp_suites = {}
    try:
        await executor.connect_mcp_servers()
        mcp_tool_defs = await executor.get_mcp_tool_definitions()
        if mcp_tool_defs:
            tools.extend(mcp_tool_defs)
            logger.info(f"Added {len(mcp_tool_defs)} MCP tools from external servers")
            # Group MCP tools into suites by server name (mcp__{server}__{tool})
            from collections import defaultdict
            mcp_groups = defaultdict(list)
            for td in mcp_tool_defs:
                parts = td["name"].split("__")
                if len(parts) >= 3:
                    server_name = parts[1]
                    mcp_groups[server_name].append(td)
            for server_name, server_tools in mcp_groups.items():
                mcp_suites[server_name] = {
                    "tools": server_tools,
                    "description": f"{server_name} MCP tools",
                    "count": len(server_tools),
                }
    except Exception as e:
        logger.warning(f"MCP server connection failed (continuing without): {e}")

    # Enhance system prompt with conversation_mode.md guidance
    enhanced_prompt = build_agentic_system_prompt(
        system_prompt, workspace, mcp_tools, databases,
        agent_calendars=agent_calendars,
        mcp_tool_descriptions=mcp_tool_defs if mcp_tool_defs else None,
    )

    # Inject persistent notepad into system prompt
    if executor._notepad:
        enhanced_prompt += f"\n\n## Working Notes\n\n{executor._notepad}"

    # Inject persistent memory index (always visible, like notepad)
    try:
        from promaia.agents.memory_store import load_memory_index
        memory_index = load_memory_index(workspace)
        if memory_index:
            enhanced_prompt += f"\n\n## Memory\n\n{memory_index}"
    except ImportError:
        pass  # memory_store not yet available on this branch

    # Inject active workflow prompt if in an interview
    if workflow_prompt:
        enhanced_prompt = workflow_prompt + "\n\n" + enhanced_prompt

    # Split prompt into base + context block → create context sources
    # Each database becomes its own context source, OFF by default
    context_marker = "\n\n## Context ("
    context_data_block = ""
    if context_marker in enhanced_prompt:
        split_idx = enhanced_prompt.index(context_marker)
        base_prompt_part = enhanced_prompt[:split_idx]
        context_data_block = enhanced_prompt[split_idx:]
        enhanced_prompt = base_prompt_part

        # Parse individual database sections into context sources
        import re
        db_pattern = re.compile(
            r'### === (.+?) DATABASE \((\d+) entries\) ===\n',
        )
        matches = list(db_pattern.finditer(context_data_block))
        for i, match in enumerate(matches):
            db_name = match.group(1).lower()
            page_count = int(match.group(2))
            # Extract content from this match to the next (or end)
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(context_data_block)
            source_content = context_data_block[start:end]

            # Restore source state from previous turn, default ON for browser-loaded
            prev_state = prev_sources.get(db_name, {}).get("on", True) if prev_sources else True

            # Extract entry titles from formatted content
            title_pattern = re.compile(r'File: `(.+?)`\)')
            titles = [m.group(1) for m in title_pattern.finditer(source_content)]

            executor._sources[db_name] = {
                "content": source_content,
                "on": prev_state,
                "page_count": page_count,
                "source": "browser",
                "titles": titles,
            }

    # Library index is built dynamically inside the agentic loop each iteration
    # (sources change during the loop as tools load/toggle context)

    # Build activity callback
    # Use external callback if provided (Slack/Discord), otherwise build terminal callback
    activity_cb = on_tool_activity or make_terminal_activity_callback(print_text_fn)

    # Run the agentic loop
    try:
        result = await agentic_turn(
            system_prompt=enhanced_prompt,
            messages=messages,
            tools=tools,
            tool_executor=executor,
            max_iterations=40,
            on_tool_activity=activity_cb,
            context_data_block=context_data_block,
            suite_registry=suite_registry,
            mcp_suites=mcp_suites if mcp_suites else None,
        )
    finally:
        await executor.disconnect_mcp_servers()

    # Persist notepad and context source data for next turn
    result.notepad_content = executor._notepad or None
    result.source_states = dict(executor._sources) if executor._sources else None

    return result
