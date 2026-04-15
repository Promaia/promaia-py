# ID-Based Lookup Migration Plan

## Why

Everything uses human-readable names for lookups. Rename anything — a database, a workspace, a channel — and things break silently. The `database_id` field is stored in every table and config but never used for lookups (except one place in files.py). This migration makes IDs the primary lookup path with names as fallback.

## Design Principles

- **Non-breaking**: Names continue to work everywhere. ID becomes primary, name becomes fallback.
- **No data migration**: Existing SQLite data stays as-is. New indexes are additive.
- **Single resolution function**: `resolve_database(name_or_id, workspace)` replaces all direct `get_database_config()` calls. Tries name first (fast, common case), then ID fallback.
- **Independently shippable phases**: Each phase can be deployed and tested alone.

---

## Phase 1: Config Layer Foundation

**Files**: `promaia/config/databases.py`, `promaia/config/workspaces.py`

### 1a. Workspace IDs

Add `workspace_id` to `WorkspaceConfig` (workspaces.py:22-33):
- Generate via `uuid.uuid5(uuid.NAMESPACE_URL, name)` — deterministic from name, no migration needed
- Persist to config on next save
- Add `get_workspace_by_id(workspace_id)` to `WorkspaceManager`

### 1b. Database ID lookup

Add to `DatabaseManager` (databases.py):
- `get_database_by_id(database_id, workspace=None)` — scans databases by `database_id` field
- Lazy `_id_index: Dict[str, DatabaseConfig]` cache, invalidated on add/remove/load
- Module-level `get_database_config_by_id(database_id, workspace=None)` convenience function

### 1c. Unified resolution function

Add `resolve_database(name_or_id, workspace=None)` (databases.py or new promaia/config/resolve.py):
```python
def resolve_database(name_or_id: str, workspace: str = None) -> Optional[DatabaseConfig]:
    """Try name first, then ID fallback."""
    config = get_database_config(name_or_id, workspace)
    if config:
        return config
    return get_database_config_by_id(name_or_id, workspace)
```

### 1d. ID detection in `get_database()`

At the end of `get_database()` (line 465-487), before returning None: if `name` looks like a UUID (`^[a-f0-9-]{36}$`) or numeric ID (`str.isdigit()`), try `get_database_by_id(name, workspace)`.

---

## Phase 2: Storage Layer Indexes

**File**: `promaia/storage/hybrid_storage.py`

Add `database_id` indexes to all content tables in `init_database()` (after line 531):

```sql
CREATE INDEX IF NOT EXISTS idx_gmail_dbid ON gmail_content (database_id)
CREATE INDEX IF NOT EXISTS idx_journal_dbid ON notion_journal (database_id)
CREATE INDEX IF NOT EXISTS idx_stories_dbid ON notion_stories (database_id)
CREATE INDEX IF NOT EXISTS idx_cms_dbid ON notion_cms (database_id)
CREATE INDEX IF NOT EXISTS idx_generic_dbid ON generic_content (database_id)
CREATE INDEX IF NOT EXISTS idx_conversation_dbid ON conversation_content (database_id)
```

Also in `_ensure_notion_table_exists()` (line 984): add database_id index for dynamically created tables.

---

## Phase 3: Dual-Path Queries

**Files**: `promaia/storage/hybrid_storage.py`, `promaia/config/registry_sync.py`

### 3a. `query_content()` accepts database_id

Add `database_id: str = None` parameter to `query_content()` (line 1609). When provided, use `WHERE database_id = ?` instead of `WHERE database_name = ?`.

### 3b. Registry sync by database_id

Change `registry_sync.py:85-88` from:
```python
registry.query_content(workspace=db_config.workspace, database_name=db_config.nickname)
```
To:
```python
registry.query_content(workspace=db_config.workspace, database_id=db_config.database_id)
```

### 3c. files.py already correct

`load_database_pages_with_filters()` at line 1318-1319 already queries by `workspace + database_id`. No change needed.

---

## Phase 4: Agent Config + Tools

**Files**: `promaia/agents/agent_config.py`, `promaia/agents/executor.py`, `promaia/agents/agentic_turn.py`, `promaia/agents/custom_tools.py`

### 4a. Agent database specs accept IDs

In `agent_config.py` `_parse_legacy_databases()` (line 136): after splitting on `:`, if `database_name` looks like a UUID, resolve via `resolve_database()`.

### 4b. Migrate tool resolution calls

Replace `get_database_config()` with `resolve_database()` in:
- `executor.py:348` — initial context loading
- `agentic_turn.py:2980` — `query_source` tool
- `custom_tools.py:65` — query tools

### 4c. Remove hardcoded names in files.py

Replace `get_database_config("journal")` and `get_database_config("cms")` (files.py:31,50) with config-driven resolution or remove these helper functions if unused.

---

## Phase 5: File Path Stability

**Files**: `promaia/storage/hybrid_storage.py`, `promaia/config/databases.py`

### 5a. Path mapping table

New table `database_path_mapping`:
```sql
CREATE TABLE IF NOT EXISTS database_path_mapping (
    database_id TEXT NOT NULL,
    workspace TEXT NOT NULL,
    current_nickname TEXT NOT NULL,
    markdown_directory TEXT NOT NULL,
    table_name TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (database_id, workspace)
)
```

Populated during sync. On nickname change, the old path gets a symlink to the new path.

### 5b. New databases use ID-based table names

For newly created databases (not existing), use `notion_{workspace}_{short_id}` where `short_id` is first 8 chars of database_id. Gate behind config flag for gradual rollout.

### 5c. Table resolution via mapping

`add_content()` routing (line 1320-1360): before creating `notion_{workspace}_{database_name}`, check `database_path_mapping` for an existing table for this `database_id`. If found, use that table name regardless of current nickname.

---

## Phase 6: Repo-Wide Call Site Migration

**~104 call sites across 32 files.**

Mechanical find-and-replace: `get_database_config(` → `resolve_database(` in all files. Since `resolve_database()` has the same signature and return type, this is safe.

Priority order by call count:
1. `promaia/cli/database_commands.py` (24 calls)
2. `promaia/agents/agentic_turn.py` (12 calls)
3. `promaia/storage/files.py` (6 calls)
4. `promaia/chat/interface.py` (5 calls)
5. `promaia/storage/chat_history.py` (4 calls)
6. All others (1-3 calls each)

---

## Implementation Order

```
Phase 1 (config foundation) ──→ Phase 2 (indexes) ──→ Phase 3 (dual queries)
         │                                                      │
         └──→ Phase 4 (agent + tools) ──────────────────────────┤
                                                                │
Phase 5 (file paths) ←─────────────────────────────────────────┘
                                                                │
Phase 6 (remaining call sites) ←────────────────────────────────┘
```

Phases 1-4 can be done in ~2 sessions. Phase 5 is the most complex (path mapping + symlinks). Phase 6 is mechanical but touches many files.

---

## What This Does NOT Change

- Existing config.json format — names still work
- User-facing tool syntax — `query_source journal:7` still works
- Existing SQLite data — no rows need updating
- Existing file paths — no files move
- Table names for existing databases — only new ones get ID-based names
