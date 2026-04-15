# Maia Mail History Feature

## Overview

The History feature separates completed emails (sent/archived) from your active queue, providing a clean review interface while maintaining a searchable archive of all past actions.

## Key Benefits

✅ **Clean Queue** - Completed messages don't clutter your active work  
✅ **Persistent Archive** - All sent/archived messages saved with completion timestamps  
✅ **Easy Toggle** - Press 'h' to switch between queue and history  
✅ **Same Interface** - History uses the same familiar UI as the queue  

## Usage

### From Command Line

Start directly in history view:
```bash
maia mail --workspace trass --history
```

Or use the shorthand:
```bash
maia mail -ws trass --history
```

### From Review Queue

While in the review queue, press `h` to toggle to history view:

**Queue View** → Press `h` → **History View**  
**History View** → Press `h` → **Queue View**

## How It Works

### Message States

**Active Queue** (default view):
- `pending` - Awaiting review and response
- `skipped` - No response needed (AI classified)

**History** (accessed via `h` key or `--history` flag):
- `sent` - Email responses sent
- `archived` - Manually archived (cleared without sending)

### Completion Tracking

When you mark a message as sent or archived:
1. Status changes to `sent` or `archived`
2. `completed_time` is set to current timestamp
3. Message is removed from queue
4. Message appears in history, ordered by completion time

### Data Persistence

- Messages are **never deleted** from the database
- All completed messages remain searchable in history
- Queue automatically excludes completed messages
- History shows only completed messages

## Interface

### Queue View (Default)

```
Maia Mail - Draft Review Queue

Progress: [████████████░░░░░░░░] 12/15 resolved (80%)
Status: ✅ 8 sent  •  🗄️ 4 archived  •  ⏳ 2 pending  •  ⏭️ 1 skipped

Navigation: ↑/↓ | Enter to open chat | a archive | h history | q quit
```

### History View

```
Maia Mail - History (Completed Messages)

Total Completed: 12  •  ✅ 8 sent  •  🗄️ 4 archived

Navigation: ↑/↓ | Enter to view | h back to queue | q quit
```

## Key Differences

| Feature | Queue View | History View |
|---------|------------|--------------|
| **Messages** | Pending & Skipped | Sent & Archived |
| **Actions** | Chat, Archive | View only |
| **Sorting** | Creation time | Completion time |
| **Purpose** | Active work | Completed review |
| **Toggle** | Press `h` for history | Press `h` for queue |

## Implementation Details

### Database Schema

New field added to `email_drafts` table:

```sql
completed_time TEXT  -- ISO timestamp when marked sent/archived
```

### Methods Added

**`DraftManager.get_history_for_workspace(workspace: str)`**
- Returns sent/archived messages
- Ordered by `completed_time DESC`
- Used for history view

**`DraftManager.get_drafts_for_workspace(workspace: str, include_resolved: bool)`**
- Updated to exclude sent/archived by default (`include_resolved=False`)
- Used for queue view

**`EmailReviewUI.launch_review(workspaces: List[str], start_in_history: bool)`**
- Added `start_in_history` parameter
- Supports `--history` CLI flag

### Status Tracking

When status changes to `sent` or `archived`:
```python
# Sets both reviewed_time and completed_time
UPDATE email_drafts 
SET status = ?, 
    reviewed_time = ?, 
    completed_time = ? 
WHERE draft_id = ?
```

## Common Workflows

### Review and Archive Workflow

1. Start in queue: `maia mail -ws trass`
2. Review pending emails
3. Press `a` to archive (clear from queue)
4. Message moves to history
5. Press `h` to view history
6. See archived message with completion timestamp

### Send and Track Workflow

1. Start in queue: `maia mail -ws trass`
2. Open draft with `Enter`
3. Refine with chat
4. Send with `/send`
5. Message marked sent and moves to history
6. Press `h` to see all sent messages

### History Review Workflow

1. Start directly in history: `maia mail -ws trass --history`
2. Review past sent/archived messages
3. See completion timestamps
4. Press `Enter` to view full message details
5. Press `h` to return to queue

## Migration

Existing databases are automatically migrated:

1. `completed_time` column added to `email_drafts` table
2. Existing sent messages: `completed_time = sent_time`
3. Existing archived messages: `completed_time = reviewed_time`
4. All future completions tracked automatically

## Examples

### Example 1: Clear Your Queue

```bash
# Start in queue
maia mail -ws trass

# Review emails
# Press 'a' on items you want to clear
# Press 'h' to see what you archived
# Press 'h' again to return to queue
```

### Example 2: Check Yesterday's Sent Emails

```bash
# Go directly to history
maia mail -ws trass --history

# See all sent emails ordered by completion time
# Most recent sends appear first
# Press 'Enter' to view full details
```

### Example 3: Toggle While Working

```bash
# Start in queue
maia mail -ws trass

# Work through some emails...
# Press 'h' to see your completed items
# Verify sends/archives
# Press 'h' to return to pending work
```

## Tips

💡 **Use history to verify sends** - Quickly check what you sent today  
💡 **Archive liberally** - Clear items you don't need to respond to  
💡 **Toggle frequently** - Press `h` to see progress as you work  
💡 **Start in history** - Use `--history` flag to review past actions  

## Keyboard Shortcuts

| Key | Queue View | History View |
|-----|------------|--------------|
| `↑`/`↓` | Navigate | Navigate |
| `Enter` | Open chat | View details |
| `a` | Archive item | *(disabled)* |
| `h` | Switch to history | Switch to queue |
| `q` | Quit | Quit |

## Related Documentation

- `MAIA_MAIL_README.md` - Complete Maia Mail documentation
- `MAIA_MAIL_QUICKSTART.md` - Getting started guide
- `MAIA_MAIL_THREAD_SCROLLING.md` - Thread formatting features




