# Complete Content Type Integration Guide for Promaia

## Executive Summary

This guide explains how Notion, Discord, and Gmail are integrated as content types in the Promaia codebase and provides the architectural patterns you need to follow to add conversation histories as a fully integrated content type.

**Key Finding:** The system uses a **hybrid architecture** with:
1. **Connector Pattern** - Pluggable source connectors (base.py → specialized connectors)
2. **Unified Storage** - SQLite metadata + Markdown files + Vector embeddings
3. **Content Type Tables** - Separate optimized SQL tables per content type
4. **Universal Query Interface** - Single interface for searching across all types

---

## 1. CONTENT TYPE DEFINITION & ARCHITECTURE

### 1.1 Where Content Types Are Defined

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/__init__.py`

```python
ConnectorRegistry.register("notion", NotionConnector)
ConnectorRegistry.register("gmail", GmailConnector)
ConnectorRegistry.register("discord", DiscordConnector)
```

**Three layers of content type definition:**

1. **Connector Registration** - Maps source_type → Connector class
2. **Database Schema** - Separate SQL table per type (hybrid_storage.py)
3. **Storage Strategy** - How files are organized (markdown, JSON, vectors)

### 1.2 Content Type Metadata Fields

**Every content item tracks:**
- `page_id` - Unique identifier (notion: page_id, gmail: thread_id, discord: msg_id)
- `content_type` - High-level category (email_thread, message, page, etc.)
- `data_source` - Where it came from (notion, gmail, discord)
- `database_id` - Which source database it belongs to
- `workspace` - Multi-tenant isolation

**Location:** Used throughout hybrid_storage.py, unified_storage.py, and content_search.py

---

## 2. THE CONNECTOR PATTERN (ABSTRACTION LAYER)

### 2.1 Base Connector Abstract Class

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/base.py`

Every content type must implement these core methods:

```python
class BaseConnector(ABC):
    
    # Connection Management
    async def connect(self) -> bool
    async def test_connection(self) -> bool
    
    # Schema Discovery
    async def get_database_schema(self) -> Dict[str, Any]
    
    # Querying
    async def query_pages(self, 
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None) -> List[Dict[str, Any]]
    
    # Individual Item Access
    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]
    async def get_page_properties(self, page_id: str) -> Dict[str, Any]
    
    # Syncing
    async def sync_to_local(self,
                           output_directory: str,
                           filters: Optional[List[QueryFilter]] = None,
                           date_filter: Optional[DateRangeFilter] = None,
                           include_properties: bool = True,
                           force_update: bool = False,
                           excluded_properties: List[str] = None) -> SyncResult
    
    # Unified sync (NEW PATTERN)
    async def sync_to_local_unified(self,
                                   storage,
                                   db_config,
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult
```

### 2.2 Supporting Classes

**QueryFilter** - For filtering by properties:
```python
class QueryFilter:
    property_name: str
    operator: str  # eq, ne, gt, lt, gte, lte, in, not_in, contains
    value: Any
```

**DateRangeFilter** - For temporal filtering:
```python
class DateRangeFilter:
    property_name: str
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    days_back: Optional[int]
```

**SyncResult** - Tracks sync operation metrics:
```python
class SyncResult:
    pages_fetched: int
    pages_saved: int
    pages_skipped: int
    pages_failed: int
    files_created: List[str]
    errors: List[str]
    api_calls_count: int
    api_rate_limit_hits: int
    api_errors_count: int
    duration_seconds: float
```

### 2.3 Connector Registration Pattern

**Location:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/__init__.py`

```python
class ConnectorRegistry:
    _connectors: Dict[str, type] = {}
    
    @classmethod
    def register(cls, source_type: str, connector_class: type):
        """Register a connector for a source type."""
        cls._connectors[source_type] = connector_class
    
    @classmethod
    def get_connector(cls, source_type: str, config: Dict[str, Any]) -> Optional[BaseConnector]:
        """Get a connector instance for a source type."""
        connector_class = cls._connectors.get(source_type)
        if connector_class:
            return connector_class(config)
        return None
```

---

## 3. CONNECTOR IMPLEMENTATION EXAMPLES

### 3.1 Notion Connector Pattern

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/notion_connector.py` (1087 lines)

**Key Patterns:**

1. **Connection Management:**
   - Uses official notion_client library
   - Supports workspace-specific clients
   - API error tracking with rate limit detection

2. **Query Building:**
   - Translates QueryFilter → Notion API filter format
   - Converts DateRangeFilter → Notion timestamp conditions
   - Handles pagination with start_cursor

3. **Property Extraction:**
   - Maps Notion property types (title, rich_text, select, multi_select, date, checkbox, number, etc.)
   - Overrides `_extract_property_value()` for type-specific logic

4. **Schema Synchronization:**
   - Calls `get_database_schema()` to retrieve properties
   - Syncs with HybridRegistry for dynamic table columns
   - Updates property columns in SQL tables

5. **File Organization:**
   - Date-prefixed markdown files: `YYYY-MM-DD Title PageID.md`
   - Workspace-specific directories: `data/md/notion/{workspace}/{database_name}/`
   - Markdown conversion using page_to_markdown()

6. **Batch Processing (NEW):**
   - `_process_page_batch()` - Concurrent page fetching with rate limiting
   - `_process_properties_only_batch()` - Fast property-only updates
   - BATCH_SIZE = 12, BATCH_DELAY = 0.08s for optimal throughput

### 3.2 Discord Connector Pattern

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/discord_connector.py` (1141 lines)

**Key Patterns:**

1. **Authentication:**
   - Uses discord.py library
   - Bot token-based authentication
   - Temporary client connections (no persistent gateway)

2. **Multi-Channel Support:**
   - Supports filtering by channel_id or channel_name
   - Multiple channels in single query: `channel_id: [id1, id2]`
   - Channel discovery and caching (`get_cached_accessible_channels()`)

3. **Message Collection:**
   - Per-channel history iteration with pagination
   - Timestamp-based filtering (after/before dates)
   - Two strategies: date-based (ALL messages in range) vs. limit-based

4. **Data Normalization:**
   - Converts discord.Message → standard data format
   - Includes: attachments, embeds, reactions, thread context
   - Timestamps normalized to ISO format

5. **Content Preparation:**
   - `_message_to_markdown()` - Converts message to markdown
   - `_prepare_page_for_storage()` - Creates storage-ready format
   - Channel-specific subdirectories: `data/md/discord/{workspace}/{channel_name}/`

6. **Rate Limiting:**
   - Conservative 1s between requests (Discord allows ~50/sec)
   - Efficient 100ms between chunks
   - Exponential backoff on 429 errors

### 3.3 Gmail Connector Pattern

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/gmail_connector.py` (1597 lines)

**Key Patterns:**

1. **Authentication:**
   - OAuth2 via unified auth module (`maia auth configure google`)
   - Automatic token refresh via proxy
   - Multi-workspace credential resolution

2. **Intelligent Chunking:**
   - Breaks large date ranges into 15-day chunks (configurable)
   - Prevents API timeouts on large syncs
   - Incremental syncs (start_date only) skip chunking

3. **Thread Deduplication:**
   - Multiple messages per thread consolidated
   - Groups messages by threadId first
   - Processes unique threads only

4. **Content Extraction Modes:**
   - `latest_only` (default) - Only newest message, very concise
   - `full_thread` (legacy) - All messages, can be verbose
   - Intelligent quote detection and stripping

5. **Message Processing:**
   - Two sync strategies:
     - `_sync_threads_legacy()` - Replaces entire thread
     - `_sync_messages_with_appending()` - Appends new messages only
   - Extracts: subject, from, to, cc, date, labels, attachments, body

6. **Rate Limiting:**
   - Batch processing: 10 threads per batch
   - Exponential backoff on 429 and 5xx errors
   - Retry with exponential backoff strategy

**Gmail-Specific Content Fields:**
```
- thread_id: Gmail thread identifier
- message_id: Individual message ID
- subject: Email subject
- sender_email/sender_name: Extracted from headers
- recipient_emails: To addresses
- labels: Gmail labels as JSON array
- has_attachments: Boolean
- is_unread: Boolean
- body_snippet: Preview of content
- thread_position: Message position in thread (0-indexed)
- is_latest_in_thread: TRUE for most recent message
- email_date: Original message date
```

---

## 4. DATABASE SCHEMA & STORAGE

### 4.1 Hybrid Storage Architecture

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/hybrid_storage.py`

**Key Concept:** Separate optimized table per content type instead of generic table

**Content-Type-Specific Tables:**

#### Gmail Table (Line 34-72)
```sql
CREATE TABLE gmail_content (
    id INTEGER PRIMARY KEY,
    page_id TEXT UNIQUE NOT NULL,      -- Individual message ID
    workspace TEXT NOT NULL,
    database_id TEXT NOT NULL,          -- Gmail account
    file_path TEXT NOT NULL,
    
    -- Gmail-specific fields
    subject TEXT,
    sender_email TEXT,
    sender_name TEXT,
    recipient_emails TEXT,              -- JSON array
    gmail_labels TEXT,                  -- JSON array
    thread_id TEXT NOT NULL,            -- Links messages
    message_id TEXT UNIQUE NOT NULL,    -- Gmail's ID
    has_attachments BOOLEAN,
    is_unread BOOLEAN,
    body_snippet TEXT,
    message_content TEXT,               -- Extracted, no quotes
    
    -- Thread context
    thread_position INTEGER,
    is_latest_in_thread BOOLEAN,
    
    -- Timestamps
    email_date TEXT,
    created_time TEXT,
    last_edited_time TEXT,
    synced_time TEXT NOT NULL,
    
    -- File metadata
    file_size INTEGER,
    checksum TEXT
)
```

#### Notion Journal Table (Line 75-103)
```sql
CREATE TABLE notion_journal (
    page_id TEXT UNIQUE NOT NULL,
    database_id TEXT NOT NULL,          -- Immutable database ID
    database_name TEXT NOT NULL,
    
    -- Notion-specific fields
    title TEXT,
    status TEXT,                        -- Published, Draft, etc.
    date_value TEXT,                    -- The "Date" property
    tags TEXT,                          -- JSON array
    featured BOOLEAN,
    author_name TEXT,
    
    created_time TEXT,
    last_edited_time TEXT,
    synced_time TEXT NOT NULL
)
```

#### Discord Messages Table (DERIVED)
Discord messages are stored in `generic_content` table with metadata:
```
- discord_channel_name
- discord_channel_id
- discord_server_id
- discord_server_name
- raw_message_data (full message as JSON)
```

#### Generic Content Table (Fallback)
```sql
CREATE TABLE generic_content (
    page_id TEXT UNIQUE NOT NULL,
    workspace TEXT NOT NULL,
    database_id TEXT NOT NULL,
    database_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    
    -- Generic fields (work for any content)
    title TEXT,
    content_type TEXT,                  -- message, page, email, etc.
    created_time TEXT,
    last_edited_time TEXT,
    synced_time TEXT NOT NULL,
    
    metadata TEXT                       -- JSON blob for type-specific data
)
```

### 4.2 Dynamic Property Tables

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/hybrid_storage.py` (Line 300+)

Notion databases create dynamic tables for custom properties:

```sql
CREATE TABLE notion_{workspace}_{database_name} (
    page_id TEXT UNIQUE NOT NULL,
    -- Dynamically added columns for each Notion property
    -- E.g., "Status", "Project", "Due Date", etc.
)
```

**Pattern in hybrid_storage.py:**
```python
def sync_table_schema_with_properties(self, database_id, database_name, 
                                      properties, workspace, remove_columns=False):
    """Sync SQL table columns with Notion properties."""
    # For each property in Notion database:
    #   If property doesn't exist in SQL table, ADD COLUMN
    #   If property exists but not in Notion, optionally remove
```

### 4.3 Unified Content View

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/hybrid_storage.py`

Creates a virtual unified view across all tables:

```sql
CREATE VIEW unified_content AS
SELECT 
    page_id, workspace, database_name, content_type, file_path,
    title, created_time, last_edited_time, synced_time,
    sender_email, sender_name, metadata
FROM (
    SELECT ... FROM gmail_content
    UNION ALL
    SELECT ... FROM notion_journal
    UNION ALL
    SELECT ... FROM notion_stories
    UNION ALL
    SELECT ... FROM generic_content
)
```

---

## 5. VECTOR SEARCH INTEGRATION

### 5.1 ChromaDB Collections

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/vector_db.py`

**Two separate collections:**

1. **Content Collection** - Full-page embeddings
   - 1 embedding per page_id
   - Uses cosine similarity
   - ChromaDB persistent storage

2. **Property Collection** - Property-specific embeddings
   - Embeddings for database properties
   - Enables property-based search
   - Faster for filtered searches

**Embedding Strategy:**
- **Provider Priority:** OpenAI (text-embedding-3-small) → Sentence-Transformers fallback
- **Chunking:** Page chunker for large documents
- **Update Strategy:** On sync, update vector embeddings

### 5.2 Content Chunking

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/page_chunker.py`

**Purpose:** Break large documents into searchable chunks

**Pattern:**
1. Detect page size (Notion pages can be huge)
2. If > 8KB, split into chunks (~1000 tokens each)
3. Create separate embeddings per chunk
4. Link chunks back to original page_id

**Use Case:** Large Notion pages, long email threads

---

## 6. SQL SEARCH INTEGRATION

### 6.1 Hybrid Query Interface

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/unified_query.py`

**Query Pattern - Chat Integration:**

```python
def query_content_for_chat(self, workspace: str, sources: List[str] = None, 
                           days: int = None, filters: Dict[str, Any] = None):
    """Query content with Gmail thread consolidation."""
    
    # Step 1: Find Gmail threads modified within date range
    # Step 2: Get ALL messages from those threads (full context)
    # Step 3: Add non-Gmail content that meets filters
    # Step 4: Return consolidated results
```

**Key Features:**
- Gmail messages grouped by thread
- Date-based cutoff
- Multi-source filtering
- Custom property filters

### 6.2 Property Filtering

**Pattern:** Store custom properties as JSON

```
Gmail: labels (JSON array)
Notion: All custom properties synced to SQL columns
Discord: metadata (JSON blob)
```

**Query Example:**
```python
filters = {
    'gmail': {'labels': ['important']},
    'notion_journal': {'status': 'published'},
    'discord': {'channel_name': '#announcements'}
}
```

---

## 7. SYNCING WORKFLOW

### 7.1 Complete Sync Flow

**Triggered by:** `maia database sync --source {database_id}`

**Location:** `/Users/kb20250422/Documents/dev/promaia/promaia/cli/database_commands.py`

**Steps:**

1. **Get Connector:** `ConnectorRegistry.get_connector(source_type, config)`
2. **Build Filters:**
   - Date filter (if --days or --start-date specified)
   - Property filters (if configured)
3. **Query Source:** `connector.query_pages(filters, date_filter, ...)`
4. **Process Items:**
   - For each item:
     - Get full content: `connector.get_page_content(page_id)`
     - Convert to markdown
     - Save to disk
     - Save to hybrid storage
     - Generate embedding
5. **Track Results:** Update SyncResult with metrics
6. **Update Metadata:** Save sync timestamp to config

### 7.2 File Organization After Sync

```
data/
├── md/
│   ├── notion/
│   │   ├── koii/
│   │   │   ├── journal/
│   │   │   │   ├── 2025-01-15 My Journal Entry abc123.md
│   │   │   │   └── ...
│   │   │   └── stories/
│   │   │       └── ...
│   │   └── other_workspace/
│   │       └── ...
│   ├── gmail/
│   │   └── koii/
│   │       ├── 2025-01-15_09-30-00_jane.doe_Subject msg_12345.md
│   │       └── ...
│   └── discord/
│       └── koii/
│           ├── general/
│           │   └── ...
│           └── announcements/
│               └── ...
├── json/
│   └── [For legacy/raw data if needed]
└── hybrid_metadata.db
    └── Tables: gmail_content, notion_journal, notion_stories, generic_content, etc.
```

### 7.3 Sync Result Tracking

```python
result = SyncResult()
result.database_name = db_config.nickname
result.pages_fetched = 100
result.pages_saved = 95
result.pages_skipped = 5
result.pages_failed = 0
result.api_calls_count = 15
result.api_rate_limit_hits = 0
result.duration_seconds = 45.2
```

---

## 8. ADDING CONVERSATION HISTORIES AS A CONTENT TYPE

### 8.1 Implementation Checklist

Follow these steps to add conversation history support:

#### Step 1: Create Connector Class
**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/conversation_connector.py`

```python
from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

class ConversationConnector(BaseConnector):
    """Connector for conversation histories."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.conversation_source = config.get("database_id")  # e.g., "chat_db"
        self.workspace = config.get("workspace", "koii")
    
    async def connect(self) -> bool:
        """Connect to conversation source."""
        # Implement authentication/connection logic
        pass
    
    async def test_connection(self) -> bool:
        """Test connection."""
        pass
    
    async def get_database_schema(self) -> Dict[str, Any]:
        """Return conversation schema."""
        return {
            "conversation_id": {"type": "text", "description": "Unique conversation ID"},
            "participant_count": {"type": "number", "description": "Number of participants"},
            "first_message_time": {"type": "date", "description": "When conversation started"},
            "last_message_time": {"type": "date", "description": "Latest message timestamp"},
            "message_count": {"type": "number", "description": "Total messages"},
            "participants": {"type": "multi_select", "description": "Participant names/IDs"},
            "topic": {"type": "text", "description": "Conversation topic/summary"},
            "has_attachments": {"type": "checkbox", "description": "Any attachments in conversation"},
            "tags": {"type": "multi_select", "description": "Custom tags"},
        }
    
    async def query_pages(self, 
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Query conversations.
        
        Returns list of conversations with metadata.
        Each "page" is one conversation.
        """
        # Query implementation
        pass
    
    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full conversation content (all messages)."""
        # Return: {
        #     "id": conversation_id,
        #     "messages": [message_list],
        #     "properties": {...},  # if include_properties
        # }
        pass
    
    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get conversation metadata."""
        # Return: {
        #     "participant_count": N,
        #     "message_count": N,
        #     "first_message_time": ISO,
        #     "last_message_time": ISO,
        #     ...
        # }
        pass
    
    async def sync_to_local_unified(self, 
                                   storage,
                                   db_config,
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult:
        """Sync conversations using unified storage."""
        result = SyncResult()
        result.start_time = datetime.now()
        
        try:
            # Query conversations
            conversations = await self.query_pages(filters, date_filter, limit=None)
            result.pages_fetched = len(conversations)
            
            # Process each conversation
            for conversation in conversations:
                page_id = conversation['id']
                
                # Get full content
                conversation_data = await self.get_page_content(page_id, include_properties)
                
                # Prepare for storage
                markdown_content = self._conversation_to_markdown(conversation_data)
                
                # Save using unified storage
                storage.save_content(
                    page_id=page_id,
                    title=conversation_data.get('title'),
                    content_data=conversation_data,
                    database_config=db_config,
                    markdown_content=markdown_content
                )
                
                result.pages_saved += 1
            
            result.end_time = datetime.now()
            return result
            
        except Exception as e:
            result.errors.append(str(e))
            result.end_time = datetime.now()
            return result
    
    def _conversation_to_markdown(self, conversation: Dict[str, Any]) -> str:
        """Convert conversation to markdown format."""
        # Format: headers + chronological messages
        # Each message: sender, timestamp, content
        pass
```

#### Step 2: Register Connector

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/__init__.py`

Add:
```python
try:
    from .conversation_connector import ConversationConnector
    ConnectorRegistry.register("conversation", ConversationConnector)
    conversation_available = True
except ImportError:
    conversation_available = False

if conversation_available:
    __all__.append('ConversationConnector')
```

#### Step 3: Create SQL Table

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/storage/hybrid_storage.py`

Add table in `init_database()`:

```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversation_content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id TEXT UNIQUE NOT NULL,          -- conversation_id
        workspace TEXT NOT NULL,
        database_id TEXT NOT NULL,             -- conversation source
        database_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        
        -- Conversation-specific fields
        title TEXT,                            -- Conversation topic/title
        participant_count INTEGER,
        participants TEXT,                     -- JSON array
        message_count INTEGER,
        first_message_time TEXT,               -- When conversation started
        last_message_time TEXT,                -- Latest message
        has_attachments BOOLEAN DEFAULT FALSE,
        tags TEXT,                             -- JSON array
        conversation_summary TEXT,             -- AI-generated summary
        
        -- Common timestamp fields
        created_time TEXT,
        last_edited_time TEXT,
        synced_time TEXT NOT NULL,
        
        -- File metadata
        file_size INTEGER,
        checksum TEXT,
        
        UNIQUE(page_id)
    )
""")
```

#### Step 4: Create Markdown Converter

**File:** `/Users/kb20250422/Documents/dev/promaia/promaia/markdown/converter.py` (add function)

```python
def conversation_to_markdown(conversation: Dict[str, Any], 
                            include_properties: bool = False) -> str:
    """Convert conversation structure to markdown."""
    
    title = conversation.get('title', 'Conversation')
    participants = conversation.get('participants', [])
    message_count = conversation.get('message_count', 0)
    messages = conversation.get('messages', [])
    
    # Build markdown
    md = f"# {title}\n\n"
    
    if include_properties:
        md += f"**Participants:** {', '.join(participants)}\n"
        md += f"**Messages:** {message_count}\n"
        md += f"**Duration:** {conversation.get('first_message_time')} to {conversation.get('last_message_time')}\n\n"
        md += "---\n\n"
    
    # Add messages chronologically
    for msg in messages:
        sender = msg.get('sender', 'Unknown')
        timestamp = msg.get('timestamp', '')
        content = msg.get('content', '')
        
        md += f"**{sender}** ({timestamp}):\n"
        md += f"{content}\n\n"
    
    return md
```

#### Step 5: Add to Database Config

**File:** `promaia.config.json`

```json
{
  "databases": [
    {
      "nickname": "my_conversations",
      "source_type": "conversation",
      "database_id": "chat_db",
      "workspace": "koii",
      "markdown_directory": "data/md/conversation/koii/my_conversations",
      "sync_interval": 3600,
      "sync_limit": 100,
      "sync_interval_minutes": 60
    }
  ]
}
```

#### Step 6: Integration Points Summary

**Where conversation content will be used:**

1. **Vector Search** - ChromaDB will automatically embed conversations
2. **SQL Search** - Queries through `unified_content` view
3. **Chat Interface** - Available in context retrieval
4. **Browser UI** - Listed and searchable
5. **API** - Accessible through standard query endpoints

---

## 9. COMMON PATTERNS & BEST PRACTICES

### 9.1 Rate Limiting Pattern

**Used by:** Gmail, Discord

```python
async def _retry_with_backoff(self, func, *args, **kwargs):
    for attempt in range(self.max_retry_attempts):
        try:
            return func(*args, **kwargs)
        except HttpError as e:
            if e.resp.status == 429:  # Rate limit
                delay = min(self.RATE_LIMIT_RETRY_DELAY * (2 ** attempt), self.MAX_RETRY_DELAY)
                await asyncio.sleep(delay)
                continue
            raise
```

### 9.2 Batch Processing Pattern

**Used by:** Notion, Gmail

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

### 9.3 File Organization Pattern

```
data/md/{source_type}/{workspace}/{database_name}/
├── YYYY-MM-DD Title PageID.md
└── YYYY-MM-DD Title PageID.md
```

**Name Components:**
- Date prefix for temporal sorting
- Title for human readability
- Page ID for uniqueness and lookup

### 9.4 Metadata Preparation Pattern

```python
def _prepare_page_for_storage(self, item, db_config, excluded_properties=None):
    """Standardized preparation for any content type."""
    
    return {
        'page_id': unique_id,
        'content': markdown_content,
        'metadata': {
            'page_id': unique_id,
            'title': title,
            'created_time': iso_datetime,
            'last_edited_time': iso_datetime,
            'synced_time': datetime.now().isoformat(),
            'source_id': original_id,
            'data_source': source_type,
            'content_type': specific_type,
            'properties': custom_properties,
            # Type-specific fields
            'type_specific_field': value
        }
    }
```

### 9.5 Property Filter Pattern

```python
# Generic implementation in BaseConnector
def apply_property_filters(self, pages: List[Dict], 
                          property_filters: Dict[str, Any]) -> List[Dict]:
    filtered_pages = []
    for page in pages:
        properties = page.get("properties", {})
        include_page = True
        
        for prop_name, expected_values in property_filters.items():
            if prop_name not in properties:
                include_page = False
                break
            
            prop_value = self._extract_property_value(properties[prop_name])
            
            if isinstance(expected_values, list):
                if prop_value not in expected_values:
                    include_page = False
                    break
            elif prop_value != expected_values:
                include_page = False
                break
        
        if include_page:
            filtered_pages.append(page)
    
    return filtered_pages
```

---

## 10. KEY FILES SUMMARY

### Core Architecture
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/base.py` - 242 lines
  - BaseConnector abstract class
  - QueryFilter, DateRangeFilter, SyncResult classes
  - ConnectorRegistry plugin system

### Content Type Implementations
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/notion_connector.py` - 1087 lines
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/gmail_connector.py` - 1597 lines
- `/Users/kb20250422/Documents/dev/promaia/promaia/connectors/discord_connector.py` - 1141 lines

### Storage
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/hybrid_storage.py` - Central SQL registry
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/unified_storage.py` - File saving logic
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/unified_query.py` - Cross-type querying
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/vector_db.py` - ChromaDB embeddings
- `/Users/kb20250422/Documents/dev/promaia/promaia/storage/content_search.py` - Full-text search

### CLI & Orchestration
- `/Users/kb20250422/Documents/dev/promaia/promaia/cli/database_commands.py` - Sync commands
- `/Users/kb20250422/Documents/dev/promaia/promaia/cli.py` - CLI entry point

### Markdown Conversion
- `/Users/kb20250422/Documents/dev/promaia/promaia/markdown/converter.py` - Content → markdown

---

## 11. DETAILED IMPLEMENTATION EXAMPLES

### Example 1: Discord Channel Structure

Discord implements multi-channel support through properties:

```python
# In config:
"property_filters": {
    "channel_id": ["123456", "789012"],  # Multiple channels
    "discord_channel_name": ["general", "announcements"]
}

# In query:
channel_identifiers = self._extract_multiple_channel_filters(filters)
# Returns: ["123456", "789012"] or ["name:general", "name:announcements"]

# In storage:
channel_name = message.get('channel_name', 'unknown')
safe_channel_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in channel_name)
discord_db_config.markdown_directory = os.path.join(original_md_dir, safe_channel_name)
# Results in: data/md/discord/koii/general/, data/md/discord/koii/announcements/
```

### Example 2: Gmail Message-Level vs Thread-Level Storage

Gmail supports two storage modes:

**Thread-Level (Legacy):**
- One file per thread containing all messages
- Entire thread is replaced on sync

**Message-Level (New - 'latest_only'):**
- One file per message
- New messages appended to threads
- Avoids duplication and preserves history

```python
# Config determines mode:
"gmail_content_mode": "latest_only"  # or "full_thread"

# In sync:
if content_mode == 'latest_only':
    return await self._sync_messages_with_appending(...)
else:
    return await self._sync_threads_legacy(...)

# Hybrid registry tracks:
- existing_message_ids_for_thread(thread_id, workspace)
- Skips messages already synced
- Updates is_latest_in_thread flags
```

### Example 3: Notion Property Synchronization

Notion databases create dynamic table columns for properties:

```python
# Discovery Phase
schema = await connector.get_database_schema()
# Returns: {"Name": {"type": "title"}, "Status": {"type": "select"}, ...}

# Sync Phase
registry.sync_table_schema_with_properties(
    database_id="abc123",
    database_name="journal",
    properties=schema,
    workspace="koii",
    remove_columns=False
)

# Result: Table notion_koii_journal gains columns:
# - page_id (always present)
# - Name (TEXT)
# - Status (TEXT)
# - [any other properties]

# Property Table also created for faster property queries:
# notion_property_schema with entries like:
# - database_id: "abc123"
# - property_name: "Status"
# - property_type: "select"
# - allowed_values: ["Done", "In Progress", "Backlog"]
```

---

## 12. TESTING YOUR CONNECTOR

### Unit Test Template

```python
import pytest
from promaia.connectors import ConversationConnector
from promaia.connectors.base import QueryFilter, DateRangeFilter

@pytest.mark.asyncio
async def test_conversation_connect():
    config = {"database_id": "test_db", "workspace": "test"}
    connector = ConversationConnector(config)
    assert await connector.connect() == True

@pytest.mark.asyncio
async def test_conversation_schema():
    config = {"database_id": "test_db", "workspace": "test"}
    connector = ConversationConnector(config)
    schema = await connector.get_database_schema()
    assert "conversation_id" in schema
    assert "message_count" in schema

@pytest.mark.asyncio
async def test_conversation_query():
    config = {"database_id": "test_db", "workspace": "test"}
    connector = ConversationConnector(config)
    
    # Query with date filter
    date_filter = DateRangeFilter(
        property_name="first_message_time",
        start_date=datetime(2025, 1, 1),
        end_date=datetime(2025, 1, 31)
    )
    
    results = await connector.query_pages(date_filter=date_filter)
    assert isinstance(results, list)
```

---

## CONCLUSION

To add conversation histories as a fully integrated content type:

1. **Create ConversationConnector** inheriting BaseConnector
2. **Implement 7 core methods** (connect, query_pages, get_page_content, etc.)
3. **Add SQL table** in hybrid_storage.py
4. **Register connector** in connectors/__init__.py
5. **Add to config** in promaia.config.json
6. **Create markdown converter** in markdown/converter.py

The system will automatically:
- Sync conversations to markdown files
- Store metadata in SQLite
- Generate vector embeddings
- Make searchable through unified query interface
- Integrate with chat interface
- Support property filtering

Follow the patterns from Notion, Gmail, and Discord for consistency.

