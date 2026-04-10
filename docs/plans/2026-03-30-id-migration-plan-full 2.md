# Full ID Migration — No Internal Name-Based Lookups

## Context

Everything in the codebase uses human-readable names (nicknames, workspace names) for internal lookups. `database_id` is stored everywhere but never queried. Names are mutable — renaming anything breaks lookups, SQL queries, file paths, agent configs. This is bad practice. We're ripping it out entirely.

**Rule: Names are for display only. IDs are for lookup, storage, and internal references.**

---

## Phase 1: Config Layer — ID-Only Lookups

### 1a. `databases.py` — Replace name lookups with ID lookups

**File**: `promaia/config/databases.py`

**Replace `get_database()` (line 465-487)**:
- Primary: look up by `database_id` across `self.databases.values()`
- Build `_id_index: Dict[str, DatabaseConfig]` on load, keyed by `database_id`
- New signature: `get_database(identifier: str, workspace: str = None)` — `identifier` can be a database_id or legacy name
- Internal logic: check `_id_index` first, then fall back to name-based for migration period only (logged as deprecation warning)

**New method: `get_database_by_id(database_id: str) -> Optional[DatabaseConfig]`**:
- O(1) lookup from `_id_index`
- This becomes the canonical internal lookup

**Config file keys**: Currently `"koii.cms"`. Change to use `database_id` as keys:
```json
{
  "databases": {
    "10dd1339-6967-807a-b987-c92a4d29b9b8": {
      "source_type": "notion",
      "nickname": "cms",
      "workspace": "koii",
      ...
    }
  }
}
```
Write a migration function that transforms existing configs on load (rename keys from `workspace.nickname` to `database_id`). Persist on next save.

### 1b. `workspaces.py` — Add workspace_id

**File**: `promaia/config/workspaces.py`

- Add `workspace_id: str` to `WorkspaceConfig` — generate `uuid.uuid5(uuid.NAMESPACE_URL, name)` deterministically if not present
- Persist to config on save
- Add `get_workspace_by_id()` to `WorkspaceManager`
- Internal code uses `workspace_id`, display code uses `name`

### 1c. Config file migration function

**New function in `databases.py`**:
```python
def migrate_config_keys_to_ids(config: dict) -> dict:
    """One-time migration: rename database keys from workspace.nickname to database_id."""
```
- Called on `load_config()` — transforms in-memory, persists on next `save_config()`
- Maps old key → database_id for each entry
- Logs each migration

---

## Phase 2: Storage Layer — Query by database_id Only

### 2a. SQL indexes on database_id

**File**: `promaia/storage/hybrid_storage.py`

Add `CREATE INDEX IF NOT EXISTS` on `database_id` for all tables:
- `gmail_content`, `notion_journal`, `notion_stories`, `notion_cms`, `generic_content`, `conversation_content`
- Also in `_ensure_notion_table_exists()` for dynamic tables

### 2b. All SQL queries use database_id

**File**: `promaia/storage/hybrid_storage.py`

- `query_content()` (line 1609): change `WHERE database_name = ?` → `WHERE database_id = ?`
- Remove `database_name` parameter from `query_content()`, replace with `database_id`
- `add_content()` routing (line 1320): keep `database_name` for table routing (table names are hard to change), but all SELECT queries use `database_id`

### 2c. `files.py` — already correct

`load_database_pages_with_filters()` (line 1318-1319) already queries by `workspace + database_id`. No change needed.

### 2d. `unified_content` view — no change needed

View already includes `database_id`. Queries against the view just need to use it.

### 2e. Registry sync by database_id

**File**: `promaia/config/registry_sync.py` (line 85-88)

Change:
```python
registry.query_content(workspace=..., database_name=db_config.nickname)
```
To:
```python
registry.query_content(workspace=..., database_id=db_config.database_id)
```

---

## Phase 3: Agent Config — SourceAccess with database_id

### 3a. SourceAccess uses database_id

**File**: `promaia/agents/agent_config.py`

`SourceAccess.source_name` (line 22) → rename to `source_id` and store `database_id` values:
```python
@dataclass
class SourceAccess:
    source_id: str                 # database_id (e.g., "10dd1339-6967-807a-...")
    initial_days: Optional[int]
    permissions: List[SourcePermission]
    max_query_days: Optional[int] = None
```

### 3b. Migrate legacy `databases` list on load

In `AgentConfig.__init__` or a `_migrate_legacy_databases()` method:
- If `databases` list is populated and `source_access` is None:
  - For each `"name:days"` entry, resolve name → `DatabaseConfig` → `database_id`
  - Build `SourceAccess(source_id=database_id, initial_days=days, ...)`
  - Store in `source_access`
  - Clear `databases` list
  - Persist on next config save

### 3c. `get_initial_context_sources()` uses database_id

Update the method to return `database_id` values, not names. Callers then use `get_database_by_id()` to get the full config.

### 3d. `executor.py` — load context by database_id

**File**: `promaia/agents/executor.py` (line 330-366)

`_load_initial_context()`: instead of parsing `"journal:7"` and calling `get_database_config("journal")`, iterate `agent.source_access` and call `get_database_by_id(source.source_id)`.

---

## Phase 4: Tools — Accept IDs, Resolve Internally

### 4a. `query_source` tool

**File**: `promaia/agents/agentic_turn.py` (line 2967-3001)

The `database` parameter from the agent can be a name (user-friendly) or ID. Internally:
```python
db_config = get_database_by_id(database)
if not db_config:
    # Legacy fallback — agent might use display name
    db_config = get_database_config(database, self.workspace)
```

Update the tool description to tell agents they can use IDs.

### 4b. `custom_tools.py` query tools

Same pattern — `get_database_by_id()` first, name fallback.

### 4c. `unified_query.py`

**File**: `promaia/storage/unified_query.py` (line 48)

`query_content_for_chat()` `sources` parameter: resolve each source to `database_id` via config lookup, then query SQL by `database_id`.

---

## Phase 5: File Paths & Table Names

### 5a. Path mapping table

**File**: `promaia/storage/hybrid_storage.py`

New table:
```sql
CREATE TABLE IF NOT EXISTS database_path_mapping (
    database_id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    nickname TEXT NOT NULL,
    markdown_directory TEXT NOT NULL,
    table_name TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

Populated on every sync. When a nickname changes, the old path stays valid because lookup goes through `database_id` → `database_path_mapping` → `table_name`.

### 5b. Content routing uses mapping

`add_content()` (line 1320): look up `database_path_mapping` by `database_id` to find the correct table name, rather than constructing `notion_{workspace}_{database_name}`.

### 5c. New databases get ID-based paths

For newly created databases, use `data/md/{source_type}/{workspace}/{database_id[:8]}/` instead of `data/md/{source_type}/{workspace}/{nickname}/`. Existing paths stay as-is (mapping table handles them).

### 5d. Hardcoded names in files.py

Remove `get_journal_directory()` and `get_cms_directory()` (files.py:31,50) — these are hardcoded to `"journal"` and `"cms"`. Replace with config-driven lookups by database_id, or remove if unused.

---

## Phase 6: All Remaining Call Sites

~104 call sites across 32 files use `get_database_config()`. Mechanical migration:

1. **Replace** `get_database_config(name, workspace)` → `get_database_by_id(id)` where the caller has access to a database_id
2. **Where only a name is available** (CLI user input, tool parameters): resolve once at the boundary, then pass `database_id` internally
3. **Priority files** (by call count):
   - `promaia/cli/database_commands.py` (24 calls) — CLI boundary, names acceptable at input
   - `promaia/agents/agentic_turn.py` (12 calls) — covered in Phase 4
   - `promaia/storage/files.py` (6 calls) — already uses database_id for main query
   - `promaia/chat/interface.py` (5 calls) — user-facing boundary
   - Remaining 20+ files (1-3 calls each)

**Boundary rule**: Names are accepted ONLY at system boundaries (CLI input, tool parameters, user messages). Everything after the boundary uses IDs.

---

## Phase 7: Channel Context Fix (from today's bug)

**File**: `promaia/messaging/slack_bot.py` (line 668-695)

The channel context loading we built today uses `get_database_config("slack")` — migrate to `get_database_by_id(slack_workspace_id)`. The Slack workspace ID (`T0ABK58RQFN`) is already stored as `database_id` in the config.

Similarly, filtering by `channel_name` in the page metadata should use `slack_channel_id` instead.

---

## Migration Script

A one-time migration script that:
1. Reads existing `promaia.config.json`
2. Re-keys `databases` dict from `workspace.nickname` to `database_id`
3. Transforms agent `databases` lists to `source_access` with `database_id` values
4. Adds `workspace_id` to each workspace
5. Populates `database_path_mapping` table from current state
6. Writes updated config

Run automatically on startup (idempotent — detects if already migrated).

---

## Implementation Order

```
Phase 1 (config IDs) → Phase 2 (storage indexes + queries)
       ↓                        ↓
Phase 3 (agent config)   Phase 5 (file paths)
       ↓
Phase 4 (tools)
       ↓
Phase 6 (remaining call sites)
       ↓
Phase 7 (channel context fix)
```

Phase 1 + 2 are the foundation. Everything else follows.

---

## Files to Modify

| File | Phase | What Changes |
|------|-------|-------------|
| `promaia/config/databases.py` | 1 | ID index, `get_database_by_id()`, config key migration |
| `promaia/config/workspaces.py` | 1 | Add `workspace_id` |
| `promaia/storage/hybrid_storage.py` | 2, 5 | database_id indexes, query changes, path mapping table |
| `promaia/config/registry_sync.py` | 2 | Query by database_id |
| `promaia/agents/agent_config.py` | 3 | SourceAccess.source_id, legacy migration |
| `promaia/agents/executor.py` | 3 | Load context by database_id |
| `promaia/agents/agentic_turn.py` | 4 | query_source ID resolution |
| `promaia/agents/custom_tools.py` | 4 | Query tools ID resolution |
| `promaia/storage/unified_query.py` | 4 | Query by database_id |
| `promaia/storage/files.py` | 5 | Remove hardcoded "journal"/"cms" |
| `promaia/messaging/slack_bot.py` | 7 | Channel context by ID |
| `promaia/cli/database_commands.py` | 6 | Boundary: accept names, resolve to IDs |
| `promaia/chat/interface.py` | 6 | Boundary: accept names, resolve to IDs |
| 20+ other files | 6 | Mechanical replacement |

## Verification

1. **Config migration**: Load existing config → verify keys are now database_ids → verify `get_database_by_id()` works
2. **Storage queries**: Query by database_id returns same results as old name-based queries
3. **Agent context loading**: Agent loads initial context via source_access with database_ids
4. **Tool resolution**: `query_source` with both names (at boundary) and IDs works
5. **Rename test**: Rename a database nickname → verify all queries, agents, and tools still work
6. **Slack bot**: Channel context pre-loads from KB using database_id
