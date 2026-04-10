# Maia Mail Thread Scrolling Enhancement

## Overview

Enhanced email thread display in Maia Mail with copy-friendly formatting and clear message position indicators. Users can now easily orient themselves when scrolling through multi-message email threads.

## Key Features

### 1. Message Position Indicators

Each message in a thread now displays its position:

```
Message: 1/5 ────────────────────────────────────────────────────────
From: sender@example.com
Date: Monday, October 20, 2025 at 3:45 PM PDT

[message content]

────────────────────────────────────────────────────────────────────
```

### 2. Color-Coded Messages

Messages alternate between **cyan** (odd) and **blue** (even) for easy visual distinction:
- Message 1, 3, 5... → **Cyan** headers/footers
- Message 2, 4, 6... → **Blue** headers/footers

This makes it easy to track your position when scrolling through long threads.

### 3. Copy-Friendly Formatting

Following the rules from `docs/COPY_FRIENDLY_RICH.md`:

✅ **Simple separators** - Uses clean dashes (`─`) instead of complex box characters  
✅ **No box artifacts** - No `┏━━━┓` characters that break copy/paste  
✅ **Clean headers** - Plain text headers with color for visibility  
✅ **Proper structure** - Content maintains markdown hierarchy when copied  

### 4. Scroll Guidance

Multi-message threads include a helpful tip:
```
📜 Tip: Scroll up ↑ to see earlier messages in the thread
```

## Implementation Details

### New Module: `thread_formatter.py`

Location: `promaia/mail/thread_formatter.py`

**Key Functions:**

- `parse_thread_messages(conversation_body: str)` - Parses thread body into individual messages
- `format_thread_for_display(...)` - Formats threads with position indicators and colors
- `print_thread(...)` - Direct printing utility

### Updated Files

1. **`promaia/mail/review_ui.py`**
   - Updated `_render_draft_detail()` to use new thread formatter
   - Thread display now shows "Message: x/y" for each message

2. **`promaia/mail/draft_chat.py`**
   - Updated chat interface to use new thread formatter
   - Consistent display across all Maia Mail interfaces

### Visual Example

**Before:**
```
EMAIL THREAD (3 messages)

From: alice@example.com
Subject: Re: Project Update
Date: Monday, October 20, 2025 at 2:30 PM PDT

[entire thread as one block - hard to distinguish messages]
```

**After:**
```
EMAIL THREAD (3 messages)

Subject:  Re: Project Update
Scroll up ↑ to see earlier messages in the thread

Message: 1/3 ────────────────────────────────────────────────────────
From: bob@example.com
Date: Friday, October 18, 2025 at 10:00 AM PDT

Hey Alice, can you send me the project update?

────────────────────────────────────────────────────────────────────

Message: 2/3 ────────────────────────────────────────────────────────
From: alice@example.com
Date: Monday, October 20, 2025 at 9:15 AM PDT

Sure! Here's the latest status...

────────────────────────────────────────────────────────────────────

Message: 3/3 ────────────────────────────────────────────────────────
From: bob@example.com
Date: Monday, October 20, 2025 at 2:30 PM PDT

Thanks! This looks great.

────────────────────────────────────────────────────────────────────
```

## Technical Notes

### Message Parsing

The formatter intelligently parses messages separated by:
- Standard 80-dash separators (`"─" * 80`)
- Gmail-style message headers (From:, Date:, Subject:)
- Thread summary indicators

### Color Codes (ANSI)

- `\033[1;36m` - Bold cyan (odd messages)
- `\033[1;34m` - Bold blue (even messages)
- `\033[2;36m` - Dim cyan (footers)
- `\033[2;34m` - Dim blue (footers)
- `\033[0m` - Reset

### Fallback for Single Messages

Single-message threads display in simplified format:
```
INBOUND MESSAGE

From:     sender@example.com
Subject:  Quick question
Date:     Monday, October 20, 2025 at 3:45 PM PDT

[message content]
```

## Benefits

1. **Easy Orientation** - Always know where you are in a thread (Message: 3/7)
2. **Quick Navigation** - Visual color cues help distinguish messages
3. **Copy-Friendly** - Clean formatting that pastes perfectly without artifacts
4. **Consistent UX** - Same formatting in review UI and chat interface
5. **Accessible** - Works in any terminal with ANSI color support

## Usage

The enhancement is automatically applied to all email thread displays in Maia Mail:

```bash
# Review emails with enhanced thread display
maia mail review

# Chat interface also uses the new format
# (automatically shown when you open a draft)
```

## Configuration

No configuration needed! The formatter automatically:
- Detects multi-message threads
- Applies appropriate formatting
- Uses colors when supported by the terminal
- Falls back gracefully for single messages

## Related Documentation

- `docs/COPY_FRIENDLY_RICH.md` - Copy-friendly display principles
- `MAIA_MAIL_README.md` - Complete Maia Mail documentation
- `MAIA_MAIL_QUICKSTART.md` - Getting started guide

## Future Enhancements

Potential improvements:
- Configurable color schemes
- Optional message summary in thread header
- Jump-to-message navigation commands
- Thread visualization in compact mode
