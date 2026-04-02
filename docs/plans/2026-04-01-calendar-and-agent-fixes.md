# Calendar & Default Agent Fixes — 2026-04-01

## Status: In Progress

---

## 1. Calendar read uses wrong calendar (hardcoded "primary")

**Problem:** `_list_calendar_events()` in `promaia/agents/agentic_turn.py:3839` hardcodes `calendarId: "primary"` instead of using `self._agent_calendar_id`. Writes via `schedule_self` correctly use the agent's dedicated calendar, but reads always hit the user's primary calendar. This means the agent can never see events it created.

**Fix:** Change line 3839 to use `self._agent_calendar_id or "primary"` as the calendar ID, matching what `_schedule_self` already does. Also consider allowing `calendar_id` as a tool input parameter so the agent can explicitly query a specific calendar.

**Files:**
- `promaia/agents/agentic_turn.py` — `_list_calendar_events()` (~line 3839)

---

## 2. Duplicate calendar creation (no dedup check)

**Problem:** `create_agent_calendar()` in `promaia/gcal/google_calendar.py:460` calls `calendars().insert()` unconditionally. It never checks whether a calendar with that name already exists. If the saved `calendar_id` is lost or the auto-creation in `agentic_adapter.py` fires again, a duplicate "maia" calendar is created.

**Fix:** Before inserting, call the existing `list_agent_calendars()` (or similar) and search by `summary` for a match. Return the existing calendar ID if found. Only create if no match.

**Files:**
- `promaia/gcal/google_calendar.py` — `create_agent_calendar()` (~line 434-468)

---

## 3. Add `maia agents reset-default` CLI command

**Problem:** There's no way to repair or recreate the default "maia" agent when it's missing or misconfigured. Mitchell's VM had no default agent at all due to loading from an older version. Koii's workspace has a duplicate calendar attached to the default agent. Both required manual intervention.

**Fix:** Add a CLI command (e.g., `maia agents reset-default`) that:
- Checks if a "maia" agent with `is_default_agent: true` exists
- If missing, creates one from the template defaults (all MCP tools, full access)
- If present but broken (no calendar, wrong tools), offers to repair it
- Handles the duplicate calendar case by consolidating to the correct one
- Auto-creates the dedicated Google Calendar if credentials exist

**Files:**
- `promaia/cli/scheduled_agent_commands.py` — new subcommand
- `promaia/agents/agent_config.py` — possible helper for default agent template
