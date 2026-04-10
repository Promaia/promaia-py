# OCR Notion Integration Guide

Sync your OCR results to a Notion database for easy organization and searching.

## Quick Start

### 1. Create a Notion Database

1. Open Notion
2. Create a new database (table view recommended)
3. Name it "OCR Uploads" or whatever you prefer
4. Copy the database link/URL

### 2. Add Database to Promaia

```bash
# Paste your Notion database URL
maia ocr database add https://notion.so/your-workspace/DATABASE_ID

# Or if you have just the ID
maia ocr database add DATABASE_ID
```

This will:
- ✅ Verify you have access to the database
- ✅ Check the database schema
- ✅ Save the configuration

### 3. Set Up Database Properties (Recommended)

View the recommended schema:

```bash
maia ocr database setup
```

This shows you what properties to create in your Notion database:

| Property | Type | Description |
|----------|------|-------------|
| Title | Title | Page title (auto-generated from filename) |
| Upload Date | Date | When image was uploaded |
| Processing Date | Date | When OCR processing completed |
| OCR Confidence | Number | Confidence score (0-100%) |
| Status | Select | Processing status (Completed, Review Needed, Failed) |
| Source Image | Files | Original image file |
| Language | Select | Detected language |
| Text Length | Number | Character count |
| Notes | Rich Text | Manual notes or corrections |

**Note:** You can use any property names you want. Promaia will try to match them automatically.

### 4. Process Images

```bash
# Process your images as usual
maia ocr process
```

### 5. Sync to Notion

```bash
# Sync all unsynced results
maia ocr sync

# Sync with limit
maia ocr sync --limit 10

# Sync specific workspace
maia ocr sync --workspace koii
```

This creates Notion pages with:
- ✅ OCR extracted text as page content
- ✅ All metadata (confidence, language, dates)
- ✅ Proper status based on results
- ✅ Automatic title from filename

## Example Workflow

### Initial Setup

```bash
# 1. Set up OCR
maia ocr setup --workspace koii

# 2. Create database in Notion (manually)
# 3. Copy the database URL

# 4. Add database to Promaia
maia ocr database add https://notion.so/workspace/abc123...

# 5. Verify configuration
maia ocr database info
```

### Daily Use

```bash
# 1. Copy journal photos to uploads
cp ~/Photos/journal/*.jpg data/uploads/pending/

# 2. Process them
maia ocr process

# 3. Sync to Notion
maia ocr sync

# 4. Check what was synced
maia ocr status
```

## Database Schema Setup

### Minimal Schema

The absolute minimum you need:

- **Title** (title property) - Required by Notion
- **Status** (select property) - Recommended

### Recommended Schema

Copy this schema to your Notion database:

**Status Property (Select):**
- Pending
- Processing
- Completed
- Failed
- Review Needed

**Language Property (Select):**
- English
- Spanish
- French
- Unknown

**OCR Confidence (Number):**
- Format: Percent
- Range: 0-100

### Auto-Detection

Promaia tries to match your existing properties by name. It looks for:

- Title properties automatically
- Properties named "Status", "State"
- Properties named "Upload Date", "Uploaded"
- Properties named "OCR Confidence", "Confidence"
- Properties named "Language", "Lang"

So you can use your own naming and Promaia will adapt!

## Commands Reference

### Database Management

```bash
# Add a Notion database
maia ocr database add <url> [--workspace WORKSPACE]

# Show recommended schema
maia ocr database setup [--workspace WORKSPACE]

# Show current database info
maia ocr database info
```

### Sync Operations

```bash
# Sync all unsynced results
maia ocr sync

# Sync with limit
maia ocr sync --limit 50

# Sync specific workspace
maia ocr sync --workspace koii
```

## What Gets Synced

When you run `maia ocr sync`, Promaia:

1. **Finds unsynced results** - Only processes OCR results that haven't been synced yet
2. **Creates Notion pages** - One page per image
3. **Sets properties** - Fills in all metadata
4. **Adds content** - OCR text as page blocks (paragraphs)
5. **Updates tracking** - Marks results as synced in local database

## Synced Page Structure

Each synced page contains:

**Properties:**
- Title: "IMG_1234 - 2026-02-02"
- Upload Date: When you uploaded the image
- Processing Date: When OCR completed
- OCR Confidence: 0.92 (92%)
- Status: "Completed" (or "Review Needed" if low confidence)
- Language: "English"
- Text Length: 456 characters

**Content:**
The extracted text as paragraphs, split by line breaks.

## Checking Sync Status

```bash
# View overall status
maia ocr status

# See which results need syncing
maia ocr database info
```

The status command shows:
- Total processed
- How many synced to Notion
- How many pending sync

## Re-syncing

If you want to re-sync results that were already synced:

```bash
# Currently, Promaia only syncs new results
# To re-sync, you would need to:
# 1. Delete the pages in Notion
# 2. Clear the page_id in the database
# (Coming soon: --force flag to re-sync)
```

## Troubleshooting

### "Cannot access database"

**Check:**
- Database URL is correct
- You've shared the database with your Notion integration
- Your `NOTION_TOKEN` is correct in `.env`

**Fix:**
1. Open your database in Notion
2. Click "Share" (top right)
3. Invite your integration
4. Try again

### "Missing recommended properties"

This is just a warning. Promaia will work with any properties you have.

**To add recommended properties:**
1. Open database in Notion
2. Click "+ New property"
3. Add the properties from `maia ocr database setup`

### "No OCR database configured"

You haven't added a database yet.

**Fix:**
```bash
maia ocr database add <your-database-url>
```

### Sync creates pages but they're empty

**Check:**
- Make sure `maia ocr process` completed successfully
- Verify markdown files exist in `data/md/ocr/`
- Check OCR confidence wasn't too low (failed OCR)

## Advanced Usage

### Multiple Workspaces

You can sync different workspaces to different databases:

```bash
# Add database for koii workspace
maia ocr database add <url1> --workspace koii

# Add database for work workspace
maia ocr database add <url2> --workspace work

# Sync each
maia ocr sync --workspace koii
maia ocr sync --workspace work
```

### Batch Syncing

Process and sync in one go:

```bash
# Process images
maia ocr process

# Sync immediately
maia ocr sync
```

Or create a script:

```bash
#!/bin/bash
# process_and_sync.sh

maia ocr process
maia ocr sync
maia ocr status
```

### Filtering in Notion

Once synced, you can create views in Notion:

- **High Confidence** - Filter: Confidence ≥ 90%
- **Needs Review** - Filter: Status = "Review Needed"
- **By Language** - Group by: Language
- **Recent** - Sort by: Processing Date (desc)

## Limitations

### File Uploads

Currently, Notion API doesn't support direct file uploads. The original images are **not** uploaded to the "Source Image" property.

**Workaround:** Original images are stored locally in `data/uploads/processed/`

**Future:** We may add support for external hosting (S3, etc.) to link images.

### Text Length

Notion has a limit of ~2000 characters per block. Very long OCR text is split into multiple paragraph blocks (max 50 blocks).

### Notion API Rate Limits

Notion API has rate limits. If syncing many results at once, use the `--limit` flag:

```bash
# Sync 50 at a time
maia ocr sync --limit 50
```

## Next Steps

Once your OCR results are in Notion:

- ✅ Search across all handwritten notes
- ✅ Organize with tags and databases
- ✅ Link to other pages
- ✅ Add manual corrections in the Notes field
- ✅ Create filtered views for different purposes

## Full Example

```bash
# Initial setup (one time)
maia ocr setup --workspace koii
# Create database in Notion, copy URL
maia ocr database add https://notion.so/workspace/abc123
maia ocr database setup  # View recommended schema
# Add properties in Notion

# Regular workflow
cp ~/Photos/journal/*.jpg data/uploads/pending/
maia ocr process
maia ocr sync
maia ocr status

# Check results in Notion!
```

---

**You're all set!** Your handwritten journal pages are now searchable in Notion 🎉
