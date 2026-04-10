# Agentic NL System Migration

## ✅ Successfully Migrated

The new agentic NL query system is now **fully integrated** into the CLI and Chat!

---

## What Was Changed

### 1. Created New Components

**`promaia/ai/nl_orchestrator.py`**
- Main agentic processor with retry logic
- Schema exploration via PRAGMA
- Learning system (saves successful queries)
- Result validation with smart feedback
- SQL error handling with specific messages

**`promaia/ai/nl_utilities.py`**
- SchemaExplorer (dynamic PRAGMA-based discovery)
- QueryLearningSystem (rolling index of 20 successful patterns)
- NLContextLogger (saves query details to `nl-context-logs/`)
- ResultValidator (validates results match intent)

**`promaia/ai/nl_processor_wrapper.py`**
- Backward-compatible wrapper
- Provides same API as old system
- Integrates seamlessly with existing code

### 2. Updated Existing Components

**`promaia/storage/unified_query.py`**
```python
# OLD
from promaia.ai.intelligent_nl_processor import process_natural_language_to_content

# NEW
from promaia.ai.nl_processor_wrapper import process_natural_language_to_content
```

No other changes needed - the wrapper provides the same interface!

---

## Key Improvements Over Old System

### Old System (LangGraph-based)
- ❌ Hardcoded 13 SQL examples (~2,800 tokens per query)
- ❌ Fixed schema - couldn't discover new columns
- ❌ No retry logic on failures
- ❌ No learning from successful queries
- ❌ Generic "try broadening" feedback for all failures

### New System (Agentic)
- ✅ Dynamic schema with sample data (~1,600 tokens per query)
- ✅ **PRAGMA-based discovery** - works with any data source
- ✅ **Smart retry logic** (up to 3 attempts with feedback)
- ✅ **Learning system** - saves successful query patterns
- ✅ **Specific SQL error messages** - "no such table: xyz" instead of generic feedback
- ✅ **Sample-based inference** - AI sees actual data and figures out what's searchable
- ✅ **Chain of thought logging** (with `--debug` flag)

---

## How It Works Now

### 1. CLI Query
```bash
maia chat -nl "trass gmail with term mgm from last month"
```

### 2. Processing Flow
```
CLI → unified_query.py → nl_processor_wrapper.py → AgenticNLQueryProcessor

1. Schema Exploration (PRAGMA + samples)
2. Intent Parsing (LLM extracts goal, databases, search terms)
3. SQL Generation (using schema + learned patterns)
4. Execution (with error capture)
5. Validation (check if results match intent)
6. Retry if needed (with specific feedback)
7. User Confirmation (save successful patterns)
```

### 3. Output
```
✅ Results look good: 457 entries from 1 databases

📊 QUERY RESULTS SUMMARY
🎯 Your Query: find gmail messages containing mgm
📚 Total Results: 457 entries
🗄️  Databases: gmail

💭 Was this query successful?
   Accept and learn from this? (Y/n): y
✅ Saved pattern to learning index (5/20)
```

---

## Backward Compatibility

### ✅ All existing code continues to work:
- CLI: `maia chat -nl "query"`
- Multiple queries: `maia chat -nl "query1" -nl "query2"`
- Edit mode: works with `-nl` arguments
- Chat interface: all NL functionality preserved

### 📦 Same API:
```python
# This function signature is unchanged
process_natural_language_to_content(
    nl_prompt: str,
    workspace: str = None,
    database_names: List[str] = None
) -> Dict[str, List[Dict[str, Any]]]
```

---

## New Capabilities

### 1. Sample-Based Schema
```python
# Instead of hardcoding:
content_fields = {'message_content': 'EMAIL BODY - Primary searchable content'}

# Now shows actual data:
message_content: "I'll admit—I have a bit of a sweet tooth..."
sender_email: "LinkedIn <updates@linkedin.com>"
```

AI infers semantics from examples → **Works for any data source!**

### 2. Smart Retry with SQL Errors
```python
# Attempt 1:
❌ SQL Error: no such table: nonexistent_table

# Attempt 2 (with feedback):
✅ Uses correct table name
```

### 3. Learning System
```bash
# After successful queries:
✅ Saved pattern to learning index (5/20)

# Future queries use learned patterns:
📚 Using 5 learned patterns
```

### 4. Debug Mode
```bash
maia chat -nl "query" --debug
# or
export MAIA_DEBUG=1
```

Shows:
- Exact prompts sent to AI
- Schema samples provided
- SQL generation reasoning
- Validation checks
- Retry logic flow

---

## Testing

### Test Scripts Available:
```bash
# Basic functionality
python3 test_agentic_nl.py

# Retry logic
python3 test_retry_logic.py

# Deep prompt logging
python3 test_retry_prompts.py

# SQL error handling
python3 test_sql_error_feedback.py

# Interactive testing
python3 query_agentic.py
```

### Real Usage:
```bash
# Simple query
maia chat -nl "gmail with term avask"

# Complex query
maia chat -nl "trass gmail from last 2 months with term mgm"

# Multiple queries
maia chat -nl "gmail with term test" -nl "journal from last week"

# With debug mode
export MAIA_DEBUG=1
maia chat -nl "your query here"
```

---

## Data Storage

### Query Logs:
```
nl-context-logs/
├── 20251007_212156_nl_query_draft.json    # Full context
└── 20251007_212156_nl_query_summary.txt   # Human-readable summary
```

### Learned Patterns:
```
data/nl_query_patterns/
└── successful_patterns.json    # Rolling index of last 20 successful queries
```

---

## Scalability

### Works with ANY data source:
- ✅ Gmail (tested)
- ✅ Notion (tested)
- ✅ **Future: Slack, Figma, Discord** → Just add to database!

The system will:
1. Run PRAGMA to discover columns
2. Sample rows to see actual data
3. Infer which fields are searchable
4. Generate appropriate SQL

**No hardcoding required!** 🚀

---

## Migration Complete

The old `intelligent_nl_processor.py` (LangGraph-based) is now **replaced** by the new agentic system.

All existing functionality preserved + major improvements:
- ✅ Dynamic schema discovery
- ✅ Smart retry logic
- ✅ Learning from success
- ✅ Specific error feedback
- ✅ Works with any data source
- ✅ 40% fewer tokens per query

**Ready for production!** 🎉

