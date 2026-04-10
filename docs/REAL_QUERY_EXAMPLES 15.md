# Real Query Examples for AI Training

## Purpose
This document replaces the fake "proven query templates" in the natural language processor with **your actual query patterns**. Fill in real examples of how you naturally ask for data, and I'll integrate them into the AI system.

## Current Status: USING FAKE EXAMPLES ❌

The AI is currently being trained on these **completely made-up** examples:

### Gmail (Fake Examples)
- `"emails about mgm"`
- `"emails from shipbob.com"`

### Journal (Fake Examples)  
- `"journal entries between feb-may 2025"`
- `"journal entries about productivity"`

### Discord (Fake Examples)
- `"discord messages about meeting"`

### Stories/Notion (Fake Examples)
- `"stories about technology"`

---

## YOUR REAL EXAMPLES ✅

**Status:** Populated with **10 actual queries** from your chat history and recents!

### 📧 GMAIL EXAMPLES

#### Example 1: Business Context Search  
```yaml
natural_query: "all trass gmail entries that include the term mgm"
description: "Find emails related to MGM business partner across workspace"
notes: "Uses workspace qualifier 'trass', searches for business partner name 'mgm'"
```

---

### 📝 JOURNAL EXAMPLES

#### Example 1: Specific Date Range Search
```yaml
natural_query: "find all the journal entries between february 2025 and may 2025"
description: "Get journal entries from a multi-month period"
notes: "Uses natural date format 'february 2025 and may 2025', formal tone with 'find all'"
```

#### Example 2: Person/Content Search
```yaml
natural_query: "find all the journal entries that relate to graham"
description: "Find journal entries mentioning specific person"
notes: "Person name 'graham' is key search term, uses 'relate to' phrasing"
```

#### Example 3: Recent Time-based Search
```yaml
natural_query: "last 3 weeks of koii journal entries"
description: "Get recent journal entries from specific workspace"
notes: "Uses 'last X weeks' pattern, workspace-specific 'koii journal entries'"
```

#### Example 4: Simple Recent Search
```yaml
natural_query: "koii journal entries from the last 7 days"
description: "Get very recent journal entries"
notes: "Common pattern: 'from the last X days', workspace qualifier"
```

#### Example 5: Cross-workspace Recent Search
```yaml
natural_query: "last 1 week of trass journals"
description: "Get recent entries from different workspace"
notes: "Uses 'trass' workspace, simplified to 'journals' instead of 'journal entries'"
```

---

### 📚 NOTION/CMS EXAMPLES

#### Example 1: Person Search in Notion
```yaml
natural_query: "koii notion entries that contain the word eddie"
description: "Find Notion content mentioning specific person"
notes: "Person name 'eddie' as search term, uses 'contain the word' phrasing"
```

---

### 🔍 COMPLEX/CROSS-DATABASE EXAMPLES

#### Example 1: Multi-month Pattern Query
```yaml
natural_query: "a couple days of journal entries from every month since 2024-12"
description: "Sample entries across multiple months for pattern analysis"
notes: "Complex time pattern: 'every month since 2024-12', casual tone 'a couple days'"
```

#### Example 2: Browse + Natural Language
```yaml
natural_query: "7 days of koii journal entries from each of the last 7 months"
description: "Multi-month sampling with specific day count per month"
notes: "Used with browse mode, complex temporal pattern spanning months"
```

---

## EXAMPLE FORMAT

Here's a filled-in example to show the format:

```yaml
natural_query: "show me all emails from last month about the canada project"
description: "Find recent emails related to a specific project"
notes: "Combines time filtering with content search, project names are important context"
```

## WHAT HAPPENS NEXT

Now that we have your real query patterns:

1. **✅ Analyzed your patterns** - Found 10 real queries from your history
2. **🔍 Key patterns identified:**
   - **Business context**: "mgm" (real partner), "graham", "eddie" (real people)  
   - **Natural dates**: "february 2025 and may 2025", "since 2024-12"
   - **Casual time phrases**: "a couple days", "last 3 weeks", "from the last 7 days"
   - **Workspace awareness**: "koii journal entries", "trass gmail", "trass journals"
   - **Complex temporal logic**: "from each of the last 7 months"
3. **⏭️ Next steps:**
   - Replace fake examples in `langgraph_query_system_new.py`
   - Generate SQL templates based on these real patterns  
   - Test the AI with your actual query style

## YOUR DATA CONTEXT

For reference, here are your actual databases:
- **Gmail:** 5,601 entries
- **Journal:** 396 entries (koii), plus trass.journal  
- **Stories:** 476 entries
- **Discord:** Multiple servers (yp, tg, dgs, etc.) with thousands of messages
- **CMS:** 34 entries
- **Projects, Epics, Awakenings:** Various Notion databases

**Fill in examples using your actual query style, and I'll make the AI understand you better!** 🎯
