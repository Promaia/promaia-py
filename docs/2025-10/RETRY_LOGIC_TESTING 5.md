# Testing the Retry Logic

## Two Test Scripts

### 1. `test_retry_logic.py` - Basic Retry Test

Shows the retry flow at a high level:
- SQL generation attempts
- Validation feedback
- How errors are injected
- Final results

```bash
python3 test_retry_logic.py
```

**What it does:**
- Generates a query
- Injects `WHERE 1=0` to force 0 results
- Shows validation feedback: "No results found"
- Retries with that feedback
- Shows how the system learns

---

### 2. `test_retry_prompts.py` - Deep Prompt Logging

Shows EXACTLY what the AI sees at each step:
- Full intent parsing input/output
- Learned patterns being used
- Validation feedback incorporated into retry prompts
- Schema samples provided to AI
- Generated SQL analysis

```bash
python3 test_retry_prompts.py
```

**What it shows:**
```
🧠 STEP 1: INTENT PARSING
📤 Input to AI:
   User Query: 'trass gmail with term mgm from last month'
   Available Databases: [gmail, journal, stories...]

📥 AI Response:
   Goal: find gmail messages containing mgm
   Databases: [trass.gmail, gmail]
   Search Terms: [mgm]
   Date Filter: last month

⚙️  STEP 2: SQL GENERATION (Attempt 1)
📤 Context sent to AI:
   Intent Goal: find gmail messages containing mgm
   Target Databases: [trass.gmail, gmail]
   Search Terms: [mgm]

📚 Using 4 learned patterns:
   1. 'gmail with term avask' → 106 results
   2. 'all gmail entries with term avask' → 106 results

🗄️  Schema info provided:
   gmail_content (5094 rows):
   Sample rows (recent data):
     Row 1:
       message_content: "I'll admit—I have a sweet tooth..."
       sender_email: "LinkedIn <updates@linkedin.com>"
       ...

📥 AI Generated SQL:
   SELECT u.page_id, u.title, ...
   FROM unified_content u
   LEFT JOIN gmail_content g ON u.page_id = g.page_id
   WHERE ...

🔍 SQL Analysis:
   ✅ Searches message_content (email body)
   ✅ Joins with gmail_content
   📊 Searching 5 fields with LIKE

🐛 INJECTING TEST ERROR:
   Breaking SQL to force retry...

⚡ STEP 3: SQL EXECUTION
📊 Result: 0 rows

⚙️  STEP 2: SQL GENERATION (Attempt 2)
⚠️  Validation Feedback from Attempt 1:
   No results found. Try broadening the search terms or date range.

💡 The AI will now adjust the SQL based on this feedback
```

---

## What Gets Logged

### At Each Retry:

1. **Intent Context:**
   - User's goal
   - Target databases
   - Search terms
   - Date filters

2. **Learned Patterns:**
   - Which successful queries are being used as examples
   - Their result counts
   - Their SQL patterns

3. **Validation Feedback:**
   - What failed in the previous attempt
   - Specific error messages
   - Suggestions for improvement

4. **Schema Information:**
   - Sample rows with actual data
   - Column names and types
   - Which fields contain searchable content

5. **Generated SQL:**
   - Full SQL query
   - Analysis of what it searches
   - Whether it has proper JOINs

---

## How Retry Logic Works

```
Attempt 1:
  Generate SQL → Execute → Validate
                              ↓
                            FAIL: 0 results
                              ↓
                        Feedback: "No results found. Try broadening..."

Attempt 2:
  Generate SQL WITH feedback → Execute → Validate
                                            ↓
                                          FAIL: Still problematic
                                            ↓
                                      Feedback: "Search terms not found..."

Attempt 3:
  Generate SQL WITH updated feedback → Execute → Validate
                                                    ↓
                                                  SUCCESS!
```

### The Feedback Loop

The system adds validation feedback to the intent:
```python
intent['_validation_feedback'] = "No results found. Try broadening..."
```

On retry, this feedback is included in the prompt:
```
PREVIOUS ATTEMPT FAILED:
No results found. Try broadening the search terms or date range.

Please adjust the query to fix this issue.
```

The AI then modifies its approach based on this feedback.

---

## Run the Tests

### Quick Test (Basic):
```bash
python3 test_retry_logic.py
```

### Detailed Test (Full Logging):
```bash
python3 test_retry_prompts.py
```

### With Debug Mode (Even More Detail):
```bash
python3 query_agentic.py --debug
# Then type your query
```

---

## What You'll Learn

1. **How validation works** - What makes a query "fail" validation
2. **How feedback is incorporated** - Exact prompt changes on retry
3. **How learned patterns help** - Which examples guide SQL generation
4. **How schema samples work** - What the AI infers from sample data
5. **How retries adapt** - Different SQL strategies on each attempt

---

## Example Output

You'll see:
- ✅ **GREEN** = Success
- ⚠️  **YELLOW** = Retry/Warning
- ❌ **RED** = Failure/Error
- 📤 **Input to AI**
- 📥 **Output from AI**
- 🔄 **Retry with feedback**

The tests will show you the complete conversation between your system and the AI, including how it learns from failures!

