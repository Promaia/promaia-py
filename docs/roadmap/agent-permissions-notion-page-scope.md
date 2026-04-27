# Agent permissions — Notion page-level scope

**Goal:** Today an agent with `databases: ["stories"]` sees every page in
the stories database. Add per-page allowlisting so agents can be scoped
to a hand-picked set of pages within a Notion DB.

**Pattern reference:** `maia setup notion` already calls `notion.com/v1/
search` and `databases/{id}/query` to fetch real pages, then renders a
multi-select. Mirror that UX here.

**Branch:** new branch off `koii-prod` (call it
`feat/agent-permissions-notion-pages`).

---

## Phase 1 — Schema

- [ ] Add `SourceAccess.allowed_pages: Optional[List[str]] = None` (Notion page IDs)
- [ ] Add `AgentConfig.get_allowed_pages(source_name) -> Optional[List[str]]` helper
- [ ] Add `AgentConfig.filter_pages_by_id(source_name, pages)` helper that drops pages whose `id` isn't in the allow list. None = legacy allow-all (no filter applied).

## Phase 2 — Runtime enforcement

- [ ] Wire `filter_pages_by_id` into `_handle_query_source` after table+column filters
- [ ] Wire same into `_handle_query_vector` per source key
- [ ] Add a deny log line when pages are filtered out (not WARN, INFO — this is normal scoping)

## Phase 3 — CLI picker

- [ ] In `maia agent edit` add a new sub-flow on the existing "Databases" option (option 2): for each Notion source picked, ask "scope to specific pages?" yes/no
- [ ] If yes: live-fetch pages via `notion.com/v1/databases/{id}/query`, paginate, sort by title
- [ ] Multi-select using the same checkbox UI as `_browse_notion_databases`
- [ ] Pre-check pages already in `allowed_pages` so re-edits stay painless
- [ ] Pages without titles fall back to first 40 chars of any rich-text property + `(<id-prefix>)`

## Phase 4 — Chat-side schema

- [ ] Extend `update_agent` tool schema with `allowed_pages: Dict[source_name, List[page_id]]`
- [ ] Extend `create_agent` tool schema the same way
- [ ] Wire through `_update_agent` and `_create_agent` handlers
- [ ] Update `agent_edit.py` and `create_agent.py` workflow prompts to mention per-page scoping when the source is Notion

## Phase 5 — Tests

- [ ] Unit: `filter_pages_by_id` (no access, drop unlisted, pass through when None)
- [ ] Unit: round-trip `allowed_pages` through `to_dict` / `from_dict`
- [ ] Smoke: agent with `allowed_pages: {"stories": ["<id-1>"]}` queries stories — sees only that page

## Phase 6 — Sign-off + ship

- [ ] User signs off after live testing on koii-prod
- [ ] Squash-merge to main → Glacier deploy
- [ ] Move this file to `archive/`

## Out of scope (move to `future.md` if relevant)

- Page-level READ vs WRITE distinction (the SQL R/W roadmap covers the principle; if it's worth doing for Notion too, fold in then).
- Auto-discover pages by tag / property filter (e.g. "all stories with status=Open"). Static IDs first, dynamic later if needed.
