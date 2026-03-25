# Promaia Configuration Files

This directory contains feature-specific configuration files for promaia.

## Configuration Files

### `cms.json`
Configuration for the CMS/Webflow pipeline.

#### Image Processing Settings
- `image_processing.webp_conversion.enabled` (boolean): Enable/disable automatic WebP conversion
- `image_processing.webp_conversion.quality` (integer 1-100): WebP compression quality (default: 85)
- `image_processing.webp_conversion.convert_formats` (array): MIME types to convert to WebP
- `image_processing.webp_conversion.skip_formats` (array): MIME types to skip conversion
- `image_processing.webp_conversion.max_dimension` (integer): Maximum width/height in pixels

#### Webflow Settings
- `webflow.max_concurrent_uploads` (integer): Maximum parallel image uploads (default: 8)
- `webflow.retry_attempts` (integer): Number of retry attempts for failed uploads (default: 3)

## Configuration Hierarchy

Promaia uses a layered configuration approach:

1. **Code defaults** - Hard-coded fallback values in the application
2. **Config files** (`conf/*.json`) - Feature-specific settings
3. **Main config** (`promaia.config.json`) - Database and workspace configuration
4. **Environment variables** (`.env`) - Secrets, API keys, and environment-specific overrides

## Why WebP Conversion?

WebP format provides:
- **30-80% smaller file sizes** compared to JPEG/PNG
- **Faster page loads** for your website
- **Better SEO** due to improved performance
- **Lossless and lossy compression** options
- **Broad browser support** (95%+ of users)

## How Conversion Works

The system automatically:
1. **New images**: Converts PNG/JPEG to WebP when uploading to Webflow
2. **Existing images**: Re-processes Webflow-hosted JPEG/PNG images on resync
3. **Caching**: Tracks converted images in `data/webp_conversion_cache.json` to avoid re-processing
4. **Smart detection**: Skips images that are already WebP or have been converted

### Conversion Cache

The cache file (`data/webp_conversion_cache.json`) stores mappings of:
```json
{
  "original_url": "https://uploads-ssl.webflow.com/.../image.jpg",
  "webp_url": "https://uploads-ssl.webflow.com/.../image.webp"
}
```

This ensures:
- Images are only converted once
- Subsequent syncs are fast
- No duplicate uploads to Webflow

## Example Usage

To disable WebP conversion:
```json
{
  "image_processing": {
    "webp_conversion": {
      "enabled": false
    }
  }
}
```

To use higher quality WebP:
```json
{
  "image_processing": {
    "webp_conversion": {
      "enabled": true,
      "quality": 95
    }
  }
}
```

To convert only JPEGs (keep PNGs as-is):
```json
{
  "image_processing": {
    "webp_conversion": {
      "enabled": true,
      "convert_formats": ["image/jpeg"]
    }
  }
}
```
