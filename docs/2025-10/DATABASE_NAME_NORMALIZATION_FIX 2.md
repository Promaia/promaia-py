# Database Name Normalization Fix

**Date**: October 8, 2025  
**Status**: ✅ Fixed

## Problem

The natural language query system was failing with the error:

```
⚠️  Natural language query failed: Results don't match intended databases. 
Got {'stories'}, expected {'trass.stories'}
```

## Root Cause

There was a mismatch between how the AI understands database names versus how they're stored in the database:

- **AI generates**: Qualified names with workspace prefix (e.g., `trass.stories`)
- **Database stores**: Just the nickname (e.g., `stories`)

This caused two issues:
1. SQL queries were filtering for `database_name = 'trass.stories'` which didn't match any rows
2. Result validation was comparing `'trass.stories'` to `'stories'` and failing

## Solution

Implemented **database name normalization** at two critical points:

### 1. SQL Generation (`nl_orchestrator.py`)

Added normalization before generating SQL:

```python
def normalize_db_name(db_name: str) -> str:
    """Strip workspace prefix from qualified names"""
    if '.' in db_name:
        return db_name.split('.')[-1]  # 'trass.stories' -> 'stories'
    return db_name

target_dbs = [normalize_db_name(db) for db in intent['databases']]
```

The SQL now correctly filters using nicknames:
```sql
WHERE database_name IN ('stories', 'journal', 'cpj')
-- Instead of: WHERE database_name IN ('trass.stories', 'trass.journal', 'trass.cpj')
```

### 2. Result Validation (`nl_utilities.py`)

Added normalization to the validator:

```python
def normalize_db_name(db_name: str) -> str:
    """Extract the nickname from qualified names like 'trass.stories' -> 'stories'"""
    if '.' in db_name:
        return db_name.split('.')[-1]
    return db_name

normalized_intended = {normalize_db_name(db) for db in intended_databases}
normalized_results = {normalize_db_name(db) for db in result_databases}

if intended_databases and not normalized_results.intersection(normalized_intended):
    return False, f"Results don't match intended databases..."
```

Now the validator correctly compares:
- Intent: `{'trass.stories', 'trass.journal'}` → normalized to `{'stories', 'journal'}`
- Results: `{'stories', 'journal'}` → already normalized
- Comparison: ✅ PASS

## How It Works

The system now follows this flow:

1. **User Query**: "stories about international launch in trass"

2. **Intent Parsing**: AI generates qualified names
   ```json
   {
     "databases": ["trass.stories", "trass.journal", "trass.cpj"]
   }
   ```

3. **SQL Generation**: Normalizes to nicknames
   ```python
   target_dbs = ["stories", "journal", "cpj"]
   ```
   ```sql
   WHERE database_name IN ('stories', 'journal', 'cpj')
   ```

4. **Results**: Database returns rows with `database_name = 'stories'`

5. **Validation**: Normalizes both sides and compares
   - Intent: `trass.stories` → `stories`
   - Result: `stories` → `stories`
   - Match: ✅ PASS

## Benefits

- ✅ **AI can use workspace context**: Can generate qualified names like `trass.stories`
- ✅ **SQL works correctly**: Filters using nicknames that actually exist in DB
- ✅ **Validation passes**: Compares apples to apples
- ✅ **Backward compatible**: Works with both qualified and nickname formats

## Testing

All tests pass:

```
✅ Schema returns nicknames without workspace prefixes
✅ AI can generate qualified names (trass.stories)
✅ SQL generation normalizes to nicknames (stories)
✅ Result validation matches both formats correctly
```

## Files Modified

1. `promaia/ai/nl_orchestrator.py`:
   - Added normalization in `_generate_sql_query()`
   - Updated prompt to clarify database_name column format
   - Added debug output showing normalized names

2. `promaia/ai/nl_utilities.py`:
   - Added normalization in `ResultValidator.validate_results()`
   - Handles both qualified and nickname formats

## Example

**Before Fix**:
```
Query: "stories about international launch in trass"
Intent databases: ['trass.stories', 'trass.journal']
SQL: WHERE database_name IN ('trass.stories', 'trass.journal')
Results: 0 rows (no match)
Error: Results don't match intended databases
```

**After Fix**:
```
Query: "stories about international launch in trass"
Intent databases: ['trass.stories', 'trass.journal']
Normalized for SQL: ['stories', 'journal']
SQL: WHERE database_name IN ('stories', 'journal')
Results: 270 rows
Validation: ✅ PASS - Results look good: 270 entries from 2 databases
```

## Future Considerations

The database stores nicknames only. If we ever need to support multiple workspaces with the same nickname (e.g., both `koii.stories` and `trass.stories`), we would need to:
1. Add a `workspace` column filter to queries
2. Update the unified_content view to distinguish between same-named databases from different workspaces

For now, nicknames are unique across all workspaces in the config, so this normalization approach works perfectly.
