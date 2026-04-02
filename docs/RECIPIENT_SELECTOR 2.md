# Email Recipient Selector

Interactive interface for selecting email recipients when sending drafts from Maia Mail.

## Overview

When sending an email draft with `/send`, users can now choose who receives the email through an interactive selector with three modes:

1. **Reply to Sender** - Send only to the original sender
2. **Reply All** (default) - Send to all recipients in the thread
3. **Custom/Forward** - Manually select specific recipients

## Usage

### From Draft Chat

```bash
# After reviewing a draft in maia mail
You: /send 1

# Interactive recipient selector appears
📧 Select Recipients

▶ Reply to Sender  •  Reply All  •  Custom/Forward

⬅️ ➡️  Switch Mode  •  ↑↓ Navigate  •  SPACE Toggle  •  A Add  •  ENTER Confirm  •  ESC Cancel

(Replying to all recipients)
  → john@example.com
  → alice@example.com
  → bob@example.com
  
# Confirm selection
✅ Sending to: john@example.com, alice@example.com, bob@example.com

# Safety confirmation
⚠️  Ready to send Draft #1
Subject: RE: Q4 Timeline Discussion

Type the first 5 characters of the subject to confirm: 'RE: Q'
Or type 'cancel' (or press Enter) to abort

Confirm: RE: Q

📤 Sending email...
✅ Email sent!
```

## Modes

### Reply to Sender

Sends email only to the person who sent the original message.

```
▶ Reply to Sender  •  Reply All  •  Custom/Forward

(Replying to sender only)
  → john@example.com
```

**Use cases:**
- Private response to a group email
- Direct communication with sender
- Avoid spamming CC'd parties

### Reply All (Default)

Sends to all recipients found in the thread (FROM, TO, CC).

```
Reply to Sender  •  ▶ Reply All  •  Custom/Forward

(Replying to all recipients)
  → john@example.com
  → alice@example.com
  → bob@example.com
  → carol@example.com
  ... and 3 more
```

**Use cases:**
- Standard reply behavior
- Keep everyone in the loop
- Group discussions

### Custom/Forward

Manually select which recipients should receive the email.

```
Reply to Sender  •  Reply All  •  ▶ Custom/Forward

⬅️ ➡️  Switch Mode  •  ↑↓ Navigate  •  SPACE Toggle  •  A Add  •  ENTER Confirm  •  ESC Cancel

✉️  Recipients:
▶     ☑       john@example.com
      ☐       alice@example.com
      ☑       bob@example.com
      ☐       carol@example.com
      ☑       dave@example.com
```

**Adding new recipients:**
Press `A` to add a new recipient. A blank checked entry appears with a cursor:
```
✉️  Recipients:
      ☑       john@example.com
▶     ☑       █
      ☑       bob@example.com
```

Type the email address, then press Enter to save or Esc to cancel.

**Use cases:**
- Forward to specific people
- Selectively include/exclude recipients
- Add new recipients to conversation
- Remove people from thread

## Controls

| Key | Action |
|-----|--------|
| `⬅️` / `➡️` | Switch between modes |
| `↑` / `↓` | Navigate recipients (Custom mode only) |
| `Space` | Toggle recipient selection (Custom mode only) |
| `A` | Add new recipient (Custom mode only) |
| `a-z, 0-9, @, ., -, _` | Type email when editing |
| `Backspace` | Delete character when editing |
| `Enter` | Save email when editing, or confirm selection |
| `Esc` | Cancel editing, or cancel entire selector |

## How It Works

### Recipient Extraction

The selector automatically extracts all email addresses from:

1. **FROM field** - The original sender
2. **TO field** - Primary recipients
3. **Thread context** - Any email addresses mentioned in the thread

Email addresses are deduplicated and presented in a clean list.

### Format Handling

Handles various email formats:
- `john@example.com` - Plain email
- `John Doe <john@example.com>` - Name with email
- `john@example.com, alice@example.com` - Multiple recipients

### Default Behavior

- **Mode**: Starts in "Reply All" mode
- **Selection**: All recipients selected by default in Custom mode
- **Confirmation**: Must press Enter to proceed (safety measure)

## Implementation

### Files

- **`promaia/mail/recipient_selector.py`** - Core selector logic and UI
- **`promaia/mail/draft_chat.py`** - Integration with draft chat
- **`promaia/mail/gmail_sender.py`** - Updated to accept custom recipients

### Key Classes

**`RecipientSelector`**:
- Extracts recipients from email headers and thread
- Renders interactive UI with three modes
- Handles keyboard navigation and selection
- Returns confirmed recipient list

**`RecipientMode`**:
- `REPLY_SENDER` - Send to sender only
- `REPLY_ALL` - Send to all (default)
- `CUSTOM` - Manual selection

### Integration Flow

```
User types /send → Recipient Selector → User confirms → Display Recipients → Safety Check → Gmail Send
                         ↓                                      ↓
                  Choose recipients                    Type first 5 chars of subject
                         ↓                                      ↓
                   Enter confirms                         Confirmation
                         ↓                                      ↓
                  Pass to GmailSender ←─────────────────────────┘
```

## Examples

### Example 1: Private Reply

```bash
# Original email from john@example.com to you + team@example.com
You: /send

# Switch to "Reply to Sender" mode with ⬅️
# Press Enter

✅ Sending to: john@example.com
Type the first 5 characters to confirm: RE: Pr
```

### Example 2: Selective Forward

```bash
# Email thread with 5 people
You: /send

# Switch to "Custom/Forward" mode with ➡️➡️
# Deselect unwanted recipients with Space
# Press A to add new recipient
# Type: sarah@example.com
# Press Enter to save
# Deselect others with Space
# Press Enter to confirm

✅ Sending to: alice@example.com, bob@example.com, sarah@example.com

⚠️  Ready to send Draft #1
Subject: RE: Project Update
Type the first 5 characters: RE: Pr
```

### Example 3: Reply All (Default)

```bash
# Group email
You: /send

# Already in "Reply All" mode
# Just press Enter

✅ Sending to: john@example.com, alice@example.com, team@example.com
```

## Safety Features

1. **Visual confirmation** - Shows exactly who will receive the email
2. **Subject confirmation** - Still requires typing first 5 characters
3. **Cancel anytime** - Esc key cancels at any point
4. **Clear feedback** - Displays selected recipients before final confirmation

## Future Enhancements

Potential improvements:

- [ ] CC and BCC support
- [ ] Remember user's preferred mode per contact
- [ ] Quick toggles (e.g., `/send --sender-only`)
- [ ] Address book integration
- [ ] Recipient groups/aliases
- [ ] Search/filter recipients in Custom mode

## Troubleshooting

### No recipients detected

If the selector shows no recipients:
- Check that the original email has valid FROM/TO fields
- Try Custom mode and verify thread context extraction
- The email may have malformed headers

### Can't select certain recipients

In Custom mode, use ↑↓ to navigate to the recipient, then Space to toggle selection.

### Accidentally sent to wrong people

The safety confirmation (typing first 5 characters of subject) is your last chance to verify. Always check the "✅ Sending to:" line before confirming.

## Architecture Notes

### Why Interactive UI?

The interactive selector provides:
- **Clarity**: See exactly who gets the email
- **Flexibility**: Change recipients per-email
- **Safety**: Visual confirmation reduces mistakes
- **Speed**: Keyboard navigation is fast

### Why Three Modes?

Common email patterns:
1. **Reply to Sender**: ~30% of replies (private responses)
2. **Reply All**: ~60% of replies (default behavior)  
3. **Custom**: ~10% of replies (forwards, selective replies)

Three modes cover all use cases without overwhelming the user.

### Design Inspiration

Based on the workspace browser UI (`promaia/cli/workspace_browser.py`):
- Similar keyboard navigation (arrows, space, enter)
- Checkbox pattern for multi-select
- Mode switching with arrow keys
- Clean, copy-friendly output

## Related Features

- [Maia Mail](MAIA_MAIL_README.md) - Main email system
- [Draft Chat](MAIA_MAIL_README.md#draft-chat) - Interactive draft refinement
- [Gmail Integration](GMAIL_INTEGRATION.md) - Gmail API integration

---

**Implementation Date**: October 20, 2025  
**Feature**: Interactive recipient selector for Maia Mail  
**Status**: Complete ✅

