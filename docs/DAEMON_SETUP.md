# Calendar Monitor Daemon Setup

This guide explains how to run the Promaia calendar monitor as a 24/7 background daemon on macOS and Linux.

## Overview

The calendar monitor daemon watches your agent calendars and automatically triggers agents when events start. It's designed for production use with:

- **Auto-start on boot** - Daemon starts automatically when the system boots
- **Auto-restart on crash** - Daemon restarts automatically if it crashes
- **Persistent logging** - Logs are written to `~/.promaia/calendar_monitor.log`
- **Process management** - PID file tracking and signal handling

## Quick Start (macOS)

### Option 1: Use CLI Commands (Recommended)

```bash
# Enable daemon (installs and loads launchd plist)
maia daemon enable

# Check status
maia daemon status

# View logs
maia daemon logs

# Follow logs in real-time
maia daemon logs --follow
```

### Option 2: Manual Installation

```bash
# Install launchd plist (customizes for your system)
./scripts/install_launchd.sh

# Check status
maia daemon status
```

## Daemon Commands

### Status

Check if the daemon is running:

```bash
maia daemon status
```

Output:
```
┌────────────┬─────────────────────────────────────────────────┐
│ Status     │ Running ✓                                       │
│ PID        │ 12345                                           │
│ PID File   │ /Users/you/.promaia/calendar_monitor.pid       │
│ Log File   │ /Users/you/.promaia/calendar_monitor.log       │
│ Auto-start │ Enabled                                         │
└────────────┴─────────────────────────────────────────────────┘
```

### Enable Auto-Start

Enable the daemon to start automatically on boot:

```bash
maia daemon enable
```

This:
1. Installs the launchd plist to `~/Library/LaunchAgents/`
2. Customizes it for your Python environment
3. Loads the service
4. Daemon will start on next boot/login

### Disable Auto-Start

Disable auto-start and unload the daemon:

```bash
maia daemon disable
```

This:
1. Unloads the service
2. Removes the launchd plist
3. Daemon will not start on boot

### Start Manually

Start the daemon in the foreground (for testing):

```bash
maia daemon start
```

Options:
- `--interval 1` - Check interval in minutes (default: 1)
- `--window 5` - Trigger window in minutes after start time (default: 5)

**Note:** This runs in the foreground. For background operation, use `maia daemon enable` instead.

### Stop

Stop the running daemon:

```bash
maia daemon stop
```

Sends SIGTERM for graceful shutdown, falls back to SIGKILL if needed.

### Restart

Restart the daemon:

```bash
maia daemon restart
```

Equivalent to `stop` followed by `start`.

### View Logs

Show recent logs:

```bash
maia daemon logs
```

Show more lines:

```bash
maia daemon logs --lines 100
```

Follow logs in real-time:

```bash
maia daemon logs --follow
```

Press Ctrl+C to stop following.

## Configuration

### Calendar Check Interval

The daemon checks calendars every 1 minute by default. To change this:

**macOS (launchd):**
1. Edit `~/Library/LaunchAgents/com.promaia.agent.plist`
2. Change `--interval 1` to desired value (in minutes)
3. Reload: `launchctl unload ~/Library/LaunchAgents/com.promaia.agent.plist && launchctl load ~/Library/LaunchAgents/com.promaia.agent.plist`

### Trigger Window

Events are triggered when they start. The daemon will trigger an event if:
- The event time has arrived (started up to 5 minutes ago), OR
- The event is starting in the next 60 seconds

Default window: 5 minutes after start time (to handle late starts or missed checks).

**macOS (launchd):**
1. Edit `~/Library/LaunchAgents/com.promaia.agent.plist`
2. Change `--window 5` to desired value (in minutes after start time)
3. Reload as above

**Note:** The window only applies AFTER the event starts, not before. Events will not trigger early.

## File Locations

- **PID File:** `~/.promaia/calendar_monitor.pid`
- **Log File:** `~/.promaia/calendar_monitor.log`
- **Error Log:** `~/.promaia/calendar_monitor.error.log` (launchd only)
- **Plist:** `~/Library/LaunchAgents/com.promaia.agent.plist` (macOS)

## Troubleshooting

### Daemon Not Starting

Check logs:
```bash
maia daemon logs
```

Check launchd status (macOS):
```bash
launchctl list | grep promaia
```

### Stale PID File

If the daemon shows a stale PID:
```bash
maia daemon status
# Shows: "Stale PID: 12345 (process not found)"

# Remove stale PID and start fresh
maia daemon start
```

### Permission Errors

Ensure the daemon has permission to access:
- Google Calendar credentials (`~/.promaia/credentials.json`)
- Notion API token (in `.env` or environment)
- Slack tokens (if using messaging)

### High CPU Usage

If the daemon uses high CPU:
1. Increase check interval: `--interval 5` (check every 5 minutes)
2. The trigger window is already optimized at 5 minutes by default

## Platform Support

### macOS (launchd)

Full support via launchd. The daemon:
- Auto-starts on boot via launchd
- Auto-restarts on crash
- Logs to both file and system console
- Handles SIGHUP for log rotation

### Linux (systemd)

Coming soon! For now, run manually:
```bash
# Run in foreground
maia agent calendar-monitor

# Or in background with nohup
nohup maia agent calendar-monitor &
```

## Advanced Usage

### Manual launchd Control

Load/unload without using `maia` commands:

```bash
# Load (enable)
launchctl load ~/Library/LaunchAgents/com.promaia.agent.plist

# Unload (disable)
launchctl unload ~/Library/LaunchAgents/com.promaia.agent.plist

# Check if loaded
launchctl list | grep promaia
```

### Log Rotation

Send SIGHUP to reopen log files (useful for log rotation):

```bash
# Get PID
PID=$(cat ~/.promaia/calendar_monitor.pid)

# Send SIGHUP
kill -HUP $PID
```

### Environment Variables

The daemon inherits environment variables from the plist. To add custom variables:

1. Edit `~/Library/LaunchAgents/com.promaia.agent.plist`
2. Add to `<key>EnvironmentVariables</key>` section:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>NOTION_TOKEN</key>
    <string>your_token_here</string>
    <key>SLACK_BOT_TOKEN</key>
    <string>your_token_here</string>
    ...
</dict>
```

3. Reload the service

**Note:** It's better to use `~/.promaia/.env` for secrets instead of the plist.

## Monitoring

### Check Daemon Health

```bash
# Quick status
maia daemon status

# Recent activity
maia daemon logs | tail -20

# Watch live
maia daemon logs --follow
```

### Expected Log Output

When running normally, you should see:

```
2026-02-06 10:00:00 - INFO - Calendar monitor started
2026-02-06 10:00:00 - INFO - Checking calendars...
2026-02-06 10:15:00 - INFO - Calendar event detected → triggering agent
2026-02-06 10:15:00 - INFO -   Agent: grace
2026-02-06 10:15:00 - INFO -   Event: Check in with team
2026-02-06 10:15:00 - INFO - Agent completed successfully
```

## Production Deployment

For production use:

1. **Enable daemon:**
   ```bash
   maia daemon enable
   ```

2. **Test:**
   ```bash
   # Check status
   maia daemon status

   # Create test calendar event (5 minutes from now)
   # Watch logs
   maia daemon logs --follow
   ```

3. **Monitor:**
   - Set up log monitoring (e.g., with `logwatch`)
   - Configure alerts for errors
   - Check daemon status daily

4. **Maintenance:**
   - Rotate logs periodically: `gzip ~/.promaia/calendar_monitor.log && touch ~/.promaia/calendar_monitor.log && kill -HUP $(cat ~/.promaia/calendar_monitor.pid)`
   - Review logs weekly for errors
   - Update Promaia: `git pull && pip install -e .`

## See Also

- [MESSAGING_SETUP.md](MESSAGING_SETUP.md) - Configure Slack/Discord for agent conversations
- [Agent Configuration](../README.md#agents) - Configure agents and calendars
