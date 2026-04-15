# Property Embeddings Cheat Sheet

Quick reference for using property-aware semantic search in Promaia.

## Quick Start

### 1. Enable in Config

```json
{
  "global": {
    "vector_search": {
      "property_embeddings": {
        "enabled": true
      }
    }
  },
  "databases": {
    "your_database": {
      "include_properties": true  // Required!
    }
  }
}
```

### 2. Sync to Populate Schemas

```bash
maia sync --source your_database:7
```

### 3. Query with Property Constraints

```bash
maia chat "stories with epic holiday launch"
```

## Property Types

### Semantic Search (Embeddings)
Use for flexible matching on text-like properties:
- **title** - Page titles
- **text** - Plain text fields
- **rich_text** - Rich text content
- **relation** - Relations (resolved to titles)

### Exact Match (Filtering)
Use for structured data properties:
- **select** - Single-select dropdowns
- **status** - Status fields
- **multi_select** - Multi-select fields
- **people** - People/user fields
- **date** - Date fields
- **checkbox** - Boolean checkboxes
- **number** - Numeric fields

## Query Examples

### Semantic Property Queries
```bash
# Search relation property
maia chat "stories with epic 2025 holiday launch"

# Search title property
maia chat "journal entries about project milestone"

# Search text/rich_text property
maia chat "tasks with description containing API integration"
```

### Filter Property Queries
```bash
# Status filter
maia chat "stories with status in progress"

# People filter
maia chat "tasks assigned to engineering team"

# Select filter
maia chat "stories with priority high"

# Multi-select filter
maia chat "tasks tagged with bug fix"
```

### Combined Queries
```bash
# Multiple constraints
maia chat "in-progress stories with epic holiday launch from last week"

# Semantic + filter
maia chat "high priority stories with epic product launch"

# Cross-property
maia chat "stories assigned to Consumer Product with epic 2025 goals"
```

## Backfill Commands

### Basic Backfill
```bash
# Sync all property embeddings
python sync_property_embeddings.py

# Preview what would be synced
python sync_property_embeddings.py --dry-run
```

### Filtered Backfill
```bash
# Specific workspace
python sync_property_embeddings.py --workspace trass

# Specific database
python sync_property_embeddings.py --database stories

# Both workspace and database
python sync_property_embeddings.py --workspace trass --database stories
```

### Advanced Options
```bash
# Force re-embed existing
python sync_property_embeddings.py --force

# Verbose output
python sync_property_embeddings.py --verbose

# Combine options
python sync_property_embeddings.py --workspace trass --force --verbose
```

## Verification

### Check Property Schemas
```bash
sqlite3 data/hybrid_metadata.db \
  "SELECT database_name, property_name, notion_type
   FROM notion_property_schema
   WHERE database_name='stories';"
```

### Check Property Embeddings
```python
from promaia.storage.vector_db import VectorDBManager

vector_db = VectorDBManager()
count = vector_db.property_collection.count()
print(f"Property embeddings: {count}")
```

### Test Property Search
```python
from promaia.storage.vector_db import VectorDBManager

vector_db = VectorDBManager()
results = vector_db.search_property(
    property_name="epic",
    query_text="holiday launch",
    n_results=5
)
for r in results:
    print(f"{r['page_id']}: {r['document']}")
```

## Common Patterns

### Search by Epic/Project
```bash
maia chat "stories with epic Q4 product launch"
maia chat "tasks with project website redesign"
```

### Search by Status/State
```bash
maia chat "in-progress stories from this week"
maia chat "completed tasks from last sprint"
```

### Search by Team/Owner
```bash
maia chat "stories assigned to Consumer Product team"
maia chat "tasks owned by engineering"
```

### Time-Bounded Property Search
```bash
maia chat "stories with epic holiday launch from last 2 weeks"
maia chat "in-progress tasks from this sprint"
```

### Cross-Database Property Search
```bash
maia chat -s stories:7 -s epics:all "stories with epic matching Q4 goals"
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Property schemas empty | Ensure `include_properties: true` in config |
| No property embeddings | Run `sync_property_embeddings.py` |
| Query not using properties | Use property names from schema |
| Relations not resolving | Check related pages exist in database |
| Rate limiting | Wait a few minutes, run sync again |

## Configuration Quick Reference

### Minimal Config
```json
{
  "global": {
    "vector_search": {
      "property_embeddings": {
        "enabled": true
      }
    }
  }
}
```

### Full Config
```json
{
  "global": {
    "vector_search": {
      "enabled": true,
      "embedding_provider": "openai",
      "embedding_model": "text-embedding-3-small",
      "property_embeddings": {
        "enabled": true,
        "embeddable_types": ["title", "text", "rich_text", "relation"],
        "filter_types": ["select", "status", "multi_select", "people"],
        "default_property_similarity_threshold": 0.75
      }
    }
  }
}
```

## Next Steps

- Read full documentation: [property_embeddings.md](property_embeddings.md)
- Try example queries above
- Check property schemas in your databases
- Backfill embeddings for existing content
- Experiment with combined constraints

---

**Quick Help**: `maia chat "stories with epic holiday launch" --debug` to see property constraint extraction
