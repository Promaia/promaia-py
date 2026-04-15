# OCR Quick Start Guide

Get started with Promaia's OCR pipeline in 5 minutes.

## 1. Install Dependencies

```bash
pip install google-cloud-vision opencv-python numpy
```

## 2. Get Google Cloud Vision API Key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create/select a project
3. Enable "Cloud Vision API"
4. Create API key under Credentials
5. Add to `.env`:

```bash
GOOGLE_CLOUD_VISION_API_KEY=your_api_key_here
```

## 3. Set Up OCR

```bash
promaia ocr setup --workspace koii
```

This creates:
- `data/uploads/pending/` - Upload images here
- `data/uploads/processed/` - Successful OCR
- `data/uploads/failed/` - Failed OCR
- `data/md/ocr/` - Markdown output

## 4. Upload Images

Copy your journal photos to:
```bash
data/uploads/pending/
```

Supported formats: JPG, PNG, HEIC, WebP, TIFF

## 5. Process Images

```bash
# Process all images
promaia ocr process

# Process single file
promaia ocr process --file image.jpg

# Process specific directory
promaia ocr process --directory /path/to/images
```

## 6. Check Results

```bash
# View statistics
promaia ocr status

# Review low-confidence results
promaia ocr review
```

## Example Output

```
OCR Processing Summary
━━━━━━━━━━━━━━━━━━━━━━━━━
Status          Count
━━━━━━━━━━━━━━━━━━━━━━━━━
Completed         25
Review Needed      3
Failed            2
Total            30
━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Markdown Files

Each image creates a markdown file:

```markdown
---
title: IMG_1234
source_image: data/uploads/pending/IMG_1234.jpg
confidence: 0.92
language: en
processed_date: 2026-02-02T10:30:00
text_length: 456
---

# IMG_1234

[Your handwritten text appears here...]
```

## Tips for Best Results

1. **Good Lighting** - Take photos in bright, even lighting
2. **Straight On** - Keep camera perpendicular to page
3. **Clear Photos** - Avoid blur and shadows
4. **Full Page** - Capture entire page in frame

## Troubleshooting

### "GOOGLE_CLOUD_VISION_API_KEY not found"
Add API key to `.env` file

### "No images found"
Check images are in `data/uploads/pending/`

### Low Confidence
- Retake photo with better lighting
- Ensure text is clear and legible
- Check for shadows or glare

## Notion Integration (Optional)

Want your OCR results in Notion?

```bash
# 1. Create a database in Notion
# 2. Copy the database URL
# 3. Add it to Promaia
maia ocr database add <your-notion-database-url>

# 4. Sync your results
maia ocr sync
```

See [OCR_NOTION_INTEGRATION.md](OCR_NOTION_INTEGRATION.md) for details.

## What's Next?

- OCR text is stored locally in markdown
- Images are organized automatically
- **Sync to Notion for full-text search**
- Review low-confidence results manually

## Cost

- First 1,000 images/month: **FREE**
- Additional: ~$1.50 per 1,000 images

## Full Documentation

See [OCR_SETUP.md](OCR_SETUP.md) for complete documentation.

## Testing Without API Key

Use mock engine for testing:

```json
{
  "global": {
    "ocr": {
      "engine": "mock"
    }
  }
}
```

Then run normally - no API calls made!

---

**Ready to go!** Start uploading your journal photos and let Promaia OCR extract the text.
