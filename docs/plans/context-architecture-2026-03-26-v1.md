# Plan: Context Architecture — Library, Rooms, and Working Memory

## Vision

Promaia agents should manage context like a human manages a workspace. You don't read every book in your office every time you start a task. You have a library (available reference material), a desk (working memory with notes), and specialized rooms (tools for specific jobs). You visit the library when you need the big picture, work from notes at your desk, and enter rooms to use specialized tools.

## Architecture

### Three Spaces

**1. Lobby (always active)**
- Base system prompt + conversation_mode guidance
- Persistent notepad (agent's working notes, survives across turns)
- Library index (source names, page counts, maybe titles — NOT full content)
- Room directory (list of available tool rooms with descriptions)
- Core tools always loaded (~12 tools)

**2. Library (visit on demand)**
- Full loaded context from browser-selected sources
- Agent "visits" the library: full context injected into system prompt for ONE API call
- Agent reads, takes notes, then "leaves" — context removed from prompt
- Library contents never change during session (set by browser at start)
- Agent can visit as many times as needed

**3. Working Context (agent-managed)**
- Data the agent has actively pulled into its prompt via query tools
- Agent can clear specific query results when no longer needed
- Completely independent from library — agent controls what's here
- Starts empty each session. Agent fills it as needed.

### Tool Rooms (lazy-loaded tool sets)

Instead of 74 tools always present (~10k tokens), the agent starts with core tools + a room directory. It enters a room to access specialized tools.

| Room | Tools | Trigger phrases |
|------|-------|-----------------|
| **Lobby** (always) | query_sql, query_vector, query_source, write_journal, notepad, visit_library, load_tools, list_workspace_files, compact_context | — |
| **Email** | send_email, create_email_draft, reply_to_email, search_emails, get_email_thread | email, draft, send, reply |
| **Calendar** | create/update/delete/list/get calendar events, schedule_agent_event, schedule_self | calendar, schedule, meeting, event |
| **Notion** | 12 notion_* tools | notion, page, database, create page |
| **Sheets** | 8 sheets_* tools | spreadsheet, sheet, CSV, cells |
| **Drive** | drive_search/download/list_folder | drive, download, file, folder |
| **Web** | web_search, web_fetch | search the web, look up, URL |
| **Agents** | list/create/update/delete/enable/disable/rename/run agent | agent, create agent |
| **Config** | register_database, list_workspaces, add_workspace, discover_source, check_credential, test_connection, list_source_types | configure, add database, setup |
| **Workflows** | create/list/get/update/delete workflow | workflow, save as workflow |

**`load_tools` tool:**
```
load_tools(room="email")      → loads email tools, adds to available tools
load_tools(room="lobby")      → unloads all rooms, back to core only
load_tools(rooms=["email", "drive"])  → load multiple rooms
```

Room state persists across turns. Agent stays in loaded rooms until it explicitly leaves or loads different ones.

---

## New Tools

### `notepad`
Persistent working notes across turns within a conversation.

```python
{
    "name": "notepad",
    "description": "Your persistent working notes. Write key facts, plans, and references. Notes survive across turns — use them to avoid re-reading context.",
    "input_schema": {
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "append", "read", "clear"],
                "description": "write: replace all notes. append: add to existing. read: return current notes. clear: erase all."
            },
            "content": {
                "type": "string",
                "description": "Note content (for write/append actions)"
            }
        },
        "required": ["action"]
    }
}
```

Notes are injected into the system prompt each turn:
```
## Working Notes
[agent's persisted notes here]
```

### `visit_library`
Temporarily load full browser-selected context for one read-through.

```python
{
    "name": "visit_library",
    "description": "Enter the library to see ALL loaded context from the browser-selected sources. Use this when you need the big picture or your notes don't cover what the user is asking about. Take notes on what's relevant, then leave. Full context is only available for this one step.",
    "input_schema": {
        "properties": {},
        "required": []
    }
}
```

When called:
- Next API call includes full context_data_block in system prompt
- After that API call completes, context_data_block is removed again
- Agent should take notes during the visit (call notepad with findings)

### `clear_working_context`
Remove query results that have been loaded into the conversation.

```python
{
    "name": "clear_working_context",
    "description": "Clear data from previous query tool results that's no longer needed. Reduces context size. You can always re-query if you need the data again.",
    "input_schema": {
        "properties": {
            "clear_all": {
                "type": "boolean",
                "description": "Clear all query results from working context"
            }
        },
        "required": []
    }
}
```

### `load_tools`
Enter/exit tool rooms.

```python
{
    "name": "load_tools",
    "description": "Load specialized tool rooms. Check the room directory to see what's available.",
    "input_schema": {
        "properties": {
            "rooms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Room names to load (e.g., ['email', 'drive']). Pass ['lobby'] to unload all rooms."
            }
        },
        "required": ["rooms"]
    }
}
```

---

## Behavior Changes

### Browser → Library (not prompt)
- Browser-selected sources load into a `library_context` field on the session state
- NOT injected into the system prompt by default
- System prompt gets a library index instead:
  ```
  ## Library (available context)
  You have these sources loaded. Use query tools for targeted lookups,
  or visit_library to read through everything.

  - journal: 7 pages (2026-03-20 to 2026-03-26)
  - stories: 4 pages
  - gmail/koii_create: 123 pages (last 7 days)
  ```

### Context auto-management
- After the first API call of each agentic turn, if the agent hasn't compacted and context exceeds a threshold, the system could auto-suggest compaction
- But ideally the agent learns to do this naturally via the system prompt instructions

### System prompt instructions
Add to conversation_mode.md:

```
## Context Management

You have a library of loaded data and a notepad for working memory.

**Default state**: You start in the lobby with your notepad and a library index.
Your notepad persists across turns — use it to avoid re-reading context.

**When you need the big picture**: Call visit_library to see all loaded context.
Read through it, take notes on what matters, then continue working.
The full context is only visible for that one step.

**When you need specific data**: Use query_sql/query_vector/query_source.
Results appear in your working context. Clear them when done.

**When you need specialized tools**: Call load_tools with the room name.
Available rooms: email, calendar, notion, sheets, drive, web, agents, config, workflows.

**Working notes**: Use the notepad tool to maintain persistent notes.
Write down key facts, plans, and references. Notes survive across turns.
This is your primary working memory — keep it updated.

**Principle**: Load the minimum context needed for excellent work.
Read once, take notes, work from notes. Go back to the library only
when your notes don't cover what you need.
```

---

## Implementation Phases

### Phase 1: Persistent Notepad
- Add `notepad` tool to ToolExecutor
- Store notes on ToolExecutor (string field)
- Inject notes into system prompt each turn: `## Working Notes\n{notes}`
- Notes persist across turns (passed back via AgenticTurnResult, stored in chat interface context_state)
- **Test**: Write notes in one turn, verify they appear in the next turn's prompt

### Phase 2: Library Architecture
- Browser loads context into `context_state['library_context']` instead of injecting into prompt
- Build library index (source names, page counts, date ranges) for the prompt
- Add `visit_library` tool: sets a flag that includes library_context in the next API call's system prompt, then auto-removes after that call
- **Test**: Start chat with sources, verify context NOT in prompt. Call visit_library, verify full context appears for one call then disappears.

### Phase 3: Tool Rooms
- Restructure `build_tool_definitions()` to accept a `rooms` list
- Add `load_tools` tool to ToolExecutor with room state
- Lobby always loaded. Other rooms loaded on demand.
- Room state persists across turns (stored in context_state)
- Room directory in system prompt
- **Test**: Start chat, verify only ~12 tools. Call load_tools(rooms=["email"]), verify email tools appear.

### Phase 4: Working Context Management
- Track query tool results as separate items in working context
- Add `clear_working_context` tool
- Agent can clear specific query results or all
- **Test**: Query for data, verify in context. Clear, verify gone. Re-query, verify back.

---

## Files to Modify

| File | Phase | Change |
|------|-------|--------|
| `promaia/agents/agentic_turn.py` | 1,2,3,4 | Notepad on ToolExecutor, visit_library/clear_working_context handling, room-aware tool building |
| `promaia/chat/agentic_adapter.py` | 1,2,3 | Pass notepad/library/rooms through AgenticTurnResult back to interface, rebuild prompt with notes+index |
| `promaia/chat/interface.py` | 1,2,3 | Store notepad/library/rooms in context_state, pass to next agentic turn |
| `promaia/chat/agentic_adapter.py` | 2 | Change browser loading to store in library_context, build index |
| `prompts/conversation_mode.md` | 1,2,3 | Add context management instructions |

## Token Impact Estimate

| Scenario | Current | After |
|----------|---------|-------|
| Simple question (no context needed) | ~50k (full context + 74 tools) | ~5k (lobby + notes + 12 tools) |
| Multi-step workflow (5 iterations) | ~250k (50k × 5) | ~55k (50k first read + 5k × 4 compact) |
| Sprint planning (100k context) | ~500k (100k × 5 turns) | ~115k (100k visit + 3k × 5 turns from notes) |

~70-80% reduction in token usage for multi-turn conversations.
