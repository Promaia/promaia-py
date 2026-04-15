# AI Context Enhancement

**Date**: October 8, 2025  
**Status**: ✅ Implemented

## Overview

Enhanced the Agentic NL Query Processor to include comprehensive workspace configuration in the AI's initial context. This provides the AI with a better understanding of how databases are organized across workspaces.

## What Was Added

### 1. Workspace Configuration Loading

The processor now loads and provides the workspace configuration from `promaia.config.json` to the AI:

```python
def _load_workspace_config(self, config_file: str = "promaia.config.json") -> Dict[str, Any]:
    """Load workspace configuration to provide context to AI."""
```

This loads:
- List of available workspaces (e.g., `koii`, `trass`)
- Default workspace
- All databases with their:
  - Nickname
  - Description
  - Workspace assignment
  - Source type (notion, gmail, discord)
  - Default include settings

### 2. Formatted Context for AI

The configuration is formatted in a readable way for the AI:

```
=== WORKSPACE CONFIGURATION ===

Workspaces: koii, trass
Default: koii

Databases by Workspace:

  KOII workspace:
    • journal (journal): Personal journal entries
      Type: notion, Default: True
    • cms (cms): Content management system
      Type: notion, Default: True
    ...

  TRASS workspace:
    • trass.journal (journal): Trass Games journal entries
      Type: notion, Default: True
    • trass.stories (stories): Trass Games stories
      Type: notion, Default: True
    ...
```

### 3. Context Included in All AI Prompts

The workspace configuration is now included in:

1. **Intent Parsing** (`_parse_intent`): Helps AI understand which databases to target
2. **SQL Generation** (`_generate_sql_query`): Provides context for generating accurate queries
3. **Vector Query Generation** (`_generate_vector_query`): Helps extract semantic search terms

## Benefits

### Better Database Selection

The AI now understands:
- Which databases belong to which workspace
- What each database contains (based on description)
- Whether a database is included by default
- The relationship between workspaces and their databases

### More Accurate Queries

When a user asks "stories about international launch in trass", the AI can now:
1. Identify that "trass" refers to a workspace
2. Find all relevant databases in that workspace (stories, journal, cpj, gmail)
3. Generate queries that target the correct databases

### Example

**Query**: "stories about international launch in trass"

**Before**: Might miss some relevant databases or misunderstand the workspace context

**After**: AI correctly identifies:
```json
{
  "goal": "find stories about international product launches",
  "databases": ["trass.stories", "trass.journal", "trass.cpj", "trass.gmail"],
  "search_terms": ["international", "launch"]
}
```

## AI Context Summary

The AI now receives in every prompt:

1. ✅ **Workspace Configuration**: All workspaces and databases organized by workspace
2. ✅ **PRAGMA Schema**: Dynamic schema exploration with table structures
3. ✅ **Sample Data**: Representative rows from each table
4. ✅ **Learned Patterns**: Previously successful query patterns (SQL mode only)

## Files Modified

- `promaia/ai/nl_orchestrator.py`:
  - Added `_load_workspace_config()` method
  - Added `_format_workspace_config()` method
  - Updated `_parse_intent()` to include workspace context
  - Updated `_generate_sql_query()` to include workspace context
  - Updated `_generate_vector_query()` to include workspace context

## Testing

Verified that:
1. ✅ Config loads successfully from `promaia.config.json`
2. ✅ Formatting produces readable output for the AI
3. ✅ AI successfully uses context to identify correct databases
4. ✅ Works in both SQL and Vector modes
5. ✅ No linter errors introduced

## Usage

No changes required for users. The enhancement is automatic and transparent.

```bash
# Works exactly as before, but with better AI understanding
maia chat -nl trass "stories about international launch"
```

## Future Enhancements

Potential improvements:
- Add property filter examples to context
- Include database statistics (entry counts, date ranges) - already partially available
- Add common search patterns per database type
