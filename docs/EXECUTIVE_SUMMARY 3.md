# Content Type Integration - Executive Summary

## Overview

I've conducted a very thorough exploration of how Notion, Discord, and Gmail are integrated as fully supported content types in the Promaia codebase. The system uses a sophisticated **hybrid architecture** that allows seamless addition of new content types.

## Key Findings

### 1. Architecture Pattern: The Connector Model

All content types follow the same abstraction pattern:

```
┌─────────────────────────────────────────────────────────┐
│             ConnectorRegistry (Plugin System)            │
├─────────────────────────────────────────────────────────┤
│  BaseConnector (Abstract Interface - 7 core methods)    │
├─────────────────────────────────────────────────────────┤
│  Notion      │  Gmail       │  Discord   │  Conversation│
│  Connector   │  Connector   │ Connector  │  Connector   │
│  (1087 LOC)  │  (1597 LOC)  │ (1141 LOC) │  (YOUR TYPE) │
└─────────────────────────────────────────────────────────┘
```

**Key Methods Every Connector Must Implement:**
1. `async connect()` - Establish connection
2. `async test_connection()` - Verify working connection
3. `async get_database_schema()` - Describe available fields
4. `async query_pages()` - Search/filter items
5. `async get_page_content()` - Fetch full item with content
6. `async get_page_properties()` - Get metadata only
7. `async sync_to_local_unified()` - Save to local storage

### 2. Storage: Hybrid Architecture

Instead of a single generic table, the system creates **optimized tables per content type**:

```
SQLite Database (hybrid_metadata.db)
├── gmail_content          (27 columns: subject, sender_email, thread_id, etc.)
├── notion_journal         (9 columns: title, status, date_value, etc.)
├── notion_stories         (9 columns: title, status, epic_relation, etc.)
├── generic_content        (fallback for any type)
├── notion_{workspace}_{db} (dynamic columns for each Notion property)
├── unified_content        (VIEW combining all tables)
└── notion_property_schema (property metadata)

Markdown Files (data/md/)
├── notion/{workspace}/{database}/*.md
├── gmail/{workspace}/*.md
├── discord/{workspace}/{channel}/*.md
└── conversation/{workspace}/{database}/*.md  ← Your type

Vector Embeddings (ChromaDB)
├── promaia_content        (full-page embeddings)
└── promaia_properties     (property-specific embeddings)
```

### 3. Vector Search Integration

- **Automatic:** Embeddings generated on sync
- **Collections:** Two separate collections for content vs. properties
- **Providers:** OpenAI (primary) → Sentence-Transformers (fallback)
- **Retrieval:** Both used in chat context, vector search queries

### 4. SQL Search Integration

- **Unified Query Interface:** `unified_query.py` queries across all types
- **Gmail Thread Consolidation:** Messages grouped by thread
- **Date Filtering:** Across all types
- **Property Filtering:** Type-specific field support

### 5. Comparison of Existing Types

#### Notion (1087 lines)
- **Strength:** Dynamic property support (creates SQL columns for each property)
- **Pattern:** Schema synchronization, batch processing (12 items, 0.08s delay)
- **File Organization:** `YYYY-MM-DD Title PageID.md`
- **Special:** Notion API property type mapping

#### Gmail (1597 lines)
- **Strength:** Complex content handling (OAuth2, quote stripping, message-level appending)
- **Pattern:** Chunking large date ranges (15-day chunks), thread deduplication
- **Storage Modes:** Thread-level (legacy) vs. message-level (new appending)
- **Special:** Intelligent quote detection, exponential backoff rate limiting

#### Discord (1141 lines)
- **Strength:** Multi-channel organization, attachment/embed handling
- **Pattern:** Per-channel directories, channel discovery & caching
- **File Organization:** Channel-specific subdirectories
- **Special:** Temporary client connections (no persistent gateway)

## Implementation Path for Conversation Histories

### Phase 1: Create Connector (1-2 hours)
- File: `connectors/conversation_connector.py`
- Extend BaseConnector
- Implement 7 core methods
- Reference: Gmail or Notion pattern depending on similarity

### Phase 2: Database Schema (30 minutes)
- File: `storage/hybrid_storage.py`
- Add `conversation_content` table
- Columns: conversation_id, participants, message_count, timestamp range, etc.
- Or use `generic_content` table if structure is highly variable

### Phase 3: Registration & Integration (30 minutes)
- File: `connectors/__init__.py` - Register connector
- File: `markdown/converter.py` - Add conversion function
- File: `promaia.config.json` - Add config template

### Phase 4: Automatic Features (0 minutes - built-in!)
- Vector search: Automatically created on sync
- SQL search: Automatically queryable via unified_query
- Chat integration: Automatically available in context
- Browser UI: Automatically listed and searchable

## Detailed Documentation Provided

I've created two comprehensive guides in the `docs/` directory:

1. **CONTENT_TYPE_INTEGRATION_GUIDE.md** (1130 lines)
   - Complete architecture explanation
   - Deep dive into each connector
   - Database schema details
   - Implementation checklist
   - Common patterns & best practices
   - Testing templates

2. **FILE_INDEX.md** (Quick reference)
   - File locations and purposes
   - Method signatures
   - Pattern templates
   - 6-file quick start checklist

## Critical Files to Review

**Understand the Pattern:**
1. `/promaia/connectors/base.py` - The interface you must implement
2. `/promaia/connectors/notion_connector.py` - Best for schema patterns
3. `/promaia/connectors/gmail_connector.py` - Best for complex syncing
4. `/promaia/connectors/discord_connector.py` - Best for multi-item organization

**Understand the Storage:**
5. `/promaia/storage/hybrid_storage.py` - How tables are created
6. `/promaia/storage/unified_storage.py` - How files are saved
7. `/promaia/storage/unified_query.py` - How queries work

**Understand the Integration:**
8. `/promaia/cli/database_commands.py` - Sync entry point
9. `/promaia/storage/vector_db.py` - Embedding creation (automatic)

## Common Patterns to Follow

### Rate Limiting (Gmail, Discord pattern)
```python
async def _retry_with_backoff(self, func, *args, **kwargs):
    for attempt in range(self.max_retry_attempts):
        try:
            return func(*args, **kwargs)
        except HttpError as e:
            if e.resp.status == 429:
                delay = min(base_delay * (2 ** attempt), max_delay)
                await asyncio.sleep(delay)
                continue
            raise
```

### Batch Processing (Notion, Gmail pattern)
```python
BATCH_SIZE = 12
BATCH_DELAY = 0.08

for i in range(0, len(items), BATCH_SIZE):
    batch = items[i:i + BATCH_SIZE]
    batch_tasks = [process_item(item) for item in batch]
    results = await asyncio.gather(*batch_tasks, return_exceptions=True)
    
    if i + BATCH_SIZE < len(items):
        await asyncio.sleep(BATCH_DELAY)
```

### Metadata Standardization (All connectors)
```python
metadata = {
    'page_id': unique_id,
    'title': title,
    'created_time': iso_datetime,      # ISO 8601
    'last_edited_time': iso_datetime,  # ISO 8601
    'synced_time': datetime.now().isoformat(),
    'source_id': original_id,
    'data_source': 'conversation',
    'content_type': 'chat_message',
    'properties': {...},
    # Type-specific fields
    'participants': [...],
}
```

## Quality Metrics of Existing Types

| Aspect | Notion | Gmail | Discord |
|--------|--------|-------|---------|
| Lines of Code | 1087 | 1597 | 1141 |
| Auth Method | API Key | OAuth2 | Bot Token |
| Rate Limiting | Per-request | Exponential backoff | Conservative 1s |
| Batch Size | 12 items, 0.08s delay | 10 threads | Messages per channel |
| File Organization | By database | Flat in workspace | By channel |
| Property Support | Dynamic SQL columns | Labels (JSON) | Metadata (JSON) |
| Special Features | Schema sync | Quote stripping | Channel discovery |

## Expected Results After Implementation

Once your connector is implemented and integrated:

1. **Syncing:** `maia database sync --source {conversation_db}` works
2. **Storage:** Conversations stored in `data/md/conversation/{workspace}/{name}/`
3. **Search:** Conversations queryable through unified query interface
4. **Vectors:** Automatically embedded and searchable via vector similarity
5. **Chat:** Available in chat context retrieval
6. **Properties:** Filterable by conversation properties
7. **Streaming:** Appear in browser UI immediately

## Estimated Implementation Time

- **Connector creation:** 3-4 hours (based on source complexity)
- **Storage integration:** 1 hour
- **Testing:** 1-2 hours
- **Documentation:** 1 hour

**Total: 6-8 hours for production-ready integration**

## Next Steps

1. Read `docs/CONTENT_TYPE_INTEGRATION_GUIDE.md` (30 min)
2. Review comparison of Notion/Gmail/Discord (30 min)
3. Choose closest pattern to follow
4. Copy appropriate connector template and adapt (2-3 hours)
5. Test with sample data
6. Deploy

## Questions Answered

**How are content types defined?**
- Through `ConnectorRegistry.register()` in `connectors/__init__.py`
- Each type gets a unique string identifier ("notion", "gmail", "discord", "conversation")

**How do they integrate with vector search?**
- Automatic: `storage.save_content()` triggers `vector_db.add_embedding()`
- One embedding per page_id
- Uses OpenAI or Sentence-Transformers

**How do they integrate with SQL search?**
- Type-specific tables in `hybrid_metadata.db`
- Unified view `unified_content` for cross-type queries
- Date and property filtering supported

**What are the database schemas?**
- Gmail: 27 optimized columns (subject, sender, thread_id, etc.)
- Notion: Dynamic columns + base properties
- Discord: Generic table + metadata JSON
- Generic fallback for any type

**Where is syncing logic?**
- Connector: `sync_to_local_unified()` method
- CLI: `cli/database_commands.py` orchestrates
- Registry: `connectors/__init__.py` coordinates

**What are common patterns?**
- Rate limiting: Exponential backoff
- Batching: 10-12 items with delay
- Files: `YYYY-MM-DD Title PageID.md`
- Metadata: ISO dates, type-specific fields
- Deduplication: Check before processing

All these answers are extensively documented with code examples in the guides provided.

