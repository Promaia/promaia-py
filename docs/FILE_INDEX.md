# Content Type Integration - Quick File Reference

## Architecture & Patterns

**Foundation - The Connector Pattern:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/base.py` (242 lines)
  - BaseConnector abstract class (all connectors extend this)
  - QueryFilter, DateRangeFilter, SyncResult classes
  - ConnectorRegistry plugin system for registering content types

**Connector Registration:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/__init__.py` (39 lines)
  - Registers "notion", "gmail", "discord" content types
  - Where you add: `ConnectorRegistry.register("conversation", ConversationConnector)`

---

## Content Type Implementations (EXAMPLES TO FOLLOW)

### Notion Connector
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/notion_connector.py` (1087 lines)
  - Schema synchronization with hybrid storage
  - Dynamic property table creation
  - Batch processing (12 items, 0.08s delay)
  - Date-prefixed file organization
  - Query building with filter translation

### Gmail Connector
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/gmail_connector.py` (1597 lines)
  - OAuth2 authentication flow
  - Intelligent date range chunking (15-day chunks)
  - Thread deduplication
  - Two sync modes: legacy (thread-level) vs. new (message-level appending)
  - Rate limiting with exponential backoff
  - Quote detection and stripping

### Discord Connector
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/discord_connector.py` (1141 lines)
  - Multi-channel support
  - Channel discovery and caching
  - Per-channel directory organization
  - Attachment, embed, and reaction extraction
  - Message-to-markdown conversion

---

## Storage & Database

**Hybrid Storage - Type-Specific Tables:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/hybrid_storage.py`
  - gmail_content table (Gmail messages/threads)
  - notion_journal, notion_stories tables (Notion databases)
  - generic_content table (Fallback for any type)
  - Dynamic property tables: notion_{workspace}_{database_name}
  - unified_content view (query across all types)
  - `sync_table_schema_with_properties()` for dynamic column creation

**Unified Storage - File Saving:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/unified_storage.py`
  - `save_content()` - Save markdown + register in SQLite
  - Directory organization: data/md/{source}/{workspace}/{database}/
  - Hybrid registry integration

**Unified Query Interface:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/unified_query.py`
  - `query_content_for_chat()` - Cross-type search
  - Gmail thread consolidation logic
  - Date filtering and source filtering
  - Property-based filtering

**Content Search:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/content_search.py`
  - `search_content()` - Full-text search through files
  - Metadata extraction and previews

---

## Vector Search (Embeddings)

**ChromaDB Integration:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/vector_db.py`
  - Two collections: promaia_content (full pages) + promaia_properties
  - OpenAI text-embedding-3-small (or sentence-transformers fallback)
  - Cosine similarity

**Page Chunking (for large documents):**
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/page_chunker.py`
  - Splits large pages into ~1000-token chunks
  - Links chunks back to original page_id

---

## Markdown Conversion

**Content → Markdown:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/markdown/converter.py`
  - `page_to_markdown()` - Notion pages
  - Add `conversation_to_markdown()` for your type

---

## CLI & Commands

**Sync Commands:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/cli/database_commands.py`
  - `handle_database_sync()` - Main sync entry point
  - Uses ConnectorRegistry to get connector
  - Applies filters and date ranges
  - Calls `connector.sync_to_local_unified()`

**Main CLI:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/cli.py`
  - Entry point for `maia database sync` command
  - Gets database config and creates connector

---

## Configuration

**Database Configuration:**
- `/Users/kb20250422/Documents/dev/promaia/promaia/config/databases.py`
  - DatabaseConfig class with properties like:
    - nickname (e.g., "journal", "conversations")
    - source_type (e.g., "notion", "gmail", "conversation")
    - database_id (unique identifier)
    - workspace (multi-tenant support)
    - markdown_directory, sync_interval, etc.

**Config File:**
- `promaia.config.json` (root of project)
  - Contains database definitions
  - Edit to add new conversation database config

---

## Key File Organization After Sync

```
data/
├── md/
│   ├── notion/{workspace}/{database_name}/*.md
│   ├── gmail/{workspace}/*.md
│   ├── discord/{workspace}/{channel_name}/*.md
│   └── conversation/{workspace}/{database_name}/*.md    # Your type
├── hybrid_metadata.db (SQLite with all metadata)
└── chroma_db/ (vector embeddings)
```

---

## Quick Start for Your Content Type

**6 Files to Create/Modify:**

1. **Connector:** `connectors/conversation_connector.py` (NEW)
   - Implement BaseConnector interface
   
2. **Registration:** `connectors/__init__.py` (MODIFY)
   - Add: `ConnectorRegistry.register("conversation", ConversationConnector)`

3. **SQL Table:** `storage/hybrid_storage.py` (MODIFY)
   - Add conversation_content table in init_database()

4. **Markdown Converter:** `markdown/converter.py` (MODIFY)
   - Add conversation_to_markdown() function

5. **Config:** `promaia.config.json` (MODIFY)
   - Add database config for conversation type

6. **Tests:** `tests/test_conversation_connector.py` (NEW)
   - Unit tests following pattern from other connectors

---

## Pattern Reference

### Base Method Signatures (Must Implement)
```python
async def connect() -> bool
async def test_connection() -> bool
async def get_database_schema() -> Dict[str, Any]
async def query_pages(...filters, date_filter, sort_by, sort_direction, limit...) -> List[Dict]
async def get_page_content(page_id: str, include_properties: bool) -> Dict[str, Any]
async def get_page_properties(page_id: str) -> Dict[str, Any]
async def sync_to_local_unified(storage, db_config, filters, date_filter, ...) -> SyncResult
```

### Supporting Classes (Use These)
- `QueryFilter(property_name, operator, value)`
- `DateRangeFilter(property_name, start_date, end_date, days_back)`
- `SyncResult()` - tracks: pages_fetched, pages_saved, pages_skipped, pages_failed, errors, api_calls_count, etc.

### Common Patterns
- **Rate Limiting:** Exponential backoff on 429/5xx errors
- **Batch Processing:** Process in groups of 10-12, delay between batches
- **File Names:** `YYYY-MM-DD Title PageID.md`
- **Metadata:** Include created_time, last_edited_time, synced_time, source_id, data_source, content_type
- **Deduplication:** Check for existing items before processing
- **Normalization:** All dates to ISO format, IDs to strings

