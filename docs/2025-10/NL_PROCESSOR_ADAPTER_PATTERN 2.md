# NL Processor Universal Adapter Pattern

**Date**: October 8, 2025  
**Status**: ✅ Implemented

## Overview

Refactored the Natural Language Query Processor to use the universal adapter pattern, separating query execution from content loading. This provides cleaner separation of concerns and reuses existing infrastructure.

## Architecture

### Before

```
NL Processor → Execute Query → Load Full Content → Return to Chat
```

The processor was responsible for both querying and loading full content.

### After

```
NL Processor → Execute Query → Return Page IDs + Metadata
                                        ↓
                              Wrapper → load_content_by_page_ids() → Return Full Content
                                        ↓
                                    Chat Interface
```

The processor returns minimal metadata, and the wrapper uses the universal adapter to load full content.

## Components

### 1. NL Processor (`nl_orchestrator.py`)

**Returns**: Minimal page references grouped by database

```python
{
    'database_name': [
        {
            'page_id': '...',
            'content_type': '...',
            'database_name': '...',
            'workspace': '...',
            'created_time': '...',
            'title': '...'
        },
        ...
    ]
}
```

**Responsibilities**:
- Parse natural language intent
- Generate SQL/vector queries
- Execute queries
- Validate results
- Return page references

### 2. Wrapper (`nl_processor_wrapper.py`)

**Does**:
1. Calls NL processor to get page references
2. Extracts `page_id` values
3. Calls `load_content_by_page_ids()` universal adapter
4. Returns full content to chat interface

**Code**:
```python
# Extract page IDs from results
page_ids = []
for db_name, entries in result["results"].items():
    for entry in entries:
        if entry.get('page_id'):
            page_ids.append(entry['page_id'])

# Use universal adapter to load full content
from promaia.storage.files import load_content_by_page_ids

full_content = load_content_by_page_ids(
    page_ids=page_ids,
    db_path="data/hybrid_metadata.db",
    expand_gmail_threads=True
)

return full_content
```

### 3. Universal Adapter (`storage/files.py`)

`load_content_by_page_ids()` handles:
- Gmail thread expansion
- Loading markdown content from disk
- Grouping by database
- Registry-based file path resolution

**Returns**: Same format as `load_database_pages_with_filters()` for compatibility

```python
{
    'database_name': [
        {
            'page_id': '...',
            'content': '...',  # Full markdown content
            'created_time': '...',
            'title': '...',
            'metadata': {...},
            'file_path': '...',
            ...
        },
        ...
    ]
}
```

## Benefits

### ✅ Separation of Concerns

- **Processor**: Query execution and validation only
- **Adapter**: Content loading and Gmail thread expansion
- **Wrapper**: Orchestration between the two

### ✅ Reuses Existing Infrastructure

The same `load_content_by_page_ids()` adapter is used by:
- Natural language queries
- Vector searches
- Browser selections
- Any other page ID-based queries

### ✅ Consistency

All query methods now return content in the same format, making the chat interface simpler.

### ✅ Performance

Content is only loaded when needed, after successful query validation.

### ✅ Gmail Thread Expansion

The universal adapter automatically expands Gmail threads, so all query methods get this feature for free.

## Testing

```python
# Test shows correct flow
NL Processor returns:
  • journal: 7 entries
  • Keys: ['page_id', 'content_type', 'database_name', 'title', 'workspace', 'created_time']

Universal Adapter loads:
  • journal: 7 pages
  • Has full content: True
  • Keys: ['content', 'page_id', 'created_time', 'title', 'metadata', 'file_path', ...]
```

## Files Modified

1. **`promaia/ai/nl_orchestrator.py`**:
   - Removed `load_content_by_page_ids()` call from processor
   - Now returns minimal page references instead of full content
   - Simplified Step 8 to just group results by database

2. **`promaia/ai/nl_processor_wrapper.py`**:
   - Added page ID extraction from processor results
   - Added call to `load_content_by_page_ids()` universal adapter
   - Updated both `process_natural_language_to_content()` and `process_vector_search_to_content()`

3. **No changes needed to**:
   - `promaia/storage/files.py` (adapter already existed)
   - `promaia/chat/interface.py` (still receives same format)

## Usage

No changes for users. The refactoring is internal only:

```bash
# Works exactly as before
maia chat -nl "stories about international launch"
```

## Future Enhancements

This pattern makes it easy to:
- Cache page references before loading content
- Implement lazy loading (load content on-demand)
- Add content filtering after query but before loading
- Support different content formats (not just markdown)
