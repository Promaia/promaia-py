# Database Schema Notes for Agentic NL System

## Key Schema Insights

### Table Structure

1. **`generic_content`** (22,644 rows)
   - General content registry
   - Columns: `page_id`, `workspace`, `database_name`, `title`, `file_path`, `metadata`
   - Points to markdown files on disk
   - Use for: journal, stories, notion databases

2. **`gmail_content`** (5,090 rows) 
   - **STANDALONE** - does NOT join to generic_content
   - Columns: `page_id`, `subject`, `message_content`, `sender_email`, `email_date`, `database_id`
   - `database_id` contains email addresses (koii@trassgames.com, etc.)
   - **Query directly** - no JOIN needed!

3. **`notion_journal`**, **`notion_stories`**, **`notion_cms`**
   - Specialized tables for Notion databases
   - May or may not join to generic_content

### Important: Gmail Queries

**WRONG** (what the system was trying):
```sql
SELECT g.*, gc.database_name  
FROM gmail_content g
JOIN generic_content gc ON g.page_id = gc.page_id  -- ❌ page_ids don't match!
WHERE g.subject LIKE '%avask%'
```

**CORRECT**:
```sql
SELECT page_id, subject, email_date, sender_email, database_id
FROM gmail_content
WHERE subject LIKE '%avask%' OR message_content LIKE '%avask%'
LIMIT 100
```

### Date Handling

**Gmail dates** are in email format:
- `"Wed, 9 Jul 2025 18:43:45 +0000"`
- NOT ISO format

For date filtering on Gmail:
- Either parse the date string
- Or use substring matching: `WHERE email_date LIKE '%Jul 2025%'`
- Or skip date filtering and return all matching results

### Query Patterns

**For Gmail searches:**
```sql
-- Simple search
SELECT * FROM gmail_content 
WHERE subject LIKE '%search_term%' OR message_content LIKE '%search_term%'

-- With sender filter
SELECT * FROM gmail_content 
WHERE sender_email LIKE '%@domain.com%' 
  AND (subject LIKE '%term%' OR message_content LIKE '%term%')

-- Multiple terms (OR logic)
SELECT * FROM gmail_content 
WHERE subject LIKE '%term1%' OR subject LIKE '%term2%'
   OR message_content LIKE '%term1%' OR message_content LIKE '%term2%'
```

**For generic content (journal, stories):**
```sql
SELECT * FROM generic_content
WHERE database_name = 'journal'
  AND (title LIKE '%search_term%' OR metadata LIKE '%search_term%')
  AND created_time >= '2025-01-01'
```

## Recommendations for Agentic System

1. **Schema detection**: Recognize `generic_content` vs `gmail_content` as separate systems
2. **No JOINs for Gmail**: Query `gmail_content` directly
3. **Date handling**: Skip date filtering for Gmail or use string matching
4. **Learn patterns**: After successful queries, save these patterns to the learning index

## Example Successful Queries

1. **Gmail search (working)**:
   ```sql
   SELECT page_id, subject, email_date 
   FROM gmail_content 
   WHERE subject LIKE '%avask%' OR message_content LIKE '%avask%'
   LIMIT 100
   ```
   Result: 96 emails found

2. **Journal entries**:
   ```sql
   SELECT * FROM generic_content
   WHERE database_name = 'journal'
   AND created_time >= '2025-09-01'
   ORDER BY created_time DESC
   LIMIT 100
   ```

These patterns should be added to the learning system for future queries.

