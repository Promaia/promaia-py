# OCR Setup Guide

This guide covers setting up and using Promaia's OCR (Optical Character Recognition) pipeline to process handwritten journal pages and other documents.

## Features

- 📸 Process hundreds of images automatically
- ✍️ Excellent handwriting recognition via Google Cloud Vision
- 📝 Convert to searchable markdown files
- 🔍 Full-text search across handwritten notes
- 📊 Confidence scoring and quality review
- 🗂️ Automatic file organization

## Prerequisites

### 1. Google Cloud Vision API Setup

Promaia uses Google Cloud Vision API for OCR, which provides excellent handwriting recognition.

#### Option A: Using API Key (Recommended for personal use)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing project
3. Enable the "Cloud Vision API"
4. Go to "Credentials" → "Create Credentials" → "API Key"
5. Copy the API key
6. Add to your `.env` file:
   ```bash
   GOOGLE_CLOUD_VISION_API_KEY=your_api_key_here
   ```

#### Option B: Using Service Account (For production)

1. Create a service account in Google Cloud Console
2. Grant "Cloud Vision API User" role
3. Create and download JSON key file
4. Add to config:
   ```json
   {
     "global": {
       "ocr": {
         "api_settings": {
           "credentials_path": "/path/to/service-account.json"
         }
       }
     }
   }
   ```

### 2. Install Dependencies

Dependencies are included in `requirements.txt` and will be installed automatically:

```bash
# If you need to reinstall OCR dependencies specifically:
pip install google-cloud-vision==3.7.0 opencv-python==4.9.0.80 numpy==1.26.3
```

## Quick Start

### 1. Set Up OCR for Your Workspace

```bash
promaia ocr setup --workspace koii
```

This command:
- Creates necessary directories
- Validates configuration
- Checks API credentials

### 2. Upload Images

Transfer your journal photos to the uploads directory:

```bash
# Default location (can be changed in config)
data/uploads/pending/
```

Supported formats: JPG, PNG, HEIC, WebP, TIFF

### 3. Process Images

```bash
# Process all images in uploads directory
promaia ocr process

# Process specific directory
promaia ocr process --directory /path/to/images

# Process single image
promaia ocr process --file image.jpg
```

### 4. Review Results

```bash
# Check processing status
promaia ocr status

# Review low-confidence results
promaia ocr review
```

## Configuration

### Basic Configuration

Add to `promaia.config.json`:

```json
{
  "global": {
    "ocr": {
      "enabled": true,
      "engine": "google_cloud_vision",
      "uploads_directory": "data/uploads/pending",
      "processed_directory": "data/uploads/processed",
      "failed_directory": "data/uploads/failed",
      "batch_size": 10,
      "confidence_threshold": 0.7
    }
  },
  "databases": {
    "uploads": {
      "source_type": "ocr",
      "workspace": "koii",
      "sync_enabled": true,
      "markdown_directory": "data/md/ocr/koii/uploads"
    }
  }
}
```

### Advanced Configuration

```json
{
  "global": {
    "ocr": {
      "enabled": true,
      "engine": "google_cloud_vision",
      "uploads_directory": "data/uploads/pending",
      "processed_directory": "data/uploads/processed",
      "failed_directory": "data/uploads/failed",
      "batch_size": 10,
      "confidence_threshold": 0.7,
      "preprocessing": {
        "resize_max": 4096,
        "enhance_contrast": true,
        "denoise": false
      },
      "api_settings": {
        "credentials_path": "/path/to/credentials.json"
      }
    }
  }
}
```

## Workflow

### Typical Usage Pattern

1. **Take Photos**: Use your phone to photograph journal pages
2. **Transfer**: Move photos to computer
3. **Upload**: Copy to `data/uploads/pending/`
4. **Process**: Run `promaia ocr process`
5. **Search**: Use Promaia chat to search handwritten notes

### File Organization

```
data/
├── uploads/
│   ├── pending/          # Place images here
│   ├── processed/        # Successful OCR
│   └── failed/           # Failed OCR
├── md/
│   └── ocr/
│       └── koii/
│           └── uploads/  # Markdown files
└── hybrid_metadata.db    # OCR metadata
```

## OCR Results

### Markdown Output

Each processed image creates a markdown file with:

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

[Extracted text content here...]
```

### Status Types

- **completed**: OCR successful, confidence ≥ threshold
- **review_needed**: OCR successful but low confidence
- **failed**: OCR failed (image unreadable, API error, etc.)

## Commands Reference

### `promaia ocr setup`

Initialize OCR for a workspace.

```bash
promaia ocr setup --workspace WORKSPACE
```

### `promaia ocr process`

Process images through OCR.

```bash
# Process all images in uploads directory
promaia ocr process

# Process specific directory
promaia ocr process --directory /path/to/images

# Process single file
promaia ocr process --file image.jpg

# Custom batch size
promaia ocr process --batch-size 5
```

### `promaia ocr status`

Show OCR statistics and configuration.

```bash
# All workspaces
promaia ocr status

# Specific workspace
promaia ocr status --workspace koii
```

### `promaia ocr review`

Review low-confidence results.

```bash
# Default threshold (0.7)
promaia ocr review

# Custom threshold
promaia ocr review --threshold 0.8 --workspace koii
```

## Tips & Best Practices

### For Best OCR Results

1. **Good Lighting**: Take photos in good lighting
2. **Straight On**: Keep camera perpendicular to page
3. **Full Page**: Capture entire page in frame
4. **Clear Photos**: Avoid blur and shadows
5. **High Resolution**: Use highest quality camera setting

### Handling Low Confidence Results

When confidence is below threshold:

1. Check original image quality
2. Review markdown output for errors
3. Consider retaking photo if text is critical
4. Manual correction may be needed

### Cost Management

Google Cloud Vision API pricing (as of 2026):
- First 1,000 images/month: **Free**
- Additional images: ~$1.50 per 1,000

Tips to minimize costs:
- Process in batches when convenient
- Review image quality before processing
- Use mock engine for testing (`"engine": "mock"`)

## Troubleshooting

### "GOOGLE_CLOUD_VISION_API_KEY not found"

Add API key to `.env` file:
```bash
GOOGLE_CLOUD_VISION_API_KEY=your_api_key_here
```

### "No images found in directory"

Check:
- Images are in correct directory
- File formats are supported (JPG, PNG, etc.)
- Files have proper extensions

### "Failed to create directory"

Check permissions:
```bash
chmod -R 755 data/uploads
```

### Low Confidence Results

Common causes:
- Poor image quality (blur, shadows)
- Messy handwriting
- Faded ink
- Background noise

Solutions:
- Retake photo with better lighting
- Use image enhancement tools
- Adjust `preprocessing` settings

## Development & Testing

### Using Mock Engine

For testing without API calls:

```json
{
  "global": {
    "ocr": {
      "engine": "mock"
    }
  }
}
```

### Testing with Sample Images

```bash
# Process single test image
promaia ocr process --file test_image.jpg

# Check results
promaia ocr status
cat data/md/ocr/koii/uploads/2026-02-02\ test_image.md
```

## Future Enhancements

Planned features:
- Automatic file watching
- Drawing/doodle embedding
- Multi-page document grouping
- Mobile upload integration
- Handwriting style learning
- Tesseract fallback engine

## Support

For issues or questions:
- Check logs: Look for OCR-related errors
- Review configuration: Ensure settings are correct
- Validate API: Test with single image first
- GitHub Issues: Report bugs or request features

## API Documentation

See also:
- [Google Cloud Vision API Docs](https://cloud.google.com/vision/docs)
- [Promaia OCR Architecture](OCR_ARCHITECTURE.md)
- [OCR API Reference](OCR_API.md)
