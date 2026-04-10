# ID vs Name Audit Report

## Problem

The codebase overwhelmingly uses human-readable names for lookups instead of stable IDs. If anything gets renamed — a database, a workspace, a channel — things break silently. Data becomes inaccessible, queries return empty, agents lose their sources.

## The Good (ID-based, stable)

| Component | What's Used | Where |
|-----------|------------|-------|
| Slack workspace | `database_id` (workspace ID) | slack_connector.py:55 |
| Discord server | `database_id` (server ID) | discord_connector.py:52 |
| Messaging channel | Platform channel ID | agent_config.py:78-82 |
| Slack/Discord channel filter | `slack_channel_id` / `discord_channel_id` | executor.py:357-359 |
| Notion page IDs | `page_id` throughout | hybrid_storage.py, files.py |

## The Bad (name-based, fragile)

### 1. Database Config Lookups

**`get_database_config(name, workspace)`** in `promaia/config/databases.py:465-487`

Every caller passes a human-readable name like `"journal"`, `"slack"`, `"cms"`. The `database_id` field exists on every config but is **never used for lookups** (except one Discord-specific method).

There's even a `get_stable_identifier()` method (line 141-145) that **nobody calls**.

Callers passing names:
- `executor.py:348` — agent database loading
- `agentic_turn.py:2980` — `query_source` tool
- `custom_tools.py:65` — query tools
- `files.py:31,50` — **hardcoded** `"journal"` and `"cms"`
- `slack_bot.py:677` — our new channel context code
- `unified_query.py:48` — content queries

### 2. Workspace Lookups

**No workspace ID exists at all.** `WorkspaceConfig` in `promaia/config/workspaces.py:18-68` only has a `name` field. Every workspace lookup is by name. If "koii" is renamed to "koii-labs", everything breaks.

### 3. Agent Database Specs

Agents store databases as name strings: `["journal:7", "gmail:7", "stories:all"]` in `agent_config.py:35-36`. No ID binding. Rename the database, the agent can't find it.

### 4. Hybrid Storage SQL Queries

The `unified_content` view and all SQL queries filter by `database_name` and `workspace` (both names):

```sql
WHERE workspace = ? AND database_name = ?
```

`database_id` is stored in every row but **not indexed and not used for queries**. Lines 1609-1646 in `hybrid_storage.py`.

If you rename a database, 100 pages of historical content become invisible — the data is there, but queries filter by the new name and find nothing.

### 5. File Paths

Markdown files are stored at paths like `data/md/notion/koii/journal/`. The path includes both workspace name and database nickname. Rename either one and new files go to a new path while old files stay at the old path.

### 6. Registry Sync

`promaia/config/registry_sync.py:86-87` queries by `database_name=db_config.nickname`. Rename breaks sync — it can't find existing entries to update.

## Rename Impact Matrix

| What Gets Renamed | Impact | What Breaks |
|-------------------|--------|-------------|
| Database nickname | CRITICAL | All `get_database_config()` calls, SQL queries, agent sources, file paths |
| Workspace name | CRITICAL | All workspace-scoped queries, file paths, agent configs |
| Slack/Discord channel name | MODERATE | Name-based display filters (ID-based filters still work) |
| Notion database title | NONE | We use Notion's UUID as `database_id`, not the title |

## What Needs to Change

### Phase 1: Add lookup-by-ID (non-breaking)

1. **`databases.py`** — add `get_database_by_id(database_id)` method that looks up by `database_id` instead of name. Keep existing name-based lookup as fallback.

2. **`workspaces.py`** — add a `workspace_id` field to `WorkspaceConfig`. Generate a stable UUID on first creation. Add `get_workspace_by_id()`.

3. **`hybrid_storage.py`** — add index on `database_id` column in all tables. Migrate queries to prefer `database_id` over `database_name`.

### Phase 2: Migrate references to IDs

4. **Agent configs** — store database specs as `["DB_UUID:7", ...]` instead of `["journal:7", ...]`. Keep name-based parsing as fallback for backwards compat.

5. **`files.py`** — remove hardcoded `"journal"` and `"cms"` lookups. Use database_id or at minimum go through a config-driven resolution.

6. **Registry sync** — query by `database_id` instead of `database_name`.

7. **File paths** — use `database_id` in the directory structure, or maintain a mapping file that survives renames.

### Phase 3: query_source and tools

8. **`query_source` tool** — accept both names and IDs. Try ID first, fall back to name. This way existing prompts work but new ones can use IDs.

9. **Channel context loading** (our new code) — already uses `get_database_config` which will benefit from the ID-based fallback in Phase 1.

## Files to Modify

| File | Changes |
|------|---------|
| `promaia/config/databases.py` | Add `get_database_by_id()`, make `get_database_config()` try ID first |
| `promaia/config/workspaces.py` | Add `workspace_id` field, `get_workspace_by_id()` |
| `promaia/storage/hybrid_storage.py` | Add `database_id` indexes, migrate queries |
| `promaia/storage/files.py` | Remove hardcoded "journal"/"cms", use config |
| `promaia/storage/unified_query.py` | Query by `database_id` instead of `database_name` |
| `promaia/agents/agent_config.py` | Support ID-based database specs |
| `promaia/agents/executor.py` | Use ID-based lookup with name fallback |
| `promaia/agents/agentic_turn.py` | `query_source` accepts IDs |
| `promaia/config/registry_sync.py` | Sync by `database_id` |
