# Enhanced Filtering for Maia Chat & Sync Commands

## Overview

The Maia chat and sync commands support advanced filtering to help you select specific content from your databases as context sources. This includes support for Notion databases, Discord channels, Gmail threads, and more. Enhanced filtering is particularly powerful for journaling, research, team communication analysis, and content workflows where you need precise control over which entries are included.

## Syntax

### Single Source Filtering
For a single source, you can use filters with or without source prefixes:

```bash
# Both of these work for single sources
maia chat -s cms -f '"Reference"=true'
maia chat -s cms -f 'cms:"Reference"=true'
```

### Multi-Source Filtering ⭐ NEW
For multiple sources, **all filters must specify a source prefix** to avoid ambiguity:

```bash
# ✅ Correct: Source-specific filters
maia chat -s cms -s journal -f 'cms:"Reference"=true' -f 'journal:created_time>2025-01-01'

# ❌ Error: Global filter in multi-source scenario
maia chat -s cms -s journal -f '"Reference"=true'
```

## Date Property Behavior ⭐ IMPORTANT

### Default Date Property
- **General use (`--days` flag)**: Uses `last_edited_time` for better relevance
- **Complex filters**: Uses the date property you explicitly specify

### Date Property Selection
```bash
# Uses last_edited_time (default for general filtering)
maia chat -s journal --days 7

# Uses created_time (explicitly specified in complex filter)
maia chat -s journal -f 'journal:created_time>2025-01-01'

# Uses last_edited_time (explicitly specified in complex filter)
maia chat -s journal -f 'journal:last_edited_time>2025-01-01'
```

**Why this matters for journaling**: 
- `created_time` = When you originally created the entry
- `last_edited_time` = When you last modified the entry (better for finding recently active thoughts)

## 🎮 Discord Integration & Browse Mode ⭐ NEW

### Interactive Browse Mode

The new browse functionality (`-b`) provides a visual TUI for selecting Discord channels:

```bash
# Interactive Discord channel selection
maia chat -b workspace.discord
maia sync -b workspace.discord

# Multiple Discord databases
maia chat -b workspace.discord workspace.yeeps_discord
maia sync -b workspace.discord workspace.yeeps_discord

# Combined with regular sources
maia chat -s journal:7 -b workspace.discord:30
maia sync -s journal:5 -b workspace.discord:14
```

### Discord Source Specifications

Discord sources support the same filtering syntax as other sources:

```bash
# Basic Discord source with days
maia chat -s workspace.discord:7
maia sync -s workspace.discord:30

# Discord with channel filtering
maia chat -s workspace.discord:14.channel_name=announcements
maia sync -s workspace.discord:7.channel_name=general

# Discord with author filtering
maia chat -s workspace.discord:7.author_name=admin
maia sync -s workspace.discord:14.author_name=moderator

# Discord with date filtering
maia chat -s workspace.discord:30 -f 'workspace.discord:created_time>2025-01-01'
maia sync -s workspace.discord:14 -f 'workspace.discord:timestamp>2025-01-15'
```

### Discord-Specific Properties

Discord messages support these filterable properties:

| Property | Type | Description | Example |
|----------|------|-------------|---------|
| `channel_name` | text | Discord channel name (sanitized) | `channel_name=announcements` |
| `channel_id` | text | Discord channel ID | `channel_id=1234567890` |
| `author_name` | text | Message author username | `author_name=admin` |
| `author_id` | text | Message author user ID | `author_id=123456789` |
| `content` | text | Message content | `content~announcement` |
| `timestamp` | date | Message timestamp | `timestamp>2025-01-01` |
| `server_name` | text | Discord server name | `server_name=My Team` |
| `has_attachments` | boolean | Has file attachments | `has_attachments=true` |
| `reaction_count` | number | Number of reactions | `reaction_count>5` |

### Channel Name Handling

Discord channels with emojis and special characters are automatically handled:

```bash
# Local filesystem uses sanitized names
data/md/discord/workspace/server/announcements/
data/md/discord/workspace/server/release-notes/
data/md/discord/workspace/server/plush-announcements/

# But actual Discord channels have emojis
📢・announcements
🗞️・release-notes  
📣・plush-announcements

# System automatically maps between them:
maia chat -s workspace.discord:7.channel_name=announcements
# ↑ Finds Discord channel: 📢・announcements
```

### Browse Mode Features

The interactive browser provides:

- **Visual Channel Selection**: Navigate with arrow keys, toggle with spacebar
- **Per-Channel Day Cycling**: Press `D` to cycle days (1→7→14→30→60→90) for individual channels
- **Real-time Filtering**: Type to filter channels by name
- **Multi-Server Support**: Browse channels across multiple Discord servers
- **Workspace Inference**: Automatically detects workspace from database names

```
🎮 Discord Channel Browser

🔍 Filter: (type to search)

📂 My Team Server (workspace.discord)
    ☑ #📢・announcements (30 days)
    ☐ #💬・general (7 days)
>>> ☑ #🗞️・release-notes (14 days)

📂 Community Server (workspace.community_discord)
    ☐ #🎮・gaming (30 days)
    ☑ #📝・feedback (60 days)

📊 Selected: 3 channels

↑↓ Navigate  SPACE Toggle  D Cycle Days  ENTER Confirm  ESC Cancel
```

## Filter Syntax Examples

### Basic Property Filters
```bash
# Boolean properties
maia chat -s cms -f 'cms:"Reference"=true'
maia chat -s cms -f 'cms:"Published"=false'

# Text properties
maia chat -s cms -f 'cms:"Status"=live'
maia chat -s cms -f 'cms:"Category"=blog'

# Properties with spaces (always use quotes)
maia chat -s cms -f 'cms:"Blog status"=live'
maia chat -s cms -f 'cms:"Content type"=article'
```

### Date Filters
```bash
# Recent content
maia chat -s journal -f 'journal:created_time>2025-01-01'
maia chat -s cms -f 'cms:last_edited_time<2024-12-31'

# Specific date ranges
maia chat -s cms -f 'cms:created_time>2025-01-01' -f 'cms:created_time<2025-02-01'
```

### Complex Expressions
You can use `and`/`or` operators within a single source:

```bash
# AND conditions
maia chat -s cms -f 'cms:"Reference"=true and "Blog status"=live'

# OR conditions  
maia chat -s cms -f 'cms:"Status"=draft or "Status"=review'

# Mixed conditions
maia chat -s cms -f 'cms:"Reference"=true and ("Status"=live or "Status"=published)'
```

## Advanced Date Filtering for Journaling ⭐ NEW

### Multiple Date Ranges (OR Logic)
Perfect for analyzing patterns across time periods:

```bash
# First week of specific months
maia chat -s journal -f 'journal:created_time>2024-12-01 and created_time<2024-12-08 or created_time>2025-01-01 and created_time<2025-01-08'

# Last week of quarters
maia chat -s journal -f 'journal:created_time>2024-12-24 and created_time<2024-12-31 or created_time>2025-03-24 and created_time<2025-03-31'

# Specific weekdays across months (great for tracking patterns)
maia chat -s journal -f 'journal:created_time>2025-01-06 and created_time<2025-01-07 or created_time>2025-02-03 and created_time<2025-02-04'
```

### Complex Journaling Patterns
```bash
# First week of every month from Dec 2024 to July 2025
maia chat -s journal -f 'journal:created_time>2024-12-01 and created_time<2024-12-08 or created_time>2025-01-01 and created_time<2025-01-08 or created_time>2025-02-01 and created_time<2025-02-08 or created_time>2025-03-01 and created_time<2025-03-08 or created_time>2025-04-01 and created_time<2025-04-08 or created_time>2025-05-01 and created_time<2025-05-08 or created_time>2025-06-01 and created_time<2025-06-08 or created_time>2025-07-01 and created_time<2025-07-08'

# Beginning and end of a period (great for "then vs now" analysis)
maia chat -s journal -f 'journal:created_time>2024-12-01 and created_time<2024-12-08 or created_time>2025-07-01 and created_time<2025-07-08'

# Monthly check-ins (1st-3rd of each month)
maia chat -s journal -f 'journal:created_time>2025-01-01 and created_time<2025-01-04 or created_time>2025-02-01 and created_time<2025-02-04 or created_time>2025-03-01 and created_time<2025-03-04'
```

### Seasonal and Periodic Analysis
```bash
# Same season across years
maia chat -s journal -f 'journal:created_time>2024-12-21 and created_time<2025-03-20 or created_time>2023-12-21 and created_time<2024-03-20'

# Monthly retrospectives (last 3 days of each month)
maia chat -s journal -f 'journal:created_time>2024-12-29 and created_time<2025-01-01 or created_time>2025-01-29 and created_time<2025-02-01'

# Weekend entries only
maia chat -s journal -f 'journal:created_time>2025-01-04 and created_time<2025-01-06 or created_time>2025-01-11 and created_time<2025-01-13'
```

## Journaling-Specific Examples ⭐ NEW

### Reflection and Pattern Analysis
```bash
# Monthly beginnings (track goal setting)
maia chat -s journal -f 'journal:created_time>2025-01-01 and created_time<2025-01-08 or created_time>2025-02-01 and created_time<2025-02-08 or created_time>2025-03-01 and created_time<2025-03-08'

# Monthly endings (track reflection)
maia chat -s journal -f 'journal:created_time>2025-01-24 and created_time<2025-01-31 or created_time>2025-02-21 and created_time<2025-02-28 or created_time>2025-03-24 and created_time<2025-03-31'

# Crisis or breakthrough periods
maia chat -s journal -f 'journal:created_time>2025-01-15 and created_time<2025-01-22 or created_time>2025-03-10 and created_time<2025-03-17'
```

### Habit and Routine Tracking
```bash
# Morning pages (assuming you journal in the morning)
maia chat -s journal -f 'journal:created_time>2025-01-01 and created_time<2025-01-02 or created_time>2025-01-08 and created_time<2025-01-09'

# Weekly planning entries (Sundays)
maia chat -s journal -f 'journal:created_time>2025-01-05 and created_time<2025-01-06 or created_time>2025-01-12 and created_time<2025-01-13'

# Monthly reviews (combining created_time and last_edited_time)
maia chat -s journal -f 'journal:created_time>2025-01-31 and created_time<2025-02-01' -f 'journal:last_edited_time>2025-01-28'
```

### Multi-Source Complex Examples
```bash
# Different filters for different sources
maia chat -s cms -s journal -f 'cms:"Reference"=true and "Blog status"=live' -f 'journal:created_time>2025-01-01'

# Multiple filters per source (AND-ed together)
maia chat -s cms -s journal \
  -f 'cms:"Reference"=true' \
  -f 'cms:"Blog status"=live' \
  -f 'journal:created_time>2025-06-01'

# Combining content creation with journal context
maia chat -s cms -s journal -f 'cms:"Status"=draft' -f 'journal:created_time>2025-01-01 and created_time<2025-01-08'
```

## Property Names

### Built-in Properties
- `created_time` - When the page was created (better for chronological analysis)
- `last_edited_time` - When the page was last modified (better for finding active thoughts)
- `title` - Page title
- `page_id` - Unique page identifier

### Custom Properties
Any custom property from your Notion database can be used. Properties with spaces must be quoted:

```bash
# Custom checkbox property
maia chat -s cms -f 'cms:"Reference"=true'

# Custom select property
maia chat -s cms -f 'cms:"Blog status"=live'

# Custom multi-select property
maia chat -s cms -f 'cms:"Tags"=research'
```

## Filter Logic

### Single Filters
Each `-f` flag creates one filter condition.

### Multiple Filters  
Multiple `-f` flags are combined with AND logic:
```bash
# This means: Reference=true AND Status=live
maia chat -s cms -f 'cms:"Reference"=true' -f 'cms:"Status"=live'
```

### Within Filter Expressions
Within a single filter expression, you can use `and`/`or`:
```bash
# This means: Reference=true AND (Status=live OR Status=published)  
maia chat -s cms -f 'cms:"Reference"=true and ("Status"=live or "Status"=published)'
```

### Complex Date Logic
```bash
# OR logic for multiple date ranges
maia chat -s journal -f 'journal:created_time>2025-01-01 and created_time<2025-01-08 or created_time>2025-02-01 and created_time<2025-02-08'

# AND logic across multiple filter flags
maia chat -s journal -f 'journal:created_time>2025-01-01' -f 'journal:created_time<2025-01-31'
```

## Error Messages

### Multi-Source Global Filter Error
```
Error: In multi-source scenarios, all filters must specify a source prefix.
Example: Instead of '"Reference"=true', use 'source:"Reference"=true'
Available sources: cms, journal
```

### Invalid Source Error
```
Error: Filter source 'invalid' not found in specified sources.
Available sources: cms, journal
```

### Date Format Error
```
Error: Invalid date format. Use YYYY-MM-DD format.
Example: 'created_time>2025-01-01' not 'created_time>Jan 1, 2025'
```

## Migration from Global Filters

If you're upgrading from the old global filter system:

### Before (Single Source - Still Works)
```bash
maia chat -s cms -f '"Reference"=true'
```

### After (Multi-Source - Required)
```bash
maia chat -s cms -s journal -f 'cms:"Reference"=true'  
```

## Best Practices

1. **Use source prefixes consistently** even for single sources to future-proof your commands
2. **Quote property names with spaces** to avoid parsing errors
3. **Test complex expressions** with debug mode: `MAIA_DEBUG=1 maia chat ...`
4. **Start simple** and build up complex filters incrementally
5. **Use `created_time` for chronological analysis** and `last_edited_time` for finding recently active content
6. **Break very long OR expressions** into multiple commands if they become unwieldy
7. **Use consistent date formats** (YYYY-MM-DD) to avoid parsing errors

## Debug Mode

Enable debug mode to see how filters are parsed and applied:

```bash
MAIA_DEBUG=1 maia chat -s cms -s journal -f 'cms:"Reference"=true'
```

This will show:
- How filters are parsed
- Which filters apply to which sources  
- Filter summary before content loading
- Detailed processing information
- SQL queries being executed
- Date property resolution

## Common Use Cases

### Journal Analysis Workflows
```bash
# Monthly pattern analysis
maia chat -s journal -f 'journal:created_time>2025-01-01 and created_time<2025-01-08 or created_time>2025-02-01 and created_time<2025-02-08'

# Seasonal mood tracking
maia chat -s journal -f 'journal:created_time>2024-12-21 and created_time<2025-03-20'

# Project retrospective
maia chat -s journal -f 'journal:created_time>2025-01-01 and created_time<2025-01-31' -f 'journal:last_edited_time>2025-01-15'
```

### Content Creation Workflows
```bash
# Research phase
maia chat -s cms -s journal -f 'cms:"Reference"=true' -f 'journal:created_time>2025-01-01'

# Writing phase
maia chat -s cms -s journal -f 'cms:"Status"=draft' -f 'journal:last_edited_time>2025-01-01'
``` 