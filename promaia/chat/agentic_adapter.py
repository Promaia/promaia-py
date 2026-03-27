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
    _generate_plan,
    agentic_turn,
    build_tool_definitions,
)

logger = logging.getLogger(__name__)


# ── Agent shim ────────────────────────────────────────────────────────────

@dataclass
class TerminalAgentShim:
    """Minimal agent object satisfying the interface that ToolExecutor,
    build_tool_definitions, and _generate_plan read via getattr."""

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

        # Find or create "maia" agent
        maia_agent = next((a for a in agents if a.name == "maia"), None)
        if not maia_agent:
            maia_agent = AgentConfig(
                name="maia",
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
        calendar_id = gcal.create_agent_calendar(
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

    try:
        perplexity = get_integration("perplexity")
        if perplexity:
            cred = perplexity.get_default_credential()
            if cred:
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
            "- **send_message**: Send a one-way message (no reply expected).\n"
            "  Use 'user' to DM someone by name, or 'channel_id' for a channel.\n"
            "- **start_conversation**: Start a back-and-forth DM conversation.\n"
            "  Sends your message and waits for the user's reply (up to 15 min).\n"
            "  Returns the user's response. Use this when you need a reply.\n"
            "  You can call it multiple times to continue the conversation."
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
            "  Always check for conflicts with query_sql before creating events."
        )
        if agent_calendar_id:
            cal_section += (
                "\n\n## Self-Scheduling\n\n"
                "- **schedule_self**: Schedule a future task for **yourself**. Creates an event "
                "on your own dedicated calendar that will trigger you to run at the specified time.\n"
                "  - Use for: reminders, follow-ups, multi-step workflows spanning hours/days\n"
                "  - Params: summary (required), start_time (required), end_time (optional), "
                "description (optional — include context for your future self)\n\n"
                "### Which calendar tool to use\n\n"
                "- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's calendar)\n"
                "- You need to follow up later / check on something tomorrow / continue a workflow → **schedule_self** (your calendar)\n\n"
                "### Important: scheduling ≠ executing\n\n"
                "When you create a calendar event for future execution, your job is DONE once the event is created. "
                "Do NOT execute the event's workflow immediately — it will be triggered automatically at the scheduled time. "
                "The event description should contain instructions for your future self, not a to-do list for right now."
            )
        # Agent calendar scheduling (for chat mode)
        if agent_calendars:
            if len(agent_calendars) == 1:
                name = next(iter(agent_calendars))
                cal_section += (
                    f"\n\n## Agent Calendar Scheduling\n\n"
                    f"- **schedule_agent_event**: Schedule events on **{name}**'s dedicated agent calendar.\n"
                    f"  No need to specify the agent parameter — {name} is the only agent with a calendar.\n\n"
                    f"### Which calendar tool to use\n\n"
                    f"- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's personal calendar)\n"
                    f"- User says \"schedule X on the agent calendar\" / \"add to maia's calendar\" → **schedule_agent_event** ({name}'s calendar)"
                )
            else:
                names = ", ".join(sorted(agent_calendars.keys()))
                cal_section += (
                    f"\n\n## Agent Calendar Scheduling\n\n"
                    f"- **schedule_agent_event**: Schedule events on an agent's dedicated calendar.\n"
                    f"  You must specify the `agent` parameter. Available agents: {names}\n\n"
                    f"### Which calendar tool to use\n\n"
                    f"- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's personal calendar)\n"
                    f"- User says \"schedule X on the agent calendar\" / names a specific agent → **schedule_agent_event** (agent's calendar)"
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
            "- **sheets_ingest**: One-time ingest of a sheet into context (CSV with inline formulas)\n"
            "- **sheets_read_range**: Read a specific A1 range from a sheet\n"
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
            "Returns a synthesized answer plus a list of individual search results "
            "(title, URL, snippet per result).\n"
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
            "- After creating a Notion page, use the URL from the create response "
            "to share links — do not construct Notion URLs manually."
        )

    # Build workflow awareness section
    workflow_section = ""
    try:
        from promaia.chat.workflows import list_workflows
        workflows = list_workflows()
        if workflows:
            lines = [
                "## Configuration Interviews\n",
                "You can guide the user through these configuration workflows. "
                "When the user wants to set up or configure something, call "
                "`start_interview` with the appropriate workflow name. "
                "The interview system will provide step-by-step guidance.\n",
                "Available workflows:",
            ]
            for wf in workflows:
                lines.append(f"- **{wf['name']}**: {wf['description']}")
            workflow_section = "\n".join(lines)
    except Exception as e:
        logger.debug(f"Could not load workflows: {e}")

    # Apply template substitutions
    filled = template.replace("{agent_name}", "Maia")
    filled = filled.replace("{platform}", "terminal")
    filled = filled.replace("{sources}", sources_list)
    filled = filled.replace("{tool_sections}", tool_sections)
    filled = filled.replace("{notion_guidance}", notion_guidance)

    if workflow_section:
        filled += "\n\n" + workflow_section

    # Add external MCP tool descriptions if any are connected
    if mcp_tool_descriptions:
        mcp_lines = [
            "## External MCP Tools\n",
            "The following tools are available from external MCP servers. "
            "Call them by their full name (including the mcp__ prefix).\n",
        ]
        for tool_def in mcp_tool_descriptions:
            mcp_lines.append(f"- **{tool_def['name']}**: {tool_def['description']}")
        filled += "\n\n" + "\n".join(mcp_lines)

    # Load saved workflows for prompt
    try:
        from promaia.tools.workflow_store import list_workflows_for_prompt
        wf_summaries = list_workflows_for_prompt(workspace)
        if wf_summaries:
            wf_lines = [
                "## Saved Workflows\n",
                "You have saved workflows available. When you recognize a user's request "
                "matches a saved workflow, mention it and ask if they'd like you to follow it. "
                "Use `get_workflow_details` to load the full steps and example runs before executing. "
                "After completing a workflow, offer to save the run as an example.\n",
            ]
            for wf in wf_summaries:
                wf_lines.append(f"- **{wf['name']}**: {wf['description']}")
            filled += "\n\n" + "\n".join(wf_lines)
    except Exception as e:
        logger.debug(f"Could not load saved workflows: {e}")

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

        # Context compact/restore
        if tool_name == "compact_context":
            if completed:
                if (tool_input or {}).get("restore", False):
                    print_text_fn("  🔊 Context restored", style="dim cyan")
                else:
                    notes = (tool_input or {}).get("notes", "")
                    preview = notes[:80] + "..." if len(notes) > 80 else notes
                    print_text_fn(f"  📝 Context compacted: {preview}", style="dim cyan")
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
    shelf_states: Optional[Dict[str, Dict]] = None,
) -> AgenticTurnResult:
    """Run an agentic turn using the full autonomous tool loop.

    This is the main bridge between interface.py and agentic_turn.py.

    Args:
        workflow_prompt: If set, prepended to system prompt for active interview workflows.
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

    # Build tool definitions
    tools = build_tool_definitions(shim, has_platform=False)

    # Create tool executor
    executor = ToolExecutor(agent=shim, workspace=workspace)

    # Restore notepad from previous turn
    if notepad_content:
        executor._notepad = notepad_content

    # Shelf states from previous turn (for restoring on/off state)
    context_state_shelves = shelf_states or {}

    # Connect external MCP servers and discover their tools
    mcp_tool_defs = []
    try:
        await executor.connect_mcp_servers()
        mcp_tool_defs = await executor.get_mcp_tool_definitions()
        if mcp_tool_defs:
            tools.extend(mcp_tool_defs)
            logger.info(f"Added {len(mcp_tool_defs)} MCP tools from external servers")
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

    # Inject active workflow prompt if in an interview
    if workflow_prompt:
        enhanced_prompt = workflow_prompt + "\n\n" + enhanced_prompt

    # Split prompt into base + context block → create library shelves
    # Each database source becomes its own shelf, OFF by default
    context_marker = "\n\n## Context ("
    context_data_block = ""
    if context_marker in enhanced_prompt:
        split_idx = enhanced_prompt.index(context_marker)
        base_prompt_part = enhanced_prompt[:split_idx]
        context_data_block = enhanced_prompt[split_idx:]
        enhanced_prompt = base_prompt_part

        # Parse individual database sections into shelves
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
            shelf_content = context_data_block[start:end]

            # Restore shelf state from previous turn, default ON for browser-loaded
            prev_state = context_state_shelves.get(db_name, {}).get("on", True) if context_state_shelves else True

            executor._shelves[db_name] = {
                "content": shelf_content,
                "on": prev_state,
                "page_count": page_count,
                "source": "browser",
            }

    # Inject library index into prompt (always visible)
    library_index = executor.build_library_index()
    if library_index:
        enhanced_prompt += "\n\n" + library_index

    # Build activity callback
    activity_cb = make_terminal_activity_callback(print_text_fn)

    # Check if planning is needed (extract latest user message)
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    plan = None
    if user_message:
        plan = await _generate_plan(
            user_message=user_message,
            agent=shim,
            available_tools=[t["name"] for t in tools],
        )

    # Emit plan via callback if generated
    if plan and activity_cb:
        await activity_cb(
            tool_name="__plan__",
            tool_input={"steps": plan},
        )

    # Run the agentic loop
    try:
        result = await agentic_turn(
            system_prompt=enhanced_prompt,
            messages=messages,
            tools=tools,
            tool_executor=executor,
            max_iterations=40,
            on_tool_activity=activity_cb,
            plan=plan,
            context_data_block=context_data_block,
        )
    finally:
        await executor.disconnect_mcp_servers()

    # Persist notepad and shelf states for next turn
    result.notepad_content = executor._notepad or None
    result.shelf_states = {
        name: {"on": shelf["on"], "page_count": shelf.get("page_count", 0)}
        for name, shelf in executor._shelves.items()
    } if executor._shelves else None

    return result
