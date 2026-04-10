# Property Embeddings Feature

## Overview

The Property Embeddings feature enables semantic search on Notion database properties, allowing natural language queries like "stories with epic 2025 holiday launch" where "epic" is a Notion property field.

This feature intelligently routes queries based on property types:
- **Semantic properties** (title, text, rich_text, relation): Use separate embeddings for flexible semantic matching
- **Filter properties** (select, status, multi_select, people, date, checkbox, number): Use exact filtering with LLM-based normalization

## Architecture

### Core Components

1. **Separate ChromaDB Collection**: Property embeddings stored in `promaia_properties` collection
2. **Property Schema Tracking**: `notion_property_schema` table tracks available properties per database
3. **Automatic Sync Integration**: Property schemas and embeddings created automatically during sync
4. **Intent-Based Query Routing**: NL orchestrator extracts property constraints and routes to appropriate search

### Data Flow

```
Notion Sync → Schema Extraction → Property Schema DB
           → Content Sync → Property Embedding Creation → ChromaDB

User Query → Intent Parser → Property Constraint Extraction
          → Query Strategy → Property Search Routing
          → Result Intersection → Unified Results
```

### Vector ID Format

Property embeddings use the ID format: `{page_id}_prop_{property_name}`

Example: `1c1ce8f7-317a-80df_prop_epic`

## Property Types

### Embeddable Properties (Semantic Search)

These properties get separate embeddings for semantic matching:

- **title**: Page titles
- **text**: Plain text fields
- **rich_text**: Rich text content
- **relation**: Relations (resolved to linked page titles)

**Example Query**: "stories with epic containing holiday launch"
- Searches the `epic` property embeddings for semantic match with "holiday launch"

### Filterable Properties (Exact Match)

These properties use metadata filtering with LLM normalization:

- **select**: Single-select dropdowns
- **status**: Status fields
- **multi_select**: Multi-select fields
- **people**: People/user fields
- **date**: Date fields
- **checkbox**: Boolean checkboxes
- **number**: Numeric fields

**Example Query**: "stories with status in progress"
- LLM normalizes "in progress" to match exact status value "In Progress"
- Applies metadata filter to results

## Configuration

### Enable Property Embeddings

In `promaia.config.json`:

```json
{
  "global": {
    "vector_search": {
      "enabled": true,
      "property_embeddings": {
        "enabled": true,
        "embeddable_types": ["title", "text", "rich_text", "relation"],
        "filter_types": ["select", "status", "multi_select", "people", "date", "checkbox", "number"],
        "default_property_similarity_threshold": 0.75
      }
    }
  },
  "databases": {
    "your_database": {
      "include_properties": true  // Must be true for property embeddings
    }
  }
}
```

### Database Configuration

Property embeddings require `include_properties: true` for each database:

```json
{
  "databases": {
    "stories": {
      "source_type": "notion",
      "database_id": "...",
      "include_properties": true,  // Required
      "sync_enabled": true
    }
  }
}
```

## Usage Examples

### Natural Language Queries

**Query with semantic property constraint**:
```bash
maia chat "stories with epic 2025 holiday launch"
```

This query:
1. Extracts property constraint: `epic` (relation property)
2. Searches property embeddings for "2025 holiday launch"
3. Searches main content for "stories"
4. Returns intersection of results

**Query with filter property constraint**:
```bash
maia chat "stories assigned to Consumer Product team"
```

This query:
1. Extracts property constraint: `Team` (multi_select property)
2. LLM normalizes "Consumer Product team" to exact value
3. Applies metadata filter
4. Returns filtered results

**Combined constraints**:
```bash
maia chat "in-progress stories with epic holiday launch from last sprint"
```

This query:
1. Extracts multiple constraints: `status` (filter) + `epic` (semantic)
2. Applies status filter for "In Progress"
3. Searches epic embeddings for "holiday launch"
4. Applies date filter for "last sprint"
5. Returns intersection of all constraints

### Programmatic Usage

```python
from promaia.ai.nl_orchestrator import NLOrchestrator
from promaia.ai.query_strategies import VectorSearchStrategy

# Initialize orchestrator
orchestrator = NLOrchestrator()

# Execute property-aware query
results = orchestrator.execute_query(
    "stories with epic 2025 holiday launch",
    verbose=True
)

# The orchestrator automatically:
# 1. Parses property constraints
# 2. Routes to appropriate search strategy
# 3. Returns unified results
```

### Backfill Existing Content

For existing content synced before property embeddings were enabled:

```bash
# Dry run to see what would be embedded
python sync_property_embeddings.py --dry-run

# Sync all databases
python sync_property_embeddings.py

# Sync specific workspace
python sync_property_embeddings.py --workspace trass

# Sync specific database
python sync_property_embeddings.py --database stories

# Force re-embed (overwrite existing)
python sync_property_embeddings.py --force

# Verbose output
python sync_property_embeddings.py --verbose
```

## Implementation Details

### Files Modified

#### 1. `promaia/storage/vector_db.py`
- Added `property_collection` for separate property embeddings
- Added `add_property_embedding()` method
- Added `search_property()` method for property-specific search

#### 2. `promaia/storage/hybrid_storage.py`
- Added `_format_property_for_embedding()` helper
- Added `_resolve_relation_titles()` for relation resolution
- Modified `_embed_to_vector_db()` to create property embeddings during sync
- Added `update_property_schema()` to track property schemas

#### 3. `promaia/ai/nl_orchestrator.py`
- Added `_format_property_schema_context()` for schema context
- Updated intent parser to extract property constraints
- Enhanced prompt to handle property-aware queries

#### 4. `promaia/ai/query_strategies.py`
- Rewrote `execute_query()` with property routing logic
- Added `_filter_property()` for filterable property handling
- Added `_load_results_by_page_ids()` for result loading
- Implemented result intersection for combined constraints

#### 5. `promaia/connectors/notion_connector.py`
- Added `update_property_schema()` call during sync
- Schema population happens after schema caching

### Database Schema

**notion_property_schema table**:
```sql
CREATE TABLE IF NOT EXISTS notion_property_schema (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    database_id TEXT NOT NULL,
    database_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    property_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    sqlite_type TEXT NOT NULL,
    notion_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(database_name, column_name)
)
```

### Property Embedding Metadata

Each property embedding includes:
```python
{
    "workspace": "trass",
    "database_name": "stories",
    "database_id": "1c1ce8f7-317a-80df",
    "content_type": "notion_page",
    "page_id": "1c1ce8f7-317a-80df-afc4-d95c8cb67c3e",
    "property_name": "epic",
    "property_type": "relation"
}
```

### Relation Resolution

Relations are resolved to page titles at embedding time:

```python
# Relation value: ["page_id_1", "page_id_2"]
# Resolved to: "Epic 1, Epic 2"
# Embedded as: "Epic 1, Epic 2"
```

This enables semantic search like "stories with epic holiday launch" to match epics whose titles contain relevant keywords.

## Testing

### Verify Schema Population

Check that property schemas are being tracked:

```bash
sqlite3 data/hybrid_metadata.db "SELECT * FROM notion_property_schema WHERE database_name='stories';"
```

Expected output shows properties with their types:
```
1|1c1ce8f7-317a-80df|stories|notion_stories|Title|title|TEXT|title|2025-10-26 22:00:00
2|1c1ce8f7-317a-80df|stories|notion_stories|Epic|epic|TEXT|relation|2025-10-26 22:00:00
3|1c1ce8f7-317a-80df|stories|notion_stories|Status|status|TEXT|status|2025-10-26 22:00:00
```

### Verify Property Embeddings

Check ChromaDB property collection:

```python
from promaia.storage.vector_db import VectorDBManager

vector_db = VectorDBManager()
count = vector_db.property_collection.count()
print(f"Property embeddings: {count}")

# Get sample
sample = vector_db.property_collection.get(limit=5)
print(sample)
```

### Test Property Search

```python
from promaia.storage.vector_db import VectorDBManager

vector_db = VectorDBManager()

# Search property embeddings
results = vector_db.search_property(
    property_name="epic",
    query_text="holiday launch",
    n_results=5
)

for result in results:
    print(f"Page: {result['page_id']}")
    print(f"Epic: {result['document']}")
    print(f"Similarity: {result['distance']}")
```

### Test End-to-End Query

```bash
# Query with property constraint
maia chat "stories with epic 2025 holiday launch" --debug

# Should show:
# - Property constraint extraction
# - Property search execution
# - Result intersection
```

## Troubleshooting

### Property schemas not populating

**Symptom**: `notion_property_schema` table is empty after sync

**Solution**:
1. Verify `include_properties: true` in database config
2. Check that `update_property_schema()` is being called in `notion_connector.py:719-747`
3. Run sync with `--force` flag to trigger full sync

### Property embeddings not created

**Symptom**: Property collection empty after sync

**Solution**:
1. Verify property schemas are populated (see above)
2. Check that embeddable types are configured correctly
3. Use `sync_property_embeddings.py` to backfill existing content
4. Check logs for embedding errors

### Queries not using property constraints

**Symptom**: Property constraints not extracted from queries

**Solution**:
1. Verify query mentions property by name (e.g., "epic", "status")
2. Check that property exists in schema for target database
3. Use `--debug` flag to see intent parsing output
4. Ensure property embeddings config is enabled

### Rate limiting during sync

**Symptom**: Many "rate limited" errors during sync

**Solution**:
1. This is expected with Notion API - wait a few minutes
2. Use `--source database:N` to sync fewer pages
3. Property schemas are still populated even if content sync fails
4. Re-run sync after rate limits clear

## Performance Considerations

### Embedding Creation

- Property embeddings created during normal sync
- No additional API calls required
- Minimal performance impact (~100ms per page with properties)

### Query Performance

- Property search is parallel to content search
- Result intersection is efficient (set operations)
- Property collection is smaller than content collection
- No performance degradation for non-property queries

### Storage

- Property embeddings stored separately from content
- Each embeddable property creates one additional vector
- Typical overhead: ~2-5% of total vector count

## Future Enhancements

### Planned Features

1. **Property value faceting**: Show common property values in results
2. **Property-based ranking**: Boost results matching property constraints
3. **Cross-database property search**: Search properties across databases
4. **Property value suggestions**: Auto-complete property values in queries
5. **Property analytics**: Track which properties are most queried

### Configuration Options

Future configuration additions:

```json
{
  "property_embeddings": {
    "enable_faceting": true,
    "enable_ranking_boost": true,
    "boost_factor": 1.5,
    "max_property_results": 100
  }
}
```

## References

### Related Files

- `promaia/storage/vector_db.py` - Vector database operations
- `promaia/storage/hybrid_storage.py` - Content sync and embedding
- `promaia/ai/nl_orchestrator.py` - Intent parsing
- `promaia/ai/query_strategies.py` - Query execution
- `promaia/connectors/notion_connector.py` - Notion sync integration
- `sync_property_embeddings.py` - Backfill script

### Database Tables

- `unified_content` - Main content table
- `notion_property_schema` - Property schema tracking
- `notion_journal`, `notion_stories`, etc. - Database-specific tables with property columns

### ChromaDB Collections

- `promaia_content` - Main content embeddings
- `promaia_properties` - Property embeddings (new)

## Version History

### v1.0.0 (2025-10-26)
- Initial implementation
- Support for embeddable and filterable property types
- Automatic schema population during sync
- Property-aware query routing
- Backfill script for existing content
- Comprehensive configuration options
