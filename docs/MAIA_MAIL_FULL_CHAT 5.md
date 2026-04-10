# Maia Mail Full Chat - User Guide

## Overview

Maia Mail draft chat now has **full maia chat capabilities**! You can edit context, add sources, use browse mode, natural language queries, vector search, and MCP servers - all while refining email drafts.

## Quick Start

When you enter draft chat, you'll see a welcome message showing your context:

```
🐙 maia mail draft chat
Query: maia mail --draft abc123 -mc
Context loaded:
	message-context
	trass.gmail: 5
	trass.journal: 3
	trass.stories: 2
Model: Anthropic Claude Sonnet

Available commands:
  /send [#] - Send draft (default: latest)
  /d - Toggle draft list view, 💡 8 earlier draft(s) hidden
  /e - Edit context (sources, filters, message context)
  /s - Sync databases in current context
  /mcp [name] - Include MCP server context (e.g., /mcp search)
  /archive or /a - Archive this email
  /q - Return to draft list
  /model - Switch model
  /help - Show detailed help

💬 You: 
```

## New Features

### 1. Context Editing (`/e`)

Press `/e` to edit your context just like in maia chat:

```
💬 You: /e

🔧 Edit Context
Current command:
  maia mail --draft abc123 -mc

Message Context: ENABLED ✓

Options:
  • Edit command manually (shown below)
  • Ctrl+R for recent queries
  • Ctrl+B for browse mode
  • Press Enter alone to cancel

maia mail --draft abc123 -mc _
```

#### Keyboard Shortcuts

- **Ctrl+B**: Launch browse mode to select databases/channels
- **Ctrl+R**: View recent queries (coming soon)
- **Enter**: Cancel (if no changes)

### 2. Message Context Flag (`-mc`)

The `-mc` flag controls whether the initial draft context is included:

**With -mc (default):**
```
Context loaded:
	message-context           ← Initial context from draft log
	trass.gmail: 5
	trass.journal: 3
```

**Without -mc:**
```
Context loaded:
	trass.journal: 7

⚠️  Message context disabled - only user persona active
```

**To toggle:** Use `/e` and add/remove `-mc` from the command.

### 3. Add Additional Sources (`-s`)

Add more databases to your context:

```
maia mail --draft abc123 -mc -s journal:7 -s stories:30
```

This adds:
- Last 7 days of journal entries
- Last 30 days of stories
- PLUS the original message context

### 4. Browse Mode (`-b`)

Launch the interactive database browser:

```
💬 You: /e

maia mail --draft abc123 -mc -b trass [press Ctrl+B]

🔍 Launching unified browser for: trass...
🔍 trass | Sources: 5 databases, 12 channels | ↑↓ Navigate SPACE Toggle ENTER Confirm

📄 Regular Sources:
☑       trass.journal:7
☑       trass.stories:7
☐       trass.gmail:7
☐       trass.cpj:7

💬 Discord Sources:
☐       trass.tg#plush-work:7
☐       trass.tg#merch-work:7
[...]
```

### 5. Natural Language Queries (`-nl`)

Use natural language to find relevant content:

```
maia mail --draft abc123 -mc -nl "emails about UK shipments"
```

The AI will search your databases for relevant content about UK shipments and add it to your context.

### 6. Vector Search (`-vs`)

Use semantic search to find similar content:

```
maia mail --draft abc123 -mc -vs "international shipping delays"
```

This finds documents semantically similar to your query.

### 7. MCP Servers (`/mcp`)

Include context from MCP (Model Context Protocol) servers:

```
💬 You: /mcp search

✅ Including MCP server: search
```

Available MCP servers are defined in your `mcp_servers.json`.

## Context Breakdown

The welcome message shows what's loaded:

- **message-context**: Initial draft context from the log file (only when `-mc` is present)
- **database-name: count**: Each database and how many documents are loaded
- **Model**: Which AI model you're using

## User Persona

The user persona from `prompts/maia_mail_prompt.md` is **always active** regardless of context flags. It defines your writing style and tone.

Only the message context (email history and project context from the initial draft) is controlled by the `-mc` flag.

## Example Workflows

### Workflow 1: Add Recent Journal Context

```
💬 You: /e
maia mail --draft abc123 -mc -s journal:7

✅ Context updated

🐙 maia mail draft chat
Context loaded:
	message-context
	trass.gmail: 5
	trass.journal: 7         ← Added

💬 You: make the response reference what I wrote about this in my journal
```

### Workflow 2: Remove Message Context

```
💬 You: /e
maia mail --draft abc123 -s journal:7

✅ Context updated

🐙 maia mail draft chat
Context loaded:
	trass.journal: 7

⚠️  Message context disabled - only user persona active

💬 You: write a fresh response using just my journal context
```

### Workflow 3: Add Natural Language Search

```
💬 You: /e
maia mail --draft abc123 -mc -nl "batch 2025-07 discussions"

✅ Context updated
🤖 Processing natural language query...
   ✅ Found 12 results

🐙 maia mail draft chat
Context loaded:
	message-context
	trass.gmail: 5
	trass.journal: 4         ← From NL search
	trass.stories: 3         ← From NL search

💬 You: reference the batch 2025-07 planning from my context
```

## Command Reference

### Draft Commands
- `/send [#]` - Send draft (number optional, defaults to latest)
- `/d` - Toggle showing all drafts vs latest only
- `/archive` or `/a` - Archive email and return to list
- `/q` - Return to draft list without archiving

### Context Commands
- `/e` - Edit context (sources, filters, message context)
- `/s` - Sync databases in current context
- `/mcp [name]` - Include MCP server context

### System Commands
- `/model` - Switch AI model
- `/help` - Show detailed help

## Tips

1. **Start with message context** (`-mc`) to see what the AI originally had
2. **Add sources incrementally** - use `/e` to add `-s journal:7`, test the draft, then add more if needed
3. **Use natural language** (`-nl`) when you're not sure which database has what you need
4. **Toggle drafts** (`/d`) to see your revision history
5. **Archive liberally** (`/archive`) - it just removes from queue, doesn't delete

## Technical Notes

### Context Priority

When you have multiple context sources, they're combined:
1. User persona (always active)
2. Message context (if `-mc` is present)
3. Additional sources (from `-s`, `-b`, `-nl`, `-vs`)
4. MCP servers (from `/mcp`)

### Context Log Files

Initial message context is loaded from:
```
context_logs/mail_draft_logs/YYYYMMDD-HHMMSS_initial_draft_SUBJECT.txt
```

The AI parses the `=== EMAIL HISTORY ===` and `=== PROJECT CONTEXT ===` sections to determine what was included in the original draft.

### Copy-Friendly UI

All displays follow copy-friendly Rich principles:
- No Unicode box characters
- Clean separators using dashes
- Everything copies cleanly to clipboard
- Professional, minimal aesthetic

## Troubleshooting

**Q: My context isn't showing up**
- Check that you added the flag correctly (e.g., `-mc` not `--mc`)
- Verify the database exists: `maia database list`
- Check if days filter is too restrictive (try increasing the number)

**Q: Browse mode (Ctrl+B) isn't working**
- Currently in development - use manual `-b workspace` flag instead
- Coming in a future update

**Q: Message context shows wrong databases**
- The context is loaded from the most recent draft log file
- If you regenerated the draft recently, that's the log being used

**Q: Changes aren't applying**
- After editing context with `/e`, wait for the "✅ Context updated" message
- The welcome message will refresh automatically

## See Also

- [Maia Mail README](MAIA_MAIL_README.md) - Overview of email system
- [Copy-Friendly Rich](COPY_FRIENDLY_RICH.md) - UI design principles
- [Vector Search](VECTOR_SEARCH.md) - How semantic search works

