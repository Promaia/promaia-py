# Dynamic Schema Mirroring for Notion Databases

## Problem Statement

**Current Architecture Issues:**
1. Hardcoded tables (`notion_journal`, `notion_stories`) are workspace-specific
2. Generic tables use JSON blobs that can't be efficiently queried
3. Schema drift between Notion and SQLite causes query inaccuracies
4. Adding new databases requires manual table creation or falls back to generic storage
5. Client databases are second-class citizens

## Proposed Solution: 1:1 Schema Mirroring

**Automatically create SQLite tables that mirror Notion database schemas exactly.**

### Architecture

```
Notion Database                    SQLite Table
┌─────────────────┐               ┌──────────────────────┐
│ Properties:     │               │ Columns:             │
│ - Title (title) │──────────────>│ - title TEXT         │
│ - Status (sel)  │   Mirror      │ - status TEXT        │
│ - Priority (sel)│   Schema      │ - priority TEXT      │
│ - Assignee (per)│               │ - assignee TEXT      │
│ - Due Date      │               │ - due_date TEXT      │
│ - Custom Field  │               │ - custom_field TEXT  │
└─────────────────┘               └──────────────────────┘
```

### Implementation Flow

#### 1. Database Discovery
```python
# When syncing a new database
database_schema = notion_client.databases.retrieve(database_id)
properties = database_schema['properties']
```

#### 2. Table Creation
```python
# Generate table name
table_name = f"notion_{sanitize(database_nickname)}"

# Map Notion types to SQLite types
TYPE_MAPPING = {
    'title': 'TEXT',
    'rich_text': 'TEXT',
    'number': 'REAL',
    'select': 'TEXT',
    'multi_select': 'TEXT',  # JSON array
    'date': 'TEXT',          # ISO format
    'checkbox': 'BOOLEAN',
    'url': 'TEXT',
    'email': 'TEXT',
    'phone_number': 'TEXT',
    'relation': 'TEXT',      # JSON array of page_ids
    'people': 'TEXT',        # JSON array
    'files': 'TEXT',         # JSON array
    'formula': 'TEXT',
    'rollup': 'TEXT',
    'created_time': 'TEXT',
    'last_edited_time': 'TEXT',
    'created_by': 'TEXT',
    'last_edited_by': 'TEXT'
}

# Create table dynamically
columns = []
for prop_name, prop_config in properties.items():
    prop_type = prop_config['type']
    sqlite_type = TYPE_MAPPING.get(prop_type, 'TEXT')
    column_name = sanitize(prop_name)
    columns.append(f"{column_name} {sqlite_type}")

sql = f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id TEXT UNIQUE NOT NULL,
    workspace TEXT NOT NULL,
    database_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    {', '.join(columns)},
    created_time TEXT,
    last_edited_time TEXT,
    synced_time TEXT NOT NULL,
    UNIQUE(page_id)
)
"""
```

#### 3. Schema Change Detection
```python
# On each sync, check if schema changed
current_schema = get_table_schema(table_name)
notion_schema = fetch_notion_schema(database_id)

if schemas_differ(current_schema, notion_schema):
    migrate_schema(table_name, current_schema, notion_schema)
```

#### 4. Schema Migration
```python
def migrate_schema(table_name, old_schema, new_schema):
    """Handle schema changes without data loss"""
    
    # Added columns
    for new_col in new_schema - old_schema:
        ALTER TABLE {table_name} ADD COLUMN {new_col} {type}
    
    # Removed columns (soft delete - keep data)
    # Mark as deprecated in metadata table
    
    # Renamed columns (detect by ID, not name)
    # Update column mapping
```

### Benefits

#### For Developers
- ✅ No hardcoded schemas in production code
- ✅ Single implementation works for all databases
- ✅ Schema evolution handled automatically
- ✅ Reduced maintenance burden

#### For Users
- ✅ All Notion properties queryable in SQL
- ✅ Accurate query results (no data loss)
- ✅ Custom properties just work
- ✅ Schema changes propagate automatically

#### For Clients
- ✅ Their databases are first-class citizens
- ✅ No "generic" fallback with limited functionality
- ✅ Full query capabilities out of the box
- ✅ Works with any Notion workspace

### Implementation Phases

#### Phase 1: Core Mirroring (Week 1)
- Schema introspection from Notion API
- Dynamic table creation
- Type mapping logic
- Basic property sync

#### Phase 2: Schema Evolution (Week 2)
- Change detection
- Migration logic (add/rename columns)
- Backward compatibility

#### Phase 3: Optimization (Week 3)
- Index generation for common query patterns
- Performance tuning for large databases
- Caching schema metadata

#### Phase 4: Cleanup (Week 4)
- Deprecate hardcoded tables
- Migrate existing data to dynamic tables
- Update query tools to use new tables

### Technical Considerations

#### Column Name Sanitization
```python
def sanitize_column_name(name: str) -> str:
    """Convert Notion property name to SQL-safe column name"""
    # "Due Date" → "due_date"
    # "Status (New)" → "status_new"
    # "Priority!" → "priority"
    name = name.lower()
    name = re.sub(r'[^a-z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_')
    
    # Avoid SQL keywords
    if name in SQL_KEYWORDS:
        name = f"{name}_value"
    
    return name
```

#### Type Mapping Edge Cases
```python
# Multi-select: Store as JSON array
multi_select_value = json.dumps([option['name'] for option in value])

# Relations: Store as JSON array of page_ids  
relation_value = json.dumps([page['id'] for page in value])

# People: Store as JSON with id and name
people_value = json.dumps([{'id': p['id'], 'name': p['name']} for p in value])

# Date: ISO format with optional end date
date_value = {
    'start': date['start'],
    'end': date.get('end'),
    'timezone': date.get('timezone')
}
```

#### Schema Metadata Table
```sql
CREATE TABLE notion_schema_metadata (
    database_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    notion_schema TEXT NOT NULL,  -- JSON
    sqlite_schema TEXT NOT NULL,  -- JSON  
    column_mapping TEXT NOT NULL, -- JSON: notion_prop -> sqlite_col
    last_updated TEXT NOT NULL,
    version INTEGER DEFAULT 1
);
```

### Migration Strategy

#### Step 1: Parallel Tables
- Keep existing hardcoded tables
- Create new dynamic tables alongside
- Sync to both during transition

#### Step 2: Query Layer Update
- Update query tools to use dynamic tables
- Add fallback to old tables for backward compatibility

#### Step 3: Data Migration
```python
# Migrate journal data
INSERT INTO notion_journal_dynamic 
SELECT * FROM notion_journal;

# Verify data integrity
assert count(notion_journal) == count(notion_journal_dynamic)
```

#### Step 4: Deprecation
- Mark old tables as deprecated
- Log warnings when accessed
- Remove after 2 version releases

### Query Compatibility

#### Before (Hardcoded)
```python
# Limited to predefined schemas
query = "SELECT * FROM notion_journal WHERE status = 'Published'"
```

#### After (Dynamic)
```python
# Works with ANY property
query = f"SELECT * FROM {get_table_name(database_id)} WHERE {column} = ?"

# Even custom properties work
query = "SELECT * FROM notion_client_tasks WHERE custom_priority = 'Urgent'"
```

### Example: Client Onboarding

```python
# Client: "I have a 'Sprint Tasks' database with custom fields"

# Step 1: Add database to config
{
    "database_id": "abc123def456",
    "nickname": "sprint_tasks",
    "workspace": "client_workspace"
}

# Step 2: First sync
# System automatically:
# 1. Fetches schema from Notion
# 2. Creates table: notion_sprint_tasks
# 3. Maps all properties to columns
# 4. Syncs data

# Step 3: Query immediately works
query_sql("SELECT * FROM notion_sprint_tasks WHERE assignee = 'Alice'")

# No manual schema definition needed!
```

### Testing Strategy

#### Unit Tests
- Type mapping for all Notion property types
- Column name sanitization edge cases
- Schema diff detection

#### Integration Tests
- Create database with diverse property types
- Sync and verify all data present
- Modify Notion schema, verify migration
- Query all property types

#### Performance Tests
- Large database (10K+ pages)
- Schema with 50+ properties
- Query performance vs. generic table

### Rollout Plan

#### Week 1: Foundation
- Implement core mirroring
- Test with 2-3 databases

#### Week 2: Client Testing
- Deploy to staging with client workspace
- Verify all their databases work
- Gather feedback

#### Week 3: Production Rollout
- Enable for new databases only
- Monitor performance and errors
- Fix edge cases

#### Week 4: Full Migration
- Migrate existing databases
- Deprecate hardcoded tables
- Update documentation

### Success Metrics

- ✅ 100% of database properties captured in SQLite
- ✅ Zero manual schema definitions required
- ✅ Schema changes propagate within 1 sync cycle
- ✅ Query performance within 10% of hardcoded tables
- ✅ Zero data loss during migrations

### Future Enhancements

1. **Smart Indexing**: Auto-create indexes on frequently queried columns
2. **Query Optimization**: Analyze query patterns and suggest schema improvements
3. **Schema Versioning**: Full audit trail of schema changes
4. **Cross-Database Queries**: Join tables from different Notion databases
5. **Schema Templates**: Pre-built schemas for common use cases (CRM, Project Management, etc.)

---

## Comparison: Before vs After

### Before (Current)
```
Notion Databases → SQLite
├─ journal (hardcoded) → notion_journal table ✓
├─ stories (hardcoded) → notion_stories table ✓  
├─ cms (hardcoded) → notion_cms table ✓
└─ client_db → generic_content (JSON blob) ✗
   └─ Can't query properties efficiently
```

### After (Dynamic Mirroring)
```
Notion Databases → SQLite
├─ journal → notion_journal table ✓ (dynamic)
├─ stories → notion_stories table ✓ (dynamic)
├─ cms → notion_cms table ✓ (dynamic)
└─ client_db → notion_client_db table ✓ (dynamic)
   └─ All properties queryable!
```

---

**Status:** Proposed  
**Priority:** High (foundational for multi-tenant scaling)  
**Effort:** 3-4 weeks  
**Dependencies:** None (can be implemented alongside messaging feature)
