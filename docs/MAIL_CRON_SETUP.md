# Promaia Mail Processing - Cron Job Setup

This guide shows you how to set up automatic processing of pending emails every 30 minutes using a cron job.

## Quick Setup

### 1. Set up the cron job

The cron job has been automatically configured! You can verify it with:

```bash
crontab -l
```

You should see:

```bash
*/30 * * * * /Users/kb20250422/Documents/dev/promaia-py/sync_all_databases.py >> /Users/kb20250422/Documents/dev/promaia-py/sync_cron.log 2>&1
*/30 * * * * /Users/kb20250422/Documents/dev/promaia-py/process_mail.py >> /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log 2>&1
```

### 2. Monitor the mail processing

Check the mail processing log to see when processing runs and the results:

```bash
tail -f /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log
```

## What It Does

The `process_mail.py` script:

1. **Processes new emails** from the last 72 hours
2. **Classifies** them using AI (relevant/spam/requires response)
3. **Generates draft responses** using vector search and AI
4. **Saves drafts** to the database for review

After processing, you can review drafts with:

```bash
maia mail -ws koii
# or
maia mail -ws trass
```

## Cron Schedule Explained

- `*/30 * * * *` means "every 30 minutes"
- The script will run at: 00:00, 00:30, 01:00, 01:30, etc.
- Both sync and mail processing run at the same time every 30 minutes

## Alternative Schedules

If you want different processing intervals, modify the cron expression:

```bash
# Edit crontab
crontab -e

# Every 15 minutes
*/15 * * * * /Users/kb20250422/Documents/dev/promaia-py/process_mail.py >> /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log 2>&1

# Every hour
0 * * * * /Users/kb20250422/Documents/dev/promaia-py/process_mail.py >> /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log 2>&1

# Every 2 hours
0 */2 * * * /Users/kb20250422/Documents/dev/promaia-py/process_mail.py >> /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log 2>&1

# Only during work hours (9 AM to 6 PM), every 30 minutes
*/30 9-18 * * * /Users/kb20250422/Documents/dev/promaia-py/process_mail.py >> /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log 2>&1
```

## Testing

### Test the script manually first:

```bash
cd /Users/kb20250422/Documents/dev/promaia
./process_mail.py
```

### Check for errors:

```bash
tail -20 /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log
```

### View recent drafts:

```bash
maia mail -ws koii
```

## Stopping the Cron Job

To disable automatic mail processing:

```bash
crontab -e
```

Comment out the line by adding `#` at the beginning:

```bash
# */30 * * * * /Users/kb20250422/Documents/dev/promaia-py/process_mail.py >> /Users/kb20250422/Documents/dev/promaia-py/mail_cron.log 2>&1
```

Or remove the line entirely and save.

## Troubleshooting

### Cron job not running?

1. Check if cron service is running: `sudo launchctl list | grep cron`
2. Check system logs: `tail -f /var/log/system.log | grep cron`
3. Verify script permissions: `ls -la process_mail.py` (should show `x` permission)

### Script errors?

1. Check the log file: `tail -50 mail_cron.log`
2. Test manually: `./process_mail.py`
3. Check if virtual environment exists: `ls -la venv/bin/python`

### No drafts generated?

Check:
1. Gmail database is synced (the sync cron job should handle this)
2. Recent emails exist (last 72 hours by default)
3. Emails require responses (not spam/promotions)
4. API keys are configured correctly

### Permission issues?

macOS may require permission for cron to access certain directories. If you get permission errors:

1. Go to System Preferences > Security & Privacy > Privacy > Full Disk Access
2. Add Terminal or the cron process to allowed applications

## Log File Locations

- **Mail Processing**: `/Users/kb20250422/Documents/dev/promaia-py/mail_cron.log`
- **Database Sync**: `/Users/kb20250422/Documents/dev/promaia-py/sync_cron.log`

## Features

- ✅ **Automated**: Processes emails every 30 minutes
- ✅ **Intelligent**: AI classification and response generation
- ✅ **Context-Aware**: Vector search across all databases
- ✅ **Safe**: Drafts require review before sending
- ✅ **Logged**: All processing results saved to log file
- ✅ **Timeout Protected**: 30-minute timeout prevents stuck processes
- ✅ **Zero overhead**: Only runs when processing

## Workflow

```
Every 30 minutes:
1. Sync databases (sync_all_databases.py)
2. Process new emails (process_mail.py)
   ↓
3. You review drafts when convenient (maia mail -ws koii)
   ↓
4. Refine and send approved drafts
   ↓
5. System learns from successful sends
```

## Related Documentation

- [MAIA_MAIL_README.md](MAIA_MAIL_README.md) - Full mail system documentation
- [MAIA_MAIL_QUICKSTART.md](MAIA_MAIL_QUICKSTART.md) - Quick start guide
- [CRON_SETUP.md](CRON_SETUP.md) - Database sync cron setup

## Current Configuration

- **Sync Interval**: Every 30 minutes
- **Processing Interval**: Every 30 minutes
- **Lookback Period**: Last 72 hours
- **Default Workspace**: koii (configurable in the script)
- **Timeout**: 30 minutes per run
- **Log Rotation**: Manual (logs append indefinitely)

## Tips

1. **Check logs regularly** to ensure processing is working
2. **Review drafts daily** to stay on top of email responses
3. **Adjust schedule** based on your email volume
4. **Monitor log file size** and rotate/archive as needed
5. **Test manually** after any configuration changes

## Success Indicators

When everything is working correctly, you should see:

```bash
$ tail -5 mail_cron.log
2025-10-19 16:50:42,345 - INFO - Processing output:
✅ Generated 31 draft(s)
2025-10-19 16:50:42,346 - INFO - Mail processing completed successfully
2025-10-19 16:50:42,346 - INFO - Exit code: 0
```

And when you run `maia mail -ws koii`, you'll see pending drafts ready for review!



