# Date Format Migration to ISO

## Problem

Gmail dates were being stored in email format (`Wed, 16 Jul 2025 11:20:32 +0000`) instead of ISO format (`2025-07-16T11:20:32+00:00`). This caused SQLite date comparisons to fail because:

```sql
-- String comparison (WRONG):
'Wed, 9 Jul 2025 20:09:54 -0700' >= '2025-10-06 04:52:35'
-- Returns TRUE because 'W' > '2' alphabetically, so ALL dates pass the filter!
```

**Impact**: NL queries like "from last 2 days" returned all emails instead of just recent ones (778 results instead of 14).

## Solution

### 1. Fixed Gmail Connector (`promaia/connectors/gmail_connector.py`)

**In `_prepare_message_for_storage()`** (line ~1347):
```python
# Parse date to ISO format for database consistency
from email.utils import parsedate_to_datetime
try:
    date_obj = parsedate_to_datetime(date_str)
    if date_obj.tzinfo is None:
        date_obj = date_obj.replace(tzinfo=timezone.utc)
    date_iso = date_obj.isoformat()  # ← Now using ISO format
except Exception:
    date_obj = datetime.now(timezone.utc)
    date_iso = date_obj.isoformat()
```

**Updated metadata** (line ~1392):
```python
metadata = {
    "created_time": date_iso,  # ← Changed from date_str
    "email_date": date_iso,    # ← Added in ISO format
    ...
}
```

### 2. Migrated Existing Database

**Script**: `migrate_dates_to_iso.py`

**Results**:
- Total Gmail entries: 5,095
- Migrated: 3,977 entries (78%)
- Already ISO: 1,098 entries
- Empty/NULL: 20 entries

**Before**:
```
created_time: Wed, 16 Jul 2025 11:20:32 +0000
email_date: Wed, 16 Jul 2025 11:20:32 +0000
```

**After**:
```
created_time: 2025-07-16T11:20:32+00:00
email_date: 2025-07-16T11:20:32+00:00
```

### 3. Verified Other Connectors

- **Discord**: ✅ Already using `message.created_at.isoformat()`
- **Notion**: ✅ Already using ISO format from Notion API

## Testing

**Query**: "trass gmail with term mgm from last 2 days"

**Before Migration**:
- Results: 778 emails (ALL emails with "mgm", no date filtering)
- Date range: Apr 2025 - Oct 2025

**After Migration**:
- Results: 14 emails (only from last 2 days)
- Date range: Oct 6-8, 2025 ✅

**SQL Generated** (correct):
```sql
WHERE u.created_time >= datetime('now', '-2 days')
-- Now compares ISO to ISO correctly!
```

## Impact on Agentic NL System

**Schema Samples Now Show ISO Dates**:
```json
{
  "created_time": "2025-07-16T11:20:32+00:00",
  "email_date": "2025-07-16T11:20:32+00:00"
}
```

The AI can now:
1. See actual ISO-formatted dates in sample data
2. Infer that `created_time` is a proper datetime field
3. Generate correct date comparisons with `datetime('now', '-X days')`

## Future Syncs

All **new** Gmail syncs will automatically use ISO format thanks to the connector fix. No further migration needed.

## Running the Migration Again

If you ever need to run it again (e.g., after importing old data):

```bash
# Dry run first
python3 migrate_dates_to_iso.py --dry-run

# Apply migration
python3 migrate_dates_to_iso.py
```

## Database Schema

The fix ensures consistency across all date fields:

| Field | Format | Example |
|-------|--------|---------|
| `created_time` | ISO 8601 | `2025-07-16T11:20:32+00:00` |
| `last_edited_time` | ISO 8601 | `2025-07-16T11:20:32+00:00` |
| `email_date` | ISO 8601 | `2025-07-16T11:20:32+00:00` |
| `synced_time` | ISO 8601 | `2025-10-08T04:52:35+00:00` |

This is the **universal standard** for all date storage in Promaia.

