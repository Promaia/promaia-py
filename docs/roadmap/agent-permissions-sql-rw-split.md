# Agent permissions — read/write split for internal sources

**Goal:** Today the gate around any source is "can the agent query it
or not." `SourcePermission.WRITE` exists as an enum value but isn't
checked anywhere — so any source the agent can read, it can also
write to (via whatever write path exists). Add an explicit
read-only / read+write split per source so the user can grant query
access without granting modification access.

**Branch:** `feat/agent-permissions-sql-rw-split` off `koii-prod`.

---

## Phase 0 — Audit write paths

- [x] Audit complete (results below)

Findings: most internal SQLite mutations are sync-system-internal
(hybrid_storage cache updates, sync_cache pruning) — agents don't
choose to call them. The TRUE agent-elective writes are:

## Phase 1 — Schema

`SourcePermission.WRITE` is already defined in `agent_config.py:32`.
What's missing is the convention + helpers:

- [x] `AgentConfig.can_write_source(source_name) -> bool` — added 2026-04-26. Returns True iff a `SourceAccess` entry for the source exists AND its `permissions` list contains `SourcePermission.WRITE`. Default false (deny).
- [x] Docstring documents the deny-by-default shift.
- [x] 4 new tests (no source_access / query-only / write granted / other-source isolation). 35/35 total pass.

## Phase 2 — Wire enforcement

For each write path found in Phase 0:

- [ ] Insert a `if not agent.can_write_source(source): return permission-denied` check
- [ ] Use the same deny-string shape the MCP gate uses so agents adapt the same way
- [ ] Log a WARN once per agent + source pair (not per call) so the operator sees the deny in the session log

## Phase 3 — CLI picker

- [ ] In `maia agent edit` option 2 (Databases), after the user picks sources, ask "read-only or read+write?" per source
- [ ] Default: read-only (deny-by-default semantic)
- [ ] Update the `databases` ↔ `source_access` mapping so the picker writes a `SourceAccess` entry per source with the chosen permissions
- [ ] In `maia agent add`, same flow during the databases step

## Phase 4 — Chat-side schema

- [ ] Extend `update_agent` and `create_agent` tool schemas with a `source_permissions: Dict[source_name, "read" | "read+write"]` shorthand that maps to a `SourceAccess[]` entry per key
- [ ] Wire through `_update_agent` and `_create_agent` handlers
- [ ] Update workflow prompts in `agent_edit.py` / `create_agent.py` to mention the new field with concrete examples ("read-only on `gmail`, read+write on `journal`")

## Phase 5 — Tests

- [ ] Unit: `can_write_source` returns False for sources with no entry, False for entries without WRITE permission, True for entries with WRITE.
- [ ] Unit: round-trip `SourceAccess.permissions` through `to_dict` / `from_dict`
- [ ] Smoke: agent with `source_access: [{source: "journal", permissions: [QUERY]}]` tries to write to journal — gets denied.

## Phase 6 — Sign-off + ship

- [ ] User signs off after live testing on koii-prod
- [ ] Squash-merge to main → Glacier deploy
- [ ] Move this file to `archive/`

## Audit results

**Agent-elective writes that need a source-level gate:**

| Tool | Call site | Source resolution | Notes |
|---|---|---|---|
| `notion_create_page` | `agentic_turn.py:6096-6138` | takes a `database_id` directly → lookup nickname in `promaia.config.json` | Creates a new page in a Notion DB. Cleanest case. |
| `notion_update_page` | `agentic_turn.py:6142-6177` | takes `page_id`; need to resolve parent database via `client.pages.retrieve` or local cache | Updates page properties + optionally appends blocks. |
| `notion_append_blocks` | `agentic_turn.py:6290-6305` | same as above (`page_id` → parent DB) | Appends content to a page. |

**Already gated elsewhere — no source-level gate needed:**

- MCP server tool calls (`po_manager.update_vendor`, etc.) — gated by `mcp_tool_allowlist` and the per-tool runtime check. Source-level WRITE doesn't add anything.
- Slack / Discord output — gated by `messaging_enabled` + `allowed_output_channel_ids` / `allowed_output_channel_groups`.
- Filesystem sandbox writes — separate gate in `promaia.tools.sandbox`.
- Memory / notepad — agent-private state, not a "source" in the access-control sense.

**Sync-system-internal writes (not agent-elective, no gate added):**

- `hybrid_storage.py` — cache mutations during Notion sync.
- `sync_cache.py` — stale-page pruning.
- `task_queue.py`, `execution_tracker.py`, `orchestrator.py` — orchestrator bookkeeping; the agent doesn't pick to write to these, the system writes on its behalf.
- `agentic_turn.py:4924, 5357` — google_sheets cache writes during sync, not agent-driven.

**Implication for Phase 1+:** the gate hangs off the three `notion_*`
write tools. Each resolves the destination Notion database (the
"source" in `source_access` parlance) and checks
`agent.can_write_source(<source_nickname>)` before invoking the
Notion API. Source-name resolution is the only fiddly bit — for
`notion_update_page` / `notion_append_blocks` we likely cache the
mapping in `hybrid_metadata.db` already; if not, a single
`client.pages.retrieve` call gives us the parent.

