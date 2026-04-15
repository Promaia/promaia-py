# Dual Vector Search Strategy

## Problem

When generating email responses, the AI needs context from both:
1. **Email threads** (Gmail) - for conversation history and patterns
2. **Project documentation** (Notion, Discord, etc.) - for business context and facts

However, Gmail email threads naturally dominate vector similarity searches for email-related queries because:
- Emails use similar language to the query
- There are thousands of email threads in the database
- Semantic similarity scores for emails are typically much higher than for project docs

This meant that ALL top results were Gmail emails, crowding out important project context.

## Solution: Dual Vector Search with Filter-Level Separation

Instead of fetching all results and filtering after, we perform **TWO SEPARATE vector searches** with different database filters applied at the ChromaDB query level:

### Search 1: Gmail Only
```python
gmail_filters = {
    "$and": [
        {"workspace": {"$eq": workspace}},
        {"database_name": {"$in": ["gmail", f"{workspace}.gmail"]}}
    ]
}
gmail_results = vector_db.search(
    query_text=search_query,
    filters=gmail_filters,
    n_results=10,
    min_similarity=0.3  # Quality threshold
)
```

### Search 2: Non-Gmail Only
```python
non_gmail_filters = {
    "$and": [
        {"workspace": {"$eq": workspace}},
        {"database_name": {"$ne": "gmail"}},
        {"database_name": {"$ne": f"{workspace}.gmail"}}
    ]
}
non_gmail_results = vector_db.search(
    query_text=search_query,
    filters=non_gmail_filters,
    n_results=5,
    min_similarity=0.0  # NO threshold - always include top 5
)
```

### Result Interleaving

Results are interleaved to ensure balanced representation:
```
[1] Gmail (96%)
[2] Non-Gmail: journal (66%)
[3] Gmail (94%)
[4] Non-Gmail: stories (64%)
[5] Gmail (91%)
[6] Non-Gmail: stories (62%)
...
```

## Benefits

1. **Guaranteed Diversity**: Top 5 non-Gmail documents always included regardless of score
2. **Quality Gmail Context**: Gmail results still meet minimum relevance threshold (0.3)
3. **Balanced Context**: AI sees both email patterns AND project facts
4. **No Crowding**: Project docs can't be pushed out by semantically similar emails

## Implementation

Located in: `promaia/mail/context_builder.py`

The `ResponseContextBuilder.build_context()` method performs both searches and combines results before loading full document content.

## Example Output

For a query about "UK shipment logistics":

**Gmail Results (10):**
- RE: BATCH 2025-07 UK SHIPMENT (96%)
- 回复: RE: BATCH 2025-07 US SHIPMENT (91%)
- RE: BATCH 2025-07 UK SHIPMENT (88%)
- etc.

**Non-Gmail Results (5):**
- [journal] 2025-06-12 (66%)
- [stories] Batch 2025-05 WROs (64%)
- [stories] International shipping contract (62%)
- [journal] 2025-06-24 (62%)
- [stories] UK 2025-07 Temp Storage (60%)

## Key Insight

The filter **must** be applied at the ChromaDB query level, not post-processing. With thousands of highly similar Gmail threads, non-Gmail content would never appear in even the top 200 results of an unfiltered search.

## Log Format

Context is now separated in logs with distinct headers:

```
=== EMAIL HISTORY (5 relevant threads) ===

[1] Re: BATCH 2025-07 UK SHIPMENT
    Database: gmail | Relevance: 96%
    ...

[2] Re: BATCH 2025-07 US SHIPMENT
    Database: gmail | Relevance: 94%
    ...

=== PROJECT CONTEXT (5 relevant documents) ===

[1] 2025-06-12
    Database: journal | Relevance: 66%
    ...

[2] Batch 2025-05 WROs
    Database: stories | Relevance: 64%
    ...
```

This makes it easy to:
- Grep for specific sections: `grep "=== EMAIL HISTORY"`
- Verify balanced results
- Debug context issues

