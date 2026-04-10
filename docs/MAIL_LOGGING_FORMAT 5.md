# Email Draft Logging Format

## Overview

Email draft logs are saved to `context_logs/mail_draft_logs/` with structured, greppable headers.

## Log Structure

All section headers use `=== SECTION NAME ===` format for easy searching:

```bash
grep "=== " log_file.txt
```

### Complete Log Format

```
=== MAIA MAIL - INITIAL DRAFT ===
Timestamp: 20251020-175159
Model: anthropic
Workspace: trass
From: sender@example.com
Subject: Email Subject
Context Sources: 20 relevant documents

==================================================
FULL PROMPT SENT TO AI:
==================================================

=== USER PERSONA ===
I am Koii's digital twin...
[Persona with filled date/time variables]

=== YOUR PREVIOUS EMAIL RESPONSES (X examples) ===
[Learned patterns from last 20 successful sends]

=== THREAD HISTORY ===
[Complete email thread with all messages]

=== RELEVANT CONTEXT FROM KNOWLEDGE BASE ===
Found X relevant documents from your knowledge base:

[1] Document Title
    Database: source_db | Relevance: 85%
    [Full document content...]

[2] Next Document
    Database: another_db | Relevance: 78%
    [Full document content...]

=== LATEST MESSAGE TO RESPOND TO ===
From: sender@example.com
Subject: Email Subject
Date: 2025-10-20

[Instructions for AI response generation]
```

## Key Features

1. **Greppable Headers**: All sections use `=== HEADER ===` format
2. **Full Context**: Complete documents with content (not summaries)
3. **Metadata**: Timestamp, model, workspace, sender in header
4. **Date/Time**: Variables filled with actual values
5. **No Redundancy**: Removed broken summary section

## Log Locations

- **Initial Drafts**: `context_logs/mail_draft_logs/`
  - Generated during `maia mail -p` (process) or `maia mail -r` (refresh)
  - Filename includes subject: `20251020-175159_initial_draft_SUBJECT_TRUNCATED.txt`

- **Refinements**: `context_logs/mail_context_logs/`
  - Generated when user refines draft in chat
  - Filename: `20251020-175159_refinement_prompt.txt`

## Vector Search

Vector search is working correctly and provides:
- 20 most relevant documents from knowledge base
- Similarity scores (0.0-1.0)
- Full document content
- Source database names

Documents are stored in `context.relevant_docs` with structure:
```python
{
    'page_id': str,
    'title': str,
    'database': str,
    'similarity': float,  # 0.0-1.0
    'content_snippet': str
}
```

## Learned Patterns

Learned patterns stored per workspace in: `data/mail_response_patterns/{workspace}/successful_responses.json`
- Each workspace has its own separate patterns
- Rolling index of last 20 successful email sends per workspace
- Used to learn user's writing style and tone for that workspace
- Keeps personal (koii) and business (trass) styles separate
- Can be cleared by deleting workspace folder or file
- See `MAIL_LEARNING_SYSTEM.md` for details

## Changes Made (2025-10-20)

1. ✅ Fixed section headers to use `=== ===` format
2. ✅ Added `=== USER PERSONA ===` header
3. ✅ Removed redundant broken context summary
4. ✅ Filled date/time variables with actual values
5. ✅ Cleared test learned patterns

