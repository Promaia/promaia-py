# Maia Mail - Quick Start Guide

## 🚀 Getting Started in 5 Steps

### Step 1: Set Up Gmail OAuth

**Important:** You need to authorize Gmail via the unified auth system (one-time setup).

```bash
maia auth configure google
```

This will:
1. Authorize Gmail and Calendar access via the Promaia OAuth proxy
2. Store tokens locally for future use
3. Save the token for future use

**Note:** You need to enable the Gmail API and create OAuth credentials first. The command will walk you through it.

### Step 2: Verify Gmail Connection

```bash
maia gmail test --workspace koii --email koii.create@gmail.com
```

This should show:
```
✅ Gmail connection successful!
📬 Testing email query...
✅ Found X recent email threads
```

### Step 3: Sync Recent Emails (Optional)

If you want to make sure your recent emails are in the database:

```bash
maia database sync --source koii.gmail --days 7
```

### Step 4: Process New Emails

Run the processor to classify emails and generate drafts:

```bash
maia mail -p -ws koii
```

This will:
- Check emails from the last 2 hours
- Classify each one (relevant? spam? needs response?)
- Generate drafts for emails that need responses
- Save drafts to database

Output will look like:
```
📧 Processing emails for workspace: koii
Found 5 thread(s) to process
Processing: RE: Q4 Timeline Discussion
  → Classification: pertains=True, spam=False, requires_response=True
  → Found 12 relevant sources
  → Generated 156 word response
  ✅ Draft saved
...
✅ Generated 3 draft(s)
```

### Step 5: Review and Send

Launch the review interface:

```bash
maia mail -ws koii
```

You'll see:
```
╭──────────────────────────────────────────────────────╮
│  Maia Mail - Draft Review Queue                     │
│                                                      │
│  Progress: [░░░░░░░░░░░░] 0/3 resolved (0%)        │
│  Status: ✅ 0 sent  •  🗄️ 0 archived  •  ⏳ 3 pending  •  ⏭️  0 skipped│
╰──────────────────────────────────────────────────────╯

▶ [1] ⏳ RE: Q4 Timeline Discussion
       From: john@example.com | Oct 19, 08:30 AM
       Preview: Can you provide an update...
       Draft: 156 words | Context: 3 sources
```

**Navigation:**
- `↑/↓` - Navigate drafts
- `Enter` - Open chat to review/refine
- `a` - Archive (clear from queue 🗄️)
- `q` - Quit

## 📖 Detailed Workflow

### Viewing a Draft

Press `Enter` on a draft to see:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  INBOUND MESSAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

From:     john@example.com
Subject:  RE: Q4 Timeline Discussion
Date:     Friday, October 19, 2025 at 08:30 AM
Thread:   3 message(s) in thread

[Full inbound message...]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  YOUR DRAFT RESPONSE (156 words)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[Your generated response...]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  CONTEXT USED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sources:     12 documents from knowledge base
Databases:   journal, stories, cpj + 2 more
AI Model:    claude-sonnet-4-20250514
Generated:   2025-10-19T12:34:56Z

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ACTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Enter - Open chat to review/refine
  a  - Archive (clear from queue 🗄️)
  v  - View full context sources
  Esc - Back to list
```

### Refining a Draft

Press `c` to open the draft chat:

```
╭──── Draft #1 ────────────────────────────╮
│                                          │
│ Thanks for following up. Based on our   │
│ latest sprint planning, we're on track  │
│ for Q4 launch...                         │
│                                          │
╰──────────────────────────────────────────╯

💬 Chat to refine the draft, or type a command:
   /send [number] - Send draft
   /q - Return to review queue

You: make it more concise and add the specific date

🤔 Refining draft...

╭──── Draft #2 ────────────────────────────╮
│                                          │
│ Thanks for following up. We're on track │
│ for October 28th Q4 launch.             │
│                                          │
╰──────────────────────────────────────────╯

You: /send 2
```

### Sending a Draft

When you type `/send 2` or press `s` in detail view:

```
⚠️  Ready to send Draft #2
Type the first 5 characters to confirm: RE: Q
Confirm: RE: Q

📤 Sending Draft #2...
✅ Email sent successfully!
```

## 🔄 Automated Processing (Optional)

To automatically process emails every 30 minutes:

```bash
# Add to crontab -e
*/30 * * * * cd /path/to/promaia && ./venv/bin/python -m promaia mail -p -ws koii >> logs/mail_processor.log 2>&1
```

Then just run `maia mail -ws koii` whenever you want to review.

## 🛠️ Troubleshooting

### "Invalid OAuth scope" Error

Re-authorize with the correct scopes:

```bash
maia auth configure google
```

### "No drafts to review"

Generate some drafts first:

```bash
maia mail -p -ws koii
```

### "No Gmail database found"

Make sure you have a Gmail database configured:

```bash
maia database list
```

If not, add it:

```bash
maia database add koii.gmail --source-type gmail --database-id koii.create@gmail.com
```

## 💡 Tips

1. **Start Small** - Process just the last 2 hours first to test
2. **Review Before Auto-Send** - Get comfortable with drafts before setting up cron
3. **Check Context** - Press `v` to see what sources were used
4. **Iterate in Chat** - Use `c` to refine drafts multiple times
5. **Learn Over Time** - The system learns from successful sends

## 📚 Full Documentation

See `MAIA_MAIL_README.md` for complete documentation.

## ⚡ Quick Commands Cheat Sheet

```bash
# Setup
maia auth configure google
maia gmail test --workspace koii --email koii.create@gmail.com

# Process & Review
maia mail -p -ws koii          # Process new emails
maia mail -ws koii             # Review drafts

# Sync emails
maia database sync --source koii.gmail --days 7
```

## 🎯 Expected First Run

On your first run with no learning data:

1. ✅ Classification will work (determines what needs response)
2. ✅ Context building will work (finds relevant docs)
3. ⚠️  Responses will be generic (no learned patterns yet)
4. ✅ After first successful send, starts learning your style

Each time you send a response, the system learns and improves!

---

**Ready to start?** Run: `maia auth configure google`

