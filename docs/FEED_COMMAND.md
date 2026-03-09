# Maia Feed - Unified Agent Activity Feed

The `maia feed` command provides a live, unified view of all agent activity across the system, formatted like a "group chat" for your agents.

## What It Shows

The feed aggregates and displays:
- **Daemon activity**: Calendar events triggering goals
- **Orchestrator planning**: Goal decomposition into tasks
- **Agent execution**: Task execution, tool calls, context loading
- **Conversations**: Slack/Discord message exchanges
- **Sync operations**: Database and Notion sync activity

## Usage

```bash
# Watch all activity in real-time
maia feed

# Filter by specific agent
maia feed --agent "chief-of-staff"

# Filter by goal ID
maia feed --goal abc123

# Filter by log level (show only warnings and errors)
maia feed --level warning

# Combine filters
maia feed --agent "grace" --level info
```

## Output Format

Events are displayed in a group-chat style with timestamps, emojis, and correlation IDs:

```
[12:55:03] 🗓️ Calendar Monitor
           Triggered goal: "Daily team check-in" (event: calendar_xyz)

[12:55:04] 🎯 Orchestrator [goal:abc123]
           Created 3 task(s)

[12:55:04] 🤖 Chief of Staff [goal:abc123] [task:def456]
           [exec:789xyz12] Starting agent 'chief-of-staff'

[12:55:05] 🔧 Chief of Staff [task:def456]
           Tool call: query_sql("recent updates")

[12:55:08] 💬 @alice → Maia
           "Hey Maia, quick update on the ML pipeline..."

[12:55:12] 💬 Maia → @alice
           "Thanks! Can you share the latest metrics?"
```

## Emoji Guide

- 🗓️ Calendar events and daemon activity
- 🎯 Orchestrator planning and goal management
- 🤖 Agent execution
- 💬 Conversations and messages
- 🔧 Tool calls
- ✅ Success
- ❌ Failure
- 🔄 In progress
- ⏳ Waiting

## Correlation IDs

Events include tags to correlate related activities:
- `[goal:abc123]` - Links to a specific goal
- `[task:def456]` - Links to a specific task
- `[conv:xyz789]` - Links to a conversation
- `[exec:123abc]` - Links to an agent execution

## Implementation

The feed aggregator watches multiple sources concurrently:

1. **Log File Watcher**: Tails `~/.promaia/calendar_monitor.log`
2. **Database Watcher**: Polls `~/.promaia/conversations.db` for new messages
3. **Logger Capture**: Captures live logs from Python loggers

All events are merged into a single chronological stream and displayed in real-time using Rich Live display.

## Stopping the Feed

Press `Ctrl+C` to stop watching the feed.

## Using with Agent Commands

You can automatically show the feed when running agents with the `--follow` (or `-f`) flag:

```bash
# Run next calendar event and show live feed
maia agent run-next --follow

# Short form
maia agent run-next -f
```

This will start the feed automatically and stop it when the agent completes. This is useful for watching exactly what your agent is doing during execution.

## Tips

- Run `maia feed` in a separate terminal while working to monitor agent activity
- Use filters to focus on specific agents or goals
- The feed shows the last 50 messages (older messages scroll off)
- Events are timestamped with wall-clock time (HH:MM:SS format)
