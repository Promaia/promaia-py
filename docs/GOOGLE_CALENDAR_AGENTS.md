# Managing Agents Like Team Members on Google Calendar

## Overview

Promaia agents can be managed like team members on your Google Calendar. Instead of configuring complex schedules, just add agents to your calendar and they'll run when their events trigger - just like scheduling meetings with your team!

## Setup

### 1. Install Google Calendar API Libraries

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

### 2. Create Google Cloud Project & Enable Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the Google Calendar API:
   - Go to "APIs & Services" > "Library"
   - Search for "Google Calendar API"
   - Click "Enable"

### 3. Authenticate with Google

```bash
maia auth configure google
```

This authorizes both Gmail and Calendar via the Promaia OAuth proxy.
You can also bring your own Google Cloud credentials if preferred.

## Creating Agents on Your Calendar

### During Agent Creation

When you create a new agent with `maia agent add`, you'll be asked:

```
Add this agent to Google Calendar? (Y/n):
```

Press **Y** (or just ENTER) to add it to your calendar immediately!

### For Existing Agents

Sync an existing agent to your calendar:

```bash
maia agent calendar-sync daily-summary
```

## What It Looks Like

After adding an agent to your calendar, you'll see:

```
🤖 daily-summary
  Friday, January 23  •  9:00 AM  •  Weekly (Every Monday, Wednesday, Friday)

  Promaia Agent: daily-summary
  Workspace: koii
  Databases: journal, gmail, discord
  Output: abc123...

  This event is managed by Promaia.
  The agent will run automatically when this event occurs.
```

The events appear in **blue** (color ID 9) and have the 🤖 emoji prefix.

## Managing Agents on Your Calendar

### View in Google Calendar

- Open https://calendar.google.com
- Your agents appear as recurring events
- They're labeled with 🤖 and the agent name

### Edit Schedule

**In Google Calendar:**
- Drag & drop to reschedule
- Edit recurrence pattern
- Skip individual occurrences
- All changes sync automatically!

**Via CLI:**
```bash
# Re-sync after editing agent config
maia agent calendar-sync agent-name

# Remove from calendar
maia agent calendar-remove agent-name

# List all agents on calendar
maia agent calendar-list
```

## How Agent Execution Works

### Calendar-Triggered Execution

Agents run when their calendar event triggers:

1. **Calendar Monitor**: Promaia watches for upcoming agent events
2. **Event Trigger**: When an event starts, the agent executes
3. **Result Logging**: Output is written to configured Notion page
4. **Stats Tracking**: Execution stats are recorded

### Manual Execution

You can still run agents manually:

```bash
maia agent run-scheduled agent-name
```

This doesn't affect calendar scheduling.

## CLI Commands

### Sync Agent to Calendar

```bash
maia agent calendar-sync <agent-name>
```

Creates recurring calendar events based on agent schedule.

### Remove Agent from Calendar

```bash
maia agent calendar-remove <agent-name>
```

Deletes all calendar events for this agent.

### List Calendar Agents

```bash
maia agent calendar-list
```

Shows all agents currently on your Google Calendar.

## Use Cases

### 1. Daily Standup Agent

Schedule an agent to run every weekday morning at 9 AM:

```
Mon-Fri: 09:00
  🤖 daily-standup
  Creates your daily summary from journal, emails, and messages
```

### 2. Weekly Report Agent

Schedule an agent for Friday afternoon:

```
Friday: 16:00
  🤖 weekly-report
  Compiles your week's achievements and sends to team
```

### 3. Ad-Hoc Research Agent

Create one-time calendar events for specific research tasks:

```
Tuesday, Jan 28: 14:00
  🤖 competitor-analysis
  Research and summarize competitor announcements from this week
```

## Benefits

### ✓ Visual Management
See all your agents alongside meetings and other calendar items

### ✓ Familiar Interface
Use Google Calendar's drag-and-drop, recurrence patterns, etc.

### ✓ Team Coordination
Share your agent calendar with team members

### ✓ Mobile Access
Manage agents from Google Calendar mobile app

### ✓ Calendar Integrations
Works with all tools that integrate with Google Calendar

### ✓ Smart Scheduling
Easily avoid conflicts with meetings and other events

## Advanced Features

### Multiple Calendars

Organize agents across different calendars:

```python
from promaia.calendar import get_calendar_manager

cal_mgr = get_calendar_manager()
cal_mgr.calendar_id = "agents@example.com"  # Use specific calendar
```

### Event Colors

Agents use color ID 9 (blue) by default. Customize per agent:

```python
event['colorId'] = '11'  # Red for urgent agents
```

### Reminders

Set up calendar reminders for agent runs:

```python
'reminders': {
    'useDefault': False,
    'overrides': [
        {'method': 'popup', 'minutes': 5},
    ],
}
```

## Troubleshooting

### "Google not configured"

Run the unified auth setup:

```bash
maia auth configure google
```

### "Authentication failed"

Re-authenticate by running `maia auth configure google` again.

### "Agent not appearing on calendar"

Check:
- Agent has a schedule (not just interval)
- Calendar ID is correct (defaults to "primary")
- You're viewing the correct Google account

### "Cannot sync interval-based agents"

Old interval-based agents need to be converted to schedule format:

```bash
# Edit the agent
maia agent info-scheduled agent-name

# Note the current interval
# Remove and recreate with schedule instead
maia agent remove agent-name
maia agent add  # Follow new schedule-based flow
```

## Privacy & Security

### What Promaia Stores

- **Event IDs**: Stored in agent config for syncing
- **Agent Metadata**: Stored in calendar event's private properties
- **OAuth Token**: Stored locally in `maia-data/credentials/google/token.json`

### What's Visible

- **On Calendar**: Event title (🤖 agent-name) and description
- **To Others**: If calendar is shared, they see agent events
- **To Google**: Standard Google Calendar API access

### Revoking Access

1. Go to [Google Account Permissions](https://myaccount.google.com/permissions)
2. Find "Promaia" and click "Remove Access"
3. Clear local token: `maia auth clear google`

## Examples

### Morning Routine Agent

```bash
maia agent add
# Name: morning-routine
# Schedule: Mon-Fri @ 7:00 AM
# Databases: journal:1, gmail:1
# Prompt: "Summarize yesterday's work and today's priorities"
# Add to calendar? Y
```

Your calendar now shows:
```
7:00 AM  🤖 morning-routine  (Every weekday)
```

### Custom Schedule Agent

```bash
maia agent add
# Name: market-analysis
# Schedule: Mon @ 9:00, Wed @ 9:00, Fri @ 9:00
# Databases: news:7, research:30
# Prompt: "Analyze market trends and competitor activity"
# Add to calendar? Y
```

Your calendar now shows:
```
Mon 9:00 AM  🤖 market-analysis  (Weekly on Monday)
Wed 9:00 AM  🤖 market-analysis  (Weekly on Wednesday)
Fri 9:00 AM  🤖 market-analysis  (Weekly on Friday)
```

## Best Practices

1. **Use Descriptive Names**: `email-drafts` not `agent-1`
2. **Group by Purpose**: Use calendar colors or separate calendars
3. **Set Realistic Schedules**: Don't overload your calendar
4. **Review Weekly**: Check agent events and adjust as needed
5. **Share Wisely**: Only share agent calendars with trusted team members
6. **Test First**: Run manually before adding to calendar

## Future Enhancements

Coming soon:
- Calendar webhook integration (instant triggering)
- Event result logging in calendar notes
- Multi-workspace calendar support
- Calendar conflict detection
- Agent execution history in calendar

## Comparison: Schedule Grid vs. Calendar

| Feature | Schedule Grid | Google Calendar |
|---------|---------------|-----------------|
| Visual Management | ✓ ASCII table | ✓✓ Rich UI |
| Drag & Drop | ✗ | ✓✓ |
| Mobile Access | ✗ | ✓✓ |
| One-Time Events | ✗ | ✓✓ |
| Complex Recurrence | ✗ | ✓✓ |
| Copy-Pastable | ✓✓ | ✗ |
| CLI-Only | ✓✓ | ✗ |
| Team Sharing | ✗ | ✓✓ |

**Recommendation**: Use Google Calendar for most agents. Use schedule grid only when you prefer pure CLI workflow.
