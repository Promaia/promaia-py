# Email Response Learning System

## Overview

The learning system stores successful email responses to learn the user's writing style and tone. Patterns are stored **per workspace** to keep different professional contexts separate.

## Directory Structure

```
data/mail_response_patterns/
├── koii/
│   └── successful_responses.json
├── trass/
│   └── successful_responses.json
└── [other workspaces]/
    └── successful_responses.json
```

## How It Works

### 1. Learning from Sent Emails

When you **send an email** from the review UI:
1. The system saves a pattern containing:
   - Inbound email (from, subject, snippet)
   - Your response (subject, body, length)
   - Metadata (workspace, AI model, timestamp)
2. Pattern is saved to workspace-specific file
3. Only the **last 20** patterns are kept (rolling index)

### 2. Generating New Responses

When generating a draft:
1. System loads patterns for **current workspace only**
2. Includes up to 10 most recent examples in the AI prompt
3. AI learns tone, style, verbosity from these examples
4. Each workspace maintains its own distinct style

## File Format

```json
[
  {
    "id": "20251020_180123",
    "timestamp": "2025-10-20T18:01:23.456789+00:00",
    "inbound": {
      "from": "sender@example.com",
      "subject": "Email Subject",
      "body_snippet": "First 150 chars of email..."
    },
    "response": {
      "subject": "Re: Email Subject",
      "body": "Your complete response...",
      "tone": "professional",
      "length": 45
    },
    "metadata": {
      "workspace": "trass",
      "ai_model": "anthropic",
      "context_sources": "{...}",
      "timestamp": "2025-10-20T18:01:23.456789+00:00"
    }
  }
]
```

## Benefits of Per-Workspace Patterns

1. **Context Separation**: Personal emails (koii) vs business emails (trass)
2. **Tone Consistency**: Different workspaces can have different communication styles
3. **Better Learning**: AI learns from relevant examples only
4. **Privacy**: Workspace patterns don't leak across contexts

## Clearing Patterns

### Clear specific workspace:
```bash
rm data/mail_response_patterns/trass/successful_responses.json
```

### Clear all workspaces:
```bash
rm -rf data/mail_response_patterns/*/successful_responses.json
```

### Via Python:
```python
from promaia.mail.learning_system import EmailResponseLearningSystem

learning = EmailResponseLearningSystem(workspace="trass")
learning.clear_patterns()
```

## Pattern Limit

- **Max patterns per workspace**: 20
- **Patterns in prompt**: Up to 10 (most recent)
- **Storage**: Rolling index (oldest deleted when limit reached)

## Checking Pattern Stats

```python
from promaia.mail.learning_system import EmailResponseLearningSystem

learning = EmailResponseLearningSystem(workspace="trass")
stats = learning.get_patterns_summary()

print(f"Patterns: {stats['count']}")
print(f"Average length: {stats['average_length']} words")
print(f"Oldest: {stats['oldest']}")
print(f"Newest: {stats['newest']}")
```

## Integration Points

### ResponseGenerator
- Loads workspace-specific patterns when generating drafts
- Caches learning systems by workspace for performance

### DraftChatInterface
- Uses workspace from draft for saving sent emails
- Ensures patterns saved to correct workspace

### EmailReviewUI
- Saves to learning system when user sends via "Send & Archive"
- Automatically uses draft's workspace

## Migration Notes

**Old structure** (single file for all workspaces):
```
data/mail_response_patterns/successful_responses.json
```

**New structure** (per-workspace):
```
data/mail_response_patterns/
├── koii/successful_responses.json
└── trass/successful_responses.json
```

If you have an old `successful_responses.json`, you can manually move patterns to workspace directories or let the system start fresh.

