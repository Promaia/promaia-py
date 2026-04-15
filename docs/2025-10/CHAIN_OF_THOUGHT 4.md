# Chain of Thought Logging

## Architecture: NOT Using LangGraph

The agentic NL system uses a **simple sequential flow**, not LangGraph:

```
1. Parse Intent    (LLM call → JSON)
      ↓
2. Generate SQL    (LLM call with learned patterns)
      ↓
3. Execute SQL     (SQLite query)
      ↓
4. Validate        (Check if results match intent)
      ↓
5. Retry?          (If validation fails, go back to step 2)
      ↓
6. Confirm         (Ask user if successful)
```

**Why not LangGraph?**
- Simpler to understand and debug
- No dependency on `langchain_core`
- Full control over retry logic
- Easier to add custom validation

---

## Enable Chain of Thought Logging

### Method 1: Command Line Flag

```bash
python3 query_agentic.py --debug
```

### Method 2: Environment Variable

```bash
export MAIA_DEBUG=1
python3 query_agentic.py
```

### Method 3: Programmatic

```python
from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor

processor = AgenticNLQueryProcessor(debug=True)
result = processor.process_query("your query here")
```

---

## What You'll See

### Normal Mode (Default)
```
🤖 Processing query: 'trass gmail with term mgm'
🔍 Step 1: Exploring database schema...
🧠 Step 2: Parsing intent...
⚙️  Step 3: Generating SQL query...
🔍 Executing query...
✅ Step 4: Validating results...
```

### Debug Mode (Chain of Thought)
```
🤖 Processing query: 'trass gmail with term mgm'
🔍 Step 1: Exploring database schema...
🧠 Step 2: Parsing intent...

======================================================================
🧠 CHAIN OF THOUGHT: Intent Parsing
======================================================================

📤 Prompt to LLM (anthropic):
Parse this natural language query into structured intent:

Query: "trass gmail with term mgm"

Available databases: ['gmail', 'journal', 'stories', ...]

Available tables and their columns:
...

📥 LLM Response:
{
  "goal": "find gmail messages containing mgm",
  "databases": ["trass.gmail", "gmail"],
  "search_terms": ["mgm"],
  "date_filter": {"days_back": 30, "description": "within last 1 month"}
}

⚙️  Step 3: Generating SQL query...

======================================================================
⚙️  CHAIN OF THOUGHT: SQL Generation (Attempt 1)
======================================================================

📤 SQL Generation Prompt:
   Intent: find gmail messages containing mgm
   Databases: trass.gmail, gmail
   Search terms: mgm
   Using 4 learned patterns

📥 Generated SQL:
SELECT page_id, title, email_date, sender_email, database_name 
FROM unified_content 
WHERE database_name IN ('gmail', 'trass.gmail')
AND (title LIKE '%mgm%' OR metadata LIKE '%mgm%')
LIMIT 1000

======================================================================
⚡ CHAIN OF THOUGHT: SQL Execution
======================================================================

🔍 Executing query against: data/hybrid_metadata.db

✅ Execution successful
   Returned 156 rows
   Sample row keys: ['page_id', 'title', 'email_date', 'sender_email', 'database_name']

======================================================================
✅ CHAIN OF THOUGHT: Result Validation
======================================================================

🔍 Validation checks:
   • Results exist: ✓
   • Count: 156
   • Database match: {'gmail'} vs expected {'trass.gmail', 'gmail'}
   • Search terms check: ['mgm']

🎯 Validation result: PASS ✓
   Reason: Results look good: 156 entries from 1 databases
```

---

## Retry Flow Example

When validation fails, you'll see the retry reasoning:

```
⚠️  No results found. Try broadening the search terms or date range.

🔄 Retry attempt 1/2

======================================================================
⚙️  CHAIN OF THOUGHT: SQL Generation (Attempt 2)
======================================================================

🔄 Retry Reasoning:
   Previous attempt failed: No results found. Try broadening the search terms
   Strategy: Adjusting query based on feedback

📤 SQL Generation Prompt:
   Intent: [same as before]
   Databases: [same]
   Search terms: [same]
   Using 4 learned patterns
   
PREVIOUS ATTEMPT FAILED:
No results found. Try broadening the search terms or date range.

Please adjust the query to fix this issue.

📥 Generated SQL:
[NEW SQL with adjusted approach]
```

---

## Understanding the Chain

### 1. Intent Parsing
- **Input**: Natural language query
- **Process**: LLM extracts goal, databases, search terms, date filter
- **Output**: Structured JSON intent
- **Debug shows**: Exact prompt sent, LLM response

### 2. SQL Generation  
- **Input**: Intent + learned patterns + dynamic schema
- **Process**: LLM generates SQL following patterns
- **Output**: SQLite query
- **Debug shows**: Which learned patterns used, generated SQL

### 3. SQL Execution
- **Input**: Generated SQL
- **Process**: Run against SQLite database
- **Output**: List of results
- **Debug shows**: Connection, row count, sample columns

### 4. Validation
- **Input**: Results + original intent
- **Process**: Check if results match what user wanted
- **Output**: PASS/FAIL + reason
- **Debug shows**: Each validation check (existence, count, database match, search terms)

### 5. Retry Logic
- **Trigger**: Validation fails
- **Process**: Add feedback to intent, generate new SQL
- **Max attempts**: 3 total (1 initial + 2 retries)
- **Debug shows**: Why previous failed, what strategy is being tried

---

## Comparison with LangGraph

**Our Sequential Flow:**
```python
# Simple and explicit
intent = parse_intent(query)
for attempt in range(3):
    sql = generate_sql(intent, attempt)
    results = execute_sql(sql)
    if validate(results, intent):
        break
    intent['feedback'] = validation_message
```

**LangGraph Would Be:**
```python
# More complex state machine
workflow = StateGraph(State)
workflow.add_node("parse", parse_node)
workflow.add_node("generate", generate_node)
workflow.add_node("execute", execute_node)
workflow.add_node("validate", validate_node)
workflow.add_conditional_edges("validate", should_retry, ...)
graph = workflow.compile()
result = graph.invoke(initial_state)
```

**Our approach wins because:**
- ✅ Easier to understand
- ✅ Simpler to debug
- ✅ No extra dependencies
- ✅ Full control over flow
- ✅ Chain of thought logging built-in

---

## Try It Now

```bash
# Enable debug mode
python3 query_agentic.py --debug

# Then try a query
🔍 Your query: gmail with term avask
```

You'll see the complete chain of thought for every step!

