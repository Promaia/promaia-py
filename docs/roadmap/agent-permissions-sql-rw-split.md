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

Before touching the gate we need to know exactly where an agent can
WRITE to an internal source. Likely candidates:

- [ ] Promaia internal SQLite (`hybrid_metadata.db`, `conversations.db`, `workflows.db`) — find every codepath an agent invocation can reach that mutates these tables
- [ ] Notion DB writes (create/update/delete page) — usually MCP-mediated; confirm
- [ ] Slack/Discord write APIs (post message, react, edit) — gated by `messaging_enabled` + channel groups; confirm no extra source-level gate is needed
- [ ] Filesystem sandbox writes — gated separately; confirm no overlap
- [ ] Anything else surfaced by `grep -r "INSERT\|UPDATE\|DELETE\|create_page\|update_page" promaia/` (filtered to call sites reachable from `agentic_turn`)

Output: a one-liner inventory in this file under "Audit results"
listing each call site + the source it modifies.

## Phase 1 — Schema

`SourcePermission.WRITE` is already defined in `agent_config.py:32`.
What's missing is the convention + helpers:

- [ ] `AgentConfig.can_write_source(source_name) -> bool` returning True iff the agent has a `SourceAccess` entry for that source AND the entry's `permissions` list contains `SourcePermission.WRITE`. Default false (deny).
- [ ] Document the deny-by-default shift in `agent_config.py` docstring: agents with no `source_access` entry for a source can READ if `databases` lists it (legacy), but cannot WRITE unless explicitly granted.

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

## Audit results (Phase 0 fills this in)

_(empty until Phase 0 runs)_
