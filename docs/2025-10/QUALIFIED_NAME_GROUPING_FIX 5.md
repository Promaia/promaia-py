# Qualified Name Grouping Fix

**Date**: October 8, 2025  
**Status**: ✅ Fixed

## Problem

Results were being grouped by simple database nicknames (`stories`, `gmail`) instead of qualified names (`trass.stories`, `koii.gmail`). This caused:

1. **Confusion**: Multiple databases appeared as one (e.g., both `trass.gmail` and `koii.gmail` showing as just `gmail`)
2. **Data collisions**: Results from `koii.stories` and `trass.stories` were merged into one `stories` group
3. **Lost context**: Users couldn't tell which workspace results came from

### Example

**Before**:
```
Results:
• gmail: 313 entries    ← Mixed from trass.gmail AND koii.gmail!
• stories: 19 entries   ← Mixed from trass.stories AND koii.stories!
```

**After**:
```
Results:
• trass.gmail: 255 entries
• koii.gmail: 58 entries
• trass.stories: 17 entries
• koii.stories: 2 entries
```

## Root Cause

Two issues:

### 1. SQL Query Missing Workspace Column

The AI-generated SQL wasn't selecting the `workspace` column:

```sql
SELECT DISTINCT
u.page_id,
u.title,
u.created_time,
u.database_name  -- Missing: u.workspace!
```

So when results came back, `result.get('workspace', '')` returned empty string.

### 2. Grouping by Simple Names Only

Both the processor and universal adapter were grouping by `database_name` only:

```python
# Old code
db = result.get('database_name', 'unknown')
grouped_results[db] = []  # Simple name grouping
```

## Solution

### 1. Updated SQL Generation Prompt

Added explicit instruction to include workspace column:

```python
Return SQLite query that:
- SELECTs: u.page_id, u.workspace, u.database_name, u.title, u.created_time (+ any other needed fields)
- IMPORTANT: Always include u.workspace in SELECT to distinguish databases across workspaces
```

### 2. Updated Grouping Logic

**Processor** (`nl_orchestrator.py`):

```python
# Group results by qualified name (workspace.database)
for result in results:
    workspace = result.get('workspace', '')
    db_name = result.get('database_name', 'unknown')
    
    # Create qualified key: workspace.database (unless already qualified)
    if workspace and '.' not in db_name:
        qualified_key = f"{workspace}.{db_name}"
    else:
        qualified_key = db_name
    
    if qualified_key not in grouped_results:
        grouped_results[qualified_key] = []
    
    grouped_results[qualified_key].append(...)
```

**Universal Adapter** (`storage/files.py`):

```python
# Group by qualified name (workspace.database)
workspace = entry['workspace']
if workspace and '.' not in database_name:
    qualified_key = f"{workspace}.{database_name}"
else:
    qualified_key = database_name

if qualified_key not in grouped_results:
    grouped_results[qualified_key] = []
grouped_results[qualified_key].append(page_data)
```

## Benefits

✅ **Clear workspace identification**: Users can see which workspace each result came from

✅ **No data collisions**: Results from same-named databases in different workspaces stay separate

✅ **Consistent behavior**: Both processor and adapter use same qualified naming scheme

✅ **Backward compatible**: Still handles cases where database_name already includes workspace prefix

## Testing

```
Query: "trass stories about international launch"

✅ Processor results grouped by:
   ✓ trass.cpj: 1 entries
   ✓ trass.gmail: 255 entries
   ✓ koii.gmail: 58 entries
   ✓ trass.stories: 15 entries
   ✓ koii.stories: 2 entries

✅ Adapter results grouped by:
   ✓ trass.cpj: 1 pages
   ✓ trass.gmail: 14 pages

✅ Both use qualified names!
```

## Files Modified

1. **`promaia/ai/nl_orchestrator.py`**:
   - Updated SQL generation prompt to require workspace column
   - Updated grouping logic to use qualified keys

2. **`promaia/storage/files.py`**:
   - Updated `load_content_by_page_ids()` to group by qualified names

## Edge Cases Handled

- ✅ Database names already with workspace prefix (e.g., `trass.stories`) are used as-is
- ✅ Empty workspace values fall back to simple database name
- ✅ Consistent handling in both processor and adapter
- ✅ Works with all query modes (SQL and vector)

## Impact

This fix ensures users can clearly understand which workspace their query results come from, especially important when:
- Querying across multiple workspaces
- Using databases with common names (journal, gmail, stories, etc.)
- Debugging query results to understand why certain content was included
