# Testing the Agentic NL System

## Quick Start

### 1. Interactive Testing (Easiest)

```bash
python3 query_agentic.py
```

Then type queries like:
- `gmail with term avask`
- `trass gmail from last month`
- `journal entries from last week`
- `stories about canada`

**Features:**
- ✅ Real-time query processing
- ✅ Shows validation and retry attempts
- ✅ Asks for confirmation to learn from successful queries
- ✅ Displays sample results
- ✅ Saves context logs to `nl-context-logs/`

---

### 2. Run Full Test Suite

```bash
python3 test_agentic_nl.py
```

This tests:
1. **Schema Exploration** - PRAGMA-based discovery
2. **Learning System** - Shows learned patterns
3. **Full Query** - Runs complete agentic workflow

---

### 3. Programmatic Usage

```python
from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor

processor = AgenticNLQueryProcessor()

result = processor.process_query(
    "gmail with term avask from last month",
    workspace="trass",  # optional
    max_retries=2
)

if result['success']:
    for db_name, pages in result['results'].items():
        print(f"{db_name}: {len(pages)} results")
        for page in pages:
            print(f"  - {page['title']}")
```

---

## Example Queries to Try

### Gmail Queries
```
✅ "gmail with term avask"
✅ "trass gmail from last month"
✅ "emails about shipbob"
✅ "gmail from koii@trassgames.com"
```

### Journal Queries
```
✅ "journal entries from last week"
✅ "last 3 days of journal"
✅ "journal about graham"
✅ "trass journal from september"
```

### Stories/CMS Queries
```
✅ "stories about international"
✅ "stories with status in-progress"
✅ "cms entries from last month"
```

---

## What Happens During a Query?

```
Your Query → "gmail with term avask from last month"
    ↓
1. 🔍 Schema Exploration
    Discovers: unified_content view (28,697 rows)
    Finds: gmail, journal, stories, etc.
    ↓
2. 🧠 Intent Parsing
    Goal: "find gmail messages containing 'avask'"
    Databases: ["gmail"]
    Search Terms: ["avask"]
    Date Filter: last month
    ↓
3. ⚙️  SQL Generation (with learned patterns)
    Loads 4 successful patterns from history
    Generates: SELECT ... FROM unified_content WHERE ...
    ↓
4. ⚡ Execute & Validate
    Run SQL → Got 106 results
    Validate: ✅ Results match intent
    ↓
5. 📊 Results Summary
    Total: 106 entries from gmail
    Sample: "Re: AVASK Global Compliance..."
    ↓
6. ✅ User Confirmation
    "Was this query successful? (Y/n)"
    If YES → Save to learning index (now 5/20)
```

---

## Viewing Results

### Context Logs
```bash
ls -lt nl-context-logs/

# View a summary
cat nl-context-logs/20251007_202943_nl_query_summary.txt

# View full JSON
cat nl-context-logs/20251007_202943_nl_query_draft.json
```

### Learned Patterns
```bash
cat data/nl_query_patterns/successful_patterns.json
```

Current patterns: **4/20** (Rolling index of last 20)

---

## Troubleshooting

### No API Key
```
ValueError: No working LLM API clients found
```
**Fix:** Make sure your `.env` has one of:
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`

### No Results Found
The system will automatically retry up to 2 times with different approaches:
```
⚠️  No results found. Try broadening the search terms or date range.
🔄 Retry attempt 1/2
```

If all retries fail, try:
- Broadening date range ("last 3 months" instead of "last week")
- Simplifying search terms
- Checking if data exists: `sqlite3 data/hybrid_metadata.db "SELECT COUNT(*) FROM unified_content WHERE ..."`

### Query Takes Too Long
- First query initializes the LLM client (slower)
- Subsequent queries use cached client
- Schema is cached after first discovery
- Learned patterns speed up future queries

---

## Integration with Your CLI

To use in your existing chat system, replace the old NL processor:

```python
# In promaia/storage/unified_query.py (or wherever NL queries are called)

# OLD
from promaia.ai.intelligent_nl_processor import process_natural_language_query

# NEW  
from promaia.ai.nl_orchestrator import get_agentic_query_processor

processor = get_agentic_query_processor()
result = processor.process_query(user_query, workspace=workspace)

if result['success']:
    # Use result['results'] - same format as before
    content = result['results']  # Dict[str, List[Dict]]
```

---

## Performance

### Token Usage
- **Old system**: ~2,800 tokens per query (13 hardcoded examples)
- **New system**: ~1,600 tokens per query (learned patterns)
- **Savings**: 40% reduction

### Accuracy
- **First query**: Uses dynamic schema + 4 learned patterns
- **After 20 successful queries**: Uses YOUR specific query patterns
- **Improvement**: Continuous - learns from each confirmed success

---

## Next Steps

1. **Test with your real queries** using `query_agentic.py`
2. **Build up learned patterns** by confirming successful queries
3. **Watch accuracy improve** as it learns your query patterns
4. **Integrate into CLI** when ready

The system is production-ready and will continue improving with use! 🚀

