# Agent permissions — unified external-tools picker

**Goal:** One picker, one screen, for every external thing an agent can
reach. Replaces the agent-edit menu's separate "MCP Tools" (option 4)
and "Channel Permissions" (option 9) entries with a single
"External tools and MCP" entry that opens an **inline tree**:
top-level rows for each tool, expandable with L/R arrows to reveal
per-tool children (Notion DBs with R/W matrix, Slack channels with
R/W matrix, MCP tool lists, etc).

The underlying gates don't change — `databases`, `source_access`,
`mcp_tools`, `mcp_tool_allowlist`, `allowed_channel_groups`,
`allowed_output_channel_groups`, `messaging_enabled` all stay as
storage. The picker just routes settings into the right field per
tool type.

**Implementation reference:** `promaia/cli/workspace_browser.py`
already does multi-column row rendering with TAB to switch columns.
Extend that pattern with L/R to expand/collapse children.

**Branch:** `feat/agent-permissions-unified-picker` off `koii-prod`.

---

## Mockup

```
External tools and MCP — agent: my-agent
─────────────────────────────────────────────────────
                                              R     W

Built-in external tools
[v] notion                                                ← expanded
        ✱ Any DB                              [ ]   [ ]
        stories                               [x]   [x]
        journal                               [x]   [ ]
        team-journal                          [ ]   [ ]
[ ] gmail                                     [ ]   [ ]
[ ] calendar                                  [ ]   [ ]
[ ] google-sheets                                         ← collapsed
[v] slack                                                 ← expanded
        ✱ Any DM                              [ ]   [ ]
        ✱ Any non-DM channel                  [ ]   [ ]
        #engineering                          [x]   [x]
        #design                               [x]   [ ]
        #announcements                        [ ]   [ ]
[ ] discord

User-added MCP servers
[v] po-manager                                            ← expanded
        list_vendors                          [x]
        list_parts                            [x]
        create_vendor                         [ ]
        delete_vendor                         [ ]
        ...
─────────────────────────────────────────────────────
↑↓ row   L/R expand/collapse   SPACE toggle   TAB column   ENTER save   ESC cancel
```

Notes on the matrix:
- Built-ins with sources (notion / sheets / slack / discord / calendar)
  use the dual R/W matrix. R column toggles `databases` /
  `source_access[].permissions=QUERY` / `allowed_channel_groups`.
  W column toggles `source_access[].permissions=WRITE` /
  `allowed_output_channel_groups` / `messaging_enabled` (gmail).
- Gmail and calendar (no per-resource sub-list) collapse to a single
  row with R/W columns directly on the tool row.
- MCP servers have a single ALLOWED column (not R/W) — the per-tool
  semantics are "can call this tool or not".

---

## Resolved design decisions

- **UX style:** inline tree (one screen, L/R expand/collapse), NOT
  sequential drill-down. Power-user editing is faster; whole agent
  config visible at once.
- **Tool discovery / inline DB add:** assume `maia setup notion`
  already ran. The picker reads from existing configured sources;
  no inline DB-add inside the picker.
- **Old menu options 4 / 9:** hard-cut. Disappear in this commit.
- **Calendar trigger vs tool:** two separate things.
  - The agent's per-agent trigger calendar (`calendar_id`) stays on
    menu option 8 (Calendar Settings). Not in the picker.
  - Calendar-as-a-tool (read/write events via tool calls) IS in the
    picker as the `calendar` built-in, with R/W matrix.
- **Notion per-page R/W:** out of scope for this picker. Deferred
  to the (existing) `agent-permissions-notion-page-scope.md`
  roadmap, which is itself deferred until post-pilot. The picker
  stops at database-level R/W for Notion.

---

## Phase 1 — Built-in tools registry

A canonical place that maps each built-in external tool name to its
gates, label, and "shape" (single row vs sub-list).

- [x] Added `promaia/cli/builtin_tools_registry.py` with one entry per built-in:
  ```python
  BUILTIN_TOOLS = [
      # tool_id        label              shape          gates
      ("notion",        "Notion",          "sublist",      ["databases", "source_access"]),
      ("gmail",         "Gmail",           "single_row",   ["databases", "source_access", "messaging_enabled"]),
      ("calendar",      "Calendar",        "single_row",   ["databases", "source_access"]),
      ("google_sheets", "Google Sheets",   "sublist",      ["databases", "source_access"]),
      ("slack",         "Slack",           "channel_sublist", ["databases", "allowed_channel_groups", "allowed_output_channel_groups", "messaging_enabled"]),
      ("discord",       "Discord",         "channel_sublist", ["databases", "allowed_channel_groups", "allowed_output_channel_groups", "messaging_enabled"]),
  ]
  ```
- [x] Helpers: `is_builtin_tool(name)`, `get_tool_shape(name)`, `get_tool_label(name)`, `get_builtin_tool(name)`
- [x] Tests: registry round-trip, expected-set, helpers, invalid-shape rejection (40/40 passing total)

## Phase 2 — Inline tree picker (the screen)

- [ ] New helper `select_external_tools(agent, workspace)` in `agent_creation_selector.py`
- [ ] Reuse `workspace_browser`'s multi-column row pattern; add expand/collapse state per top-level row
- [ ] Top-level rows: read pre-state from agent's existing config
  - Built-ins: row pre-checked iff any of their gates are non-default for this agent
  - MCP servers: row pre-checked iff in `agent.mcp_tools`
- [ ] Children rendered when expanded, hidden when collapsed
  - Lazy-load: don't fetch live data (Slack channels, MCP tools) until the row is expanded the first time
  - Show "Fetching..." inline placeholder during the load
- [ ] Keybindings: `↑↓ row`, `L/R expand/collapse`, `SPACE toggle current cell`, `TAB column (R↔W)`, `ENTER save`, `ESC cancel`

## Phase 3 — Per-tool routing

When the picker saves, route each tool's state into the right
underlying field. This is mostly bookkeeping but the per-tool logic
is what determines correctness.

- [ ] **Notion**: source list with R/W matrix → `databases` (read rows) + `SourceAccess` entries with `permissions=[QUERY]` or `[QUERY, WRITE]`
- [ ] **Gmail single-row**: R/W → `databases` includes "gmail" + `source_access[gmail].permissions` reflects R/W; W also flips `messaging_enabled=True`
- [ ] **Calendar single-row**: R/W → same shape as Gmail (databases + source_access)
- [ ] **Google Sheets sublist**: per-spreadsheet R/W matrix → `databases` + `SourceAccess` per sheet
- [ ] **Slack channel sublist**: live-fetched channels + R/W matrix → `allowed_channel_groups` (R column) + `allowed_output_channel_groups` (W column); W on any row flips `messaging_enabled=True`
- [ ] **Discord channel sublist**: same as Slack but Discord API; falls back to wildcard-only if no Discord bot token configured
- [ ] **MCP server tool sublist**: per-tool ALLOWED column (no R/W split — per-tool allow/deny only) → `mcp_tool_allowlist[server]`

## Phase 4 — Replace the old menu options

- [ ] In `handle_agent_edit`: replace options **4** and **9** with a single **4 — External tools and MCP** that calls `select_external_tools`
- [ ] In `handle_agent_create`: replace the separate "Configure MCP tools?" prompt and the channel-permissions step with the same unified picker
- [ ] Hard-cut: old options literally disappear from the menu. No deprecation alias.
- [ ] Update help text / `maia agent --help` if any of it lists the old option numbers

## Phase 5 — Chat-side schema

- [ ] Add a `set_external_tools` tool to the agent-editing-agent's toolkit, taking `(tool_id, sub_settings_dict)` and dispatching to the right `update_agent` field changes underneath
- [ ] Update `agent_edit.py` and `create_agent.py` workflow prompts to point at the new tool first; keep `update_agent` direct as the fallback for power users
- [ ] Concrete example in the prompt: "give bondu read-only on stories, read+write on journal" → maia calls `set_external_tools` with the right per-DB R/W

## Phase 6 — Tests

- [ ] Registry: every entry valid, no missing fields, helpers return right values
- [ ] Pre-check: an agent with `databases: ["notion-stories"]` and `source_access: [{source: "notion-stories", permissions: [QUERY]}]` shows notion as expanded with stories=R only
- [ ] Per-tool routing: each shape's save path produces the expected `agents.json` diff
- [ ] Smoke: run `maia agent edit` on a fixture agent, walk the picker via prompt_toolkit's testing harness, verify all expected fields land

## Phase 7 — Sign-off + ship

- [ ] User signs off after live testing on koii-prod
- [ ] Squash-merge to main → Glacier deploy
- [ ] Move this file to `archive/`
