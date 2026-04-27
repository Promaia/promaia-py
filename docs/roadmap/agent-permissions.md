# Granular agent permissions

**Branch:** `feat/agent-permissions-granular` (cherry-picked to `koii-prod` for live testing).
**Plan + research:**
- `docs/plans/2026-04-26-agent-permissions-gap-fills-plan.md`
- `docs/plans/2026-04-26-agent-permissions-research.md`

Goal: every category of resource access an agent can perform — query
tools (source / vector / sql), MCP tools, messaging output — has an
enforcement gate driven by an explicit allowlist on `AgentConfig`.
Default posture is deny-by-default for non-default agents
(`is_default_agent=False`).

---

## Phase 1 — Schema + runtime gates

- [x] `AgentConfig.mcp_tool_allowlist` (Dict[server, [tools]])
- [x] `AgentConfig.allowed_output_channel_ids` (separate from input gate)
- [x] `AgentConfig.allowed_channel_groups` (DM / channel buckets + wildcards)
- [x] `AgentConfig.allowed_output_channel_groups`
- [x] `SourceAccess.allowed_tables`
- [x] `SourceAccess.allowed_columns`
- [x] `promaia.agents.mcp_tool_cache` module (refresh / load / save / diff + schema fingerprint)
- [x] Per-tool MCP gate at runtime — `_check_mcp_tool_allowed` in `agentic_turn.py`
- [x] Run-start MCP new-tool diff (logs WARN against cached "last-reviewed" snapshot)
- [x] Per-source `allowed_tables` filter in `query_source`
- [x] Per-source `allowed_tables` filter in `query_vector`
- [x] Per-source `allowed_columns` redaction in `query_source`
- [x] Per-source `allowed_columns` redaction in `query_vector`
- [x] `can_access_channel` and `can_post_to_channel` resolvers (flat list → groups → legacy-allow)
- [x] `is_default_agent` uniqueness validation in `save_agent` (Q7)

## Phase 2 — CLI pickers

- [x] `select_mcp_tool_allowlist` — checkbox UI per server.tool, Q5b refuse-to-save
- [x] Wired into `maia agent add`
- [x] Wired into `maia agent edit` (option 4 — MCP Tools)
- [x] `select_channel_groups` — DM/channel buckets + wildcards
- [x] Wired into `maia agent edit` (option 9 — Channel Permissions)
- [x] Built-in integrations (`gmail`, `calendar`) skipped from MCP picker

## Phase 3 — Tests

- [x] `test_agent_permissions_gates.py` covers all gates + uniqueness + cache diff (31/31 pass)

## Phase 4 — Real channel list in CLI picker

The current `select_channel_groups` picker takes user-typed channel
IDs. The existing `_browse_slack_channels` (in `cli/setup_commands.py`)
calls Slack's `conversations.list` API to fetch real `(id, name)`
tuples and shows a checkbox UI. Reuse that pattern so the agent-edit
picker shows real channel names instead of asking for opaque IDs.

- [x] Refactor `select_channel_groups` to live-fetch channels via Slack API for each bucket
- [x] Wildcards (`* DMs`, `* channels`) stay as toggles ABOVE the channel checkbox list (separator-divided in the same prompt_toolkit picker)
- [x] Single-pane UX: wildcards + real channels in one checkbox list
- [ ] Discord: same pattern when bot token is available; else fall back to channel-free wildcard-only (deferred — slack bot not always present locally either; current code falls back gracefully)
- [ ] Manual smoke test: `maia agent edit <name>` → option 9 → see real channels with names

## Phase 5 — End-to-end deny test

Today: 31 unit tests prove the GATE FUNCTION returns the right answer.
Nothing exercises the full path "agent calls a tool → MCP layer → gate
fires → agent sees deny string → adapts."

- [ ] Build a fixture agent with deliberately-restricted `mcp_tool_allowlist` (e.g. po-manager.list_vendors only)
- [ ] Run it via `maia agent run-scheduled` (or equivalent) with a prompt that would call `po-manager.delete_vendor`
- [ ] Verify the runtime gate fires (WARN log line, deny string in tool result)
- [ ] Verify the agent reads the deny string and stops (doesn't loop on it)
- [ ] Same end-to-end test for the channel output gate

## Phase 6 — Chat-side interview surface

The agent-editing-agent in `chat/workflows/` walks the user through
agent edits via conversation. Today its `update_agent` tool only
knows about `allowed_channel_ids` and `mcp_tools`. Needs to learn
the new fields.

- [ ] Extend the chat-side `update_agent` tool schema with `mcp_tool_allowlist`, `allowed_channel_groups`, `allowed_output_channel_groups`, `allowed_output_channel_ids`
- [ ] Update `agent_edit.py` workflow prompt to mention the new fields and walk the user through them
- [ ] Update `create_agent.py` workflow prompt the same way
- [ ] Manual smoke test: `maia chat` → "edit my bondu agent so it can only post in DMs" → verify the right fields land in `agents.json`

## Phase 7 — Sign-off + ship to main

- [ ] User signs off after live testing on `koii-prod`
- [ ] Squash-merge `feat/agent-permissions-granular` → `main`
- [ ] Push to `main` triggers `deploy-pilots.yml` → Glacier rebuilds with the new code
- [ ] Move this file to `archive/2026-XX-XX-agent-permissions.md` once deployed

## Out of scope (moved to `future.md` if relevant)

- MCP tool fingerprint enforcement (Q5d) — for Rose, when we adopt external MCP servers.
- Public/private/thread channel distinctions — needs richer Slack metadata; not blocking anyone today.
- Multi-table SQL source — schema is ready (`allowed_tables`), no caller exists yet. When the caller lands the gate fires automatically.
