# Chat Qualified Names Display

**Date**: October 8, 2025  
**Status**: ✅ Implemented

## Problem

The chat initialization message showed simple database nicknames instead of qualified names:

```
🐙 maia chat
Query: maia chat -nl entries in the trass stories database with term UK
Pages loaded: 14
stories: 14        ← Which workspace? Unclear!
```

When loading from multiple workspaces, it was confusing:

```
Pages loaded: 313
gmail: 313        ← Is this trass.gmail or koii.gmail? Mixed?
stories: 19       ← Is this trass.stories or koii.stories? Mixed?
```

## Root Cause

The `generate_source_breakdown()` function tried to be clever by only showing qualified names when there was a collision:

```python
if potential_collision:
    # Include workspace prefix to disambiguate
    source_name = f"{workspace}.{source}"
else:
    # No collision, use just the source name
    source_name = source  # ← Problem: loses workspace context
```

This meant:
- ✗ Single workspace queries showed just nicknames (`stories`)
- ✓ Multi-workspace collisions showed qualified names (`trass.stories`, `koii.stories`)
- ✗ Inconsistent display depending on what was loaded

## Solution

Simplified the function to **always** use qualified names:

```python
def generate_source_breakdown(multi_source_data):
    """Generate a dictionary of source names to page counts.
    
    Always uses qualified names (workspace.database) for clarity.
    """
    if not multi_source_data:
        return None
    
    breakdown = {}
    for source_key, pages in multi_source_data.items():
        # Always use the full qualified name (workspace.database)
        # This provides clarity about which workspace each database belongs to
        source_name = source_key
            
        breakdown[source_name] = len(pages)
    
    return breakdown
```

## Results

**Before**:
```
🐙 maia chat
Pages loaded: 14
stories: 14        ← Unclear which workspace
```

**After**:
```
🐙 maia chat
Pages loaded: 14
trass.stories: 14  ← Clear workspace identification ✓
```

**Multi-workspace example**:
```
🐙 maia chat
Pages loaded: 316
trass.gmail: 255
koii.gmail: 58
trass.stories: 3   ← All clearly distinguished!
```

## Benefits

✅ **Always clear**: Users always know which workspace data came from

✅ **Consistent**: Same display format regardless of collision detection

✅ **Matches results**: Display matches the internal grouping structure

✅ **No ambiguity**: No more guessing "which stories database is this?"

✅ **Simpler code**: Removed complex collision detection logic

## Impact on Users

This change is purely visual in the chat initialization message. It provides better clarity without changing any functionality:

- Natural language queries still work the same
- Data loading is unchanged
- Only the display format is clearer

## Testing

```python
test_data = {
    'trass.stories': [1, 2, 3],
    'koii.stories': [4, 5],
    'trass.gmail': [6],
}

breakdown = generate_source_breakdown(test_data)
# Returns:
# {
#   'trass.stories': 3,
#   'koii.stories': 2,
#   'trass.gmail': 1
# }
```

All qualified names preserved! ✅

## Files Modified

**`promaia/chat/interface.py`**:
- Simplified `generate_source_breakdown()` to always use qualified names
- Removed collision detection logic
- Added clearer documentation

## Related Improvements

This complements the previous fixes:
- Qualified name grouping (data structure)
- Database name normalization (queries)
- Universal adapter pattern (loading)

Now the **display** matches the **data structure** - complete consistency! 🎉
