# Planning & Action Modes

## Problem

Promaia's agentic loop loads all 74 tool definitions into every API call. This:
- Burns tokens on tool schemas the model isn't using
- Overwhelms the model with options during planning/knowledge work
- Makes it harder to follow context management instructions (tool definitions compete for attention)
- Sonnet won't proactively manage context (take notes, hide shelves) because it's focused on answering + tool options

## Concept

Two modes — rooms of a house. The notepad is the only thing carried between them.

```
🏠 The House

📚 Library (planning room)
   - Shelves of context (ON/OFF)
   - Notepad
   - Query tools (source, vector, sql)
   - Tool suite INDEX (names + one-liners, no schemas)

🔧 Workshop (action room)
   - Notepad (carried from library)
   - Conversation history
   - Loaded tool suites (full schemas)
   - Tool results from current session
   - NO shelves, NO query tools
```

## Library (Planning Mode)

The model starts here. It has access to:

- **Library shelves** — toggle ON/OFF, read context, manage what's loaded
- **Notepad** — persistent working notes, always visible
- **Query tools** — query_source, query_vector, query_sql for gathering context
- **Tool suite index** — a compact list of available tool suites with descriptions, NO full schemas

The model's job in this mode:
1. Understand what the user needs
2. Gather context (load shelves, run queries)
3. Take notes on what matters
4. Form a plan for what actions to take
5. Decide which tool suites it needs
6. Enter the workshop

## Workshop (Action Mode)

The model enters explicitly by calling `enter_workshop(suites=["notion", "google"])`. When it enters:

- **Shelves are deterministically forced OFF** (not relying on model behavior)
- **Query tools are removed** (can't search, must have planned already)
- **Tool suite schemas are loaded** for the requested suites
- **Notepad persists** (the only thing carried between rooms)
- **Conversation history persists**
- **Tool results are visible** (sheets data, Notion pages, API responses from this session)

The model's job in this mode:
1. Execute the planned actions using loaded tools
2. Take notes on results (IDs, confirmations, errors)
3. Exit back to the library when done

## Tool Suites

Tools are loaded as **suites**, not individual tools. Examples:

| Suite | Tools |
|-------|-------|
| `notion` | notion_search, notion_create_page, notion_update_page, notion_query_database, notion_get_blocks, notion_append_blocks, notion_get_page, notion_update_blocks |
| `google` | send_email, create_email_draft, reply_to_email, create_calendar_event, update_calendar_event, delete_calendar_event, sheets_find, sheets_ingest, sheets_read_range, sheets_update_cells, sheets_append_rows, sheets_insert_rows, sheets_create_spreadsheet, sheets_manage_sheets, sheets_format_cells |
| `po-manager` | All mcp__po-manager__* tools |
| `web` | web_search, web_fetch |
| `workspace` | library, notepad, write_journal |

The model can load up to ~2 suites at a time to keep context manageable.

## Why This Works

### Structural enforcement > prompt instructions

We've tried telling Sonnet to proactively manage context (take notes, hide shelves). It understands the concept perfectly but doesn't do it. By structurally separating planning from action:
- The model MUST take notes before entering the workshop (shelves are forced off)
- The model CAN'T do redundant searches during execution (query tools removed)
- The model isn't overwhelmed by 74 tools while thinking about what to do

### Query tools are planning tools, not action tools

When you're searching for context (vector search, SQL query, loading a source), you're still figuring out what to do. By the time you're executing (sending an email, updating a Notion block), you should already know what you need.

### Token savings

Instead of 74 tool schemas in every API call (~15k+ tokens), the library mode has a compact index (~500 tokens) and the workshop loads only the relevant suite (~2-5k tokens). Over a multi-turn conversation with multiple iterations per turn, this is massive.

## Mode Switching

**Library → Workshop:** Model calls `enter_workshop(suites=["notion", "google"])`. System deterministically:
1. Forces all shelves OFF
2. Removes query tool definitions
3. Loads requested suite schemas
4. Continues the agentic loop in workshop mode

**Workshop → Library:** Model calls `exit_workshop()` or the system auto-exits when no tool calls are made. System:
1. Removes tool suite schemas
2. Restores query tool definitions
3. Shelves remain OFF (model must explicitly turn them back on if needed)
4. Continues in library mode

## Open Questions

- Should the model be able to switch suites without exiting? (e.g., swap notion for google mid-workshop)
- Should there be a max iterations in workshop mode before forcing an exit?
- How does this interact with the planning system (_generate_plan)?
- Should the plan itself declare which suites each step needs?

## Implementation Notes

Key files:
- `promaia/agents/agentic_turn.py` — the main loop, tool definitions, tool executor
- `promaia/chat/agentic_adapter.py` — prompt assembly, shelf management
- `prompts/conversation_mode.md` — instructions for the model

The tool suite index would be built from `build_tool_definitions()` output, grouped by suite. The mode switch would happen inside the agentic loop iteration, changing which tools and system prompt sections are active.
