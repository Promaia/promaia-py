# Agent permissions — unified external-tools picker

**Goal:** Today the agent-edit menu has two separate "tools" entries
(option 4 MCP Tools, option 9 Channel Permissions) and uses the
"databases" option for things that are really tools too (notion,
sheets). This roadmap collapses everything an agent can reach
externally into one picker with two visible sections, then routes
each pick into the right underlying gate.

The underlying gates don't change — `databases`, `source_access`,
`messaging_enabled`, `mcp_tools`, `mcp_tool_allowlist`,
`allowed_channel_groups`, etc. all stay. This is a presentation
layer + a per-tool sub-flow router.

**Branch:** `feat/agent-permissions-unified-picker` off `koii-prod`.

---

## Sketch

```
External tools — pick what this agent can reach
─────────────────────────────────────────────────
Built-in external tools
  [ ] notion         — read databases + write pages (+ optional page allowlist)
  [ ] gmail          — read inbox + send replies
  [ ] calendar       — read events / trigger on events
  [ ] google-sheets  — read sheets + write rows
  [ ] slack          — read channels + post (channel-group picker)
  [ ] discord        — read channels + post (channel-group picker)

User-added MCP servers (from mcp_servers.json)
  [ ] po-manager     — N tools available (per-tool allowlist)
  [ ] (others)
```

For each ticked item, drill into a sub-picker that maps to the right
gates. Built-in tools have heterogeneous shapes; MCP servers all use
the same per-tool flow.

---

## Phase 1 — Built-in tools registry

A canonical place that says "here are the built-in external tools we
recognise, and here's what each one needs the picker to ask the user."

- [ ] Add `promaia/cli/builtin_tools_registry.py` with one entry per built-in:
  ```
  BUILTIN_TOOLS = [
      {"id": "notion",        "label": "Notion",        "gates": ["databases", "source_access", "allowed_pages"]},
      {"id": "gmail",         "label": "Gmail",         "gates": ["databases", "source_access", "messaging_enabled"]},
      {"id": "calendar",      "label": "Calendar",      "gates": ["databases", "calendar_id"]},
      {"id": "google_sheets", "label": "Google Sheets", "gates": ["databases", "source_access"]},
      {"id": "slack",         "label": "Slack",         "gates": ["databases", "allowed_channel_groups", "allowed_output_channel_groups", "messaging_enabled"]},
      {"id": "discord",       "label": "Discord",       "gates": ["databases", "allowed_channel_groups", "allowed_output_channel_groups", "messaging_enabled"]},
  ]
  ```
- [ ] One short description string per tool that the picker can show under each row
- [ ] Helper: `is_builtin_tool(name)` — true for any name in the registry; false for everything else (which is then assumed to be an MCP server)

## Phase 2 — Top-level picker

- [ ] New helper `select_external_tools(agent)` in `agent_creation_selector.py`
- [ ] Renders the two sections sketched above using the existing `prompt_toolkit` checkbox pattern
- [ ] Pre-checks rows based on what's already configured for the agent:
  - Built-ins: ticked iff at least one of their gates is set on the agent (e.g. notion ticked iff `databases` contains a notion source OR a Notion-related `source_access` entry exists)
  - MCP servers: ticked iff in `agent.mcp_tools`
- [ ] Returns the user's top-level selection — list of tool IDs

## Phase 3 — Per-tool sub-pickers

When a tool is ticked at the top level, drill into a sub-flow.
Reuse existing pickers where possible.

- [ ] **Notion sub-flow**:
  - Pick which Notion DBs from this workspace's available sources
  - Per DB: read-only / read+write (writes `SourceAccess.permissions`)
  - Optional: scope to specific pages (forward to the Notion page picker from the page-scope roadmap when it lands)
- [ ] **Gmail sub-flow**:
  - Read-only / read+write (write = can call `reply_to_email` / `draft_reply_to_email`)
  - `messaging_enabled` flips ON if "read+write"
- [ ] **Calendar sub-flow**:
  - Pick calendar source (read access via databases)
  - Optional: dedicated `calendar_id` for triggers
- [ ] **Google Sheets sub-flow**:
  - Pick sheets from databases
  - Per sheet: read-only / read+write
- [ ] **Slack sub-flow**:
  - Reuse existing `select_channel_groups` (live channel list + wildcards)
  - Read side → `allowed_channel_groups`; write side → `allowed_output_channel_groups`; `messaging_enabled` flips ON when picking write
- [ ] **Discord sub-flow**:
  - Same shape as Slack but Discord channel API
  - Defer to "manual ID entry" if no Discord bot token configured (matches current Slack fallback)
- [ ] **MCP server sub-flow** (per ticked server):
  - Reuse existing `select_mcp_tool_allowlist` (per-tool checkbox list with Q5b refuse-to-save)

## Phase 4 — Replace existing menu options

- [ ] In `handle_agent_edit`, replace options **4 (MCP Tools)** and **9 (Channel Permissions)** with a single option **4 — External Tools**, which calls `select_external_tools` and routes through the sub-flows
- [ ] In `handle_agent_create`, replace the separate "Configure MCP tools?" and "Channel permissions" steps with the same unified picker
- [ ] Old menu options stay valid as deprecation aliases for one release? OR hard-cut on this commit (decide during implementation)

## Phase 5 — Chat-side schema

The agent-editing-agent's tools currently expose `mcp_tools`,
`allowed_channel_groups`, etc. directly. Add a higher-level shorthand
that maps to the same fields, so chat instructions like *"give this
agent slack with read-only access to #engineering"* don't require
the user to know the underlying field names.

- [ ] Add a `set_external_tool` tool to the agent-editing-agent's toolkit, taking `(tool_id, sub_settings_dict)` and dispatching to the right `update_agent` field changes underneath
- [ ] Update `agent_edit.py` and `create_agent.py` workflow prompts to point at the new tool first, with `update_agent`-direct as the fallback for power users

## Phase 6 — Tests

- [ ] Registry round-trip: every built-in id has an entry, has a label, has a non-empty gates list
- [ ] `is_builtin_tool` returns correct values for sample names
- [ ] Pre-check logic: an agent with `databases: ["notion-stories"]` has notion pre-ticked
- [ ] Sub-flow integration: each sub-flow returns the right field set without crashing on empty input
- [ ] Smoke: run `maia agent edit` on a fixture agent through the unified picker; verify all expected fields land in `agents.json`

## Phase 7 — Sign-off + ship

- [ ] User signs off after live testing on koii-prod
- [ ] Squash-merge to main → Glacier deploy
- [ ] Move this file to `archive/`

## Open design questions (decide before / during Phase 2)

- **Tool discovery for "what databases does notion / sheets have available"** — today the user runs `maia setup notion` to add DBs, then they appear in `databases`. Should the unified picker offer "add a new Notion DB" inline, or assume setup already happened?
- **Hard-cut vs deprecation aliases** for the old menu options 4 / 9 — easier to hard-cut since this is internal tooling, but could surprise existing muscle memory.
- **Calendar's "trigger" vs "data source" distinction** — calendar today doubles as an agent trigger source (`calendar_id`) and a queryable database. Should the unified picker treat them as one tool or two?
