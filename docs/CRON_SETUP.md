# Promaia Database Sync - Cron Job Setup

This guide shows you how to set up automatic syncing of all promaia databases every 30 minutes using a cron job.

## Quick Setup

### 1. Set up the cron job

Open your terminal and edit your crontab:

```bash
crontab -e
```

Add this line to sync every 30 minutes:

```bash
*/30 * * * * $PROJECT_ROOT/sync_all_databases.py >> $PROJECT_ROOT/sync_cron.log 2>&1
```

Save and exit the editor (in vi/vim: press `ESC`, then type `:wq` and press Enter).

### 2. Verify the cron job

List your current cron jobs to verify it was added:

```bash
crontab -l
```

You should see your sync job listed.

### 3. Monitor the sync

Check the sync log to see when syncs run and their results:

```bash
tail -f $PROJECT_ROOT/sync_cron.log
```

## Cron Schedule Explained

- `*/30 * * * *` means "every 30 minutes"
- The script will run at: 00:00, 00:30, 01:00, 01:30, etc.

## Alternative Schedules

If you want different sync intervals, modify the cron expression:

```bash
# Every 15 minutes
*/15 * * * * $PROJECT_ROOT/sync_all_databases.py

# Every hour
0 * * * * $PROJECT_ROOT/sync_all_databases.py

# Every 2 hours
0 */2 * * * $PROJECT_ROOT/sync_all_databases.py

# Only during work hours (9 AM to 6 PM), every 30 minutes
*/30 9-18 * * * $PROJECT_ROOT/sync_all_databases.py
```

## Testing

### Test the script manually first:

```bash
cd $PROJECT_ROOT
./sync_all_databases.py
```

### Check for errors:

```bash
tail -20 $PROJECT_ROOT/sync_cron.log
```

## Stopping the Cron Job

To disable automatic syncing:

```bash
crontab -e
```

Comment out the line by adding `#` at the beginning:

```bash
# */30 * * * * $PROJECT_ROOT/sync_all_databases.py
```

Or remove the line entirely and save.

## Troubleshooting

### Cron job not running?

1. Check if cron service is running: `sudo launchctl list | grep cron`
2. Check system logs: `tail -f /var/log/system.log | grep cron`
3. Verify script permissions: `ls -la sync_all_databases.py` (should show `x` permission)

### Script errors?

1. Check the log file: `tail -50 sync_cron.log`
2. Test manually: `./sync_all_databases.py`
3. Check if virtual environment exists: `ls -la venv/bin/python`

### Permission issues?

macOS may require permission for cron to access certain directories. If you get permission errors:

1. Go to System Preferences > Security & Privacy > Privacy > Full Disk Access
2. Add Terminal or the cron process to allowed applications

## Features

- ✅ **Minimal**: Just one script + one cron line
- ✅ **Reliable**: Uses existing `maia sync` command
- ✅ **Logged**: All sync results saved to `sync_cron.log`
- ✅ **Safe**: 30-minute timeout prevents stuck processes
- ✅ **Zero overhead**: Only runs when syncing

