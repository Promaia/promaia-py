"""
WebP conversion cache manager.

Tracks which Webflow images have already been converted to WebP
to avoid re-processing on subsequent syncs.
"""
import json
import os
from pathlib import Path
from typing import Dict, Optional
import logging

from promaia.utils.env_writer import get_data_dir

logger = logging.getLogger(__name__)


def _get_cache_file() -> Path:
    """Get the cache file path using the standard data directory."""
    return get_data_dir() / "data" / "webp_conversion_cache.json"

# In-memory cache
_cache: Optional[Dict[str, str]] = None


def _ensure_cache_file():
    """Ensure the cache file exists with proper structure."""
    if not _get_cache_file().exists():
        _get_cache_file().parent.mkdir(parents=True, exist_ok=True)
        initial_data = {
            "_metadata": {
                "description": "Cache of Webflow image URLs that have been converted to WebP",
                "format": "original_url -> webp_url mapping"
            },
            "conversions": {}
        }
        with open(_get_cache_file(), 'w') as f:
            json.dump(initial_data, f, indent=2)


def load_cache() -> Dict[str, str]:
    """
    Load the WebP conversion cache from disk.

    Returns:
        Dictionary mapping original URLs to WebP URLs
    """
    global _cache

    if _cache is not None:
        return _cache

    _ensure_cache_file()

    try:
        with open(_get_cache_file(), 'r') as f:
            data = json.load(f)
            _cache = data.get("conversions", {})
            return _cache
    except Exception as e:
        logger.error(f"Failed to load WebP cache: {e}")
        _cache = {}
        return _cache


def get_cached_webp_url(original_url: str) -> Optional[str]:
    """
    Get the cached WebP URL for an original image URL.

    Args:
        original_url: Original Webflow image URL

    Returns:
        WebP URL if cached, None otherwise
    """
    cache = load_cache()
    return cache.get(original_url)


def cache_webp_conversion(original_url: str, webp_url: str):
    """
    Cache a WebP conversion mapping.

    Args:
        original_url: Original Webflow image URL
        webp_url: New WebP URL after conversion
    """
    global _cache

    cache = load_cache()
    cache[original_url] = webp_url

    # Save to disk
    try:
        _ensure_cache_file()
        with open(_get_cache_file(), 'r') as f:
            data = json.load(f)

        data["conversions"][original_url] = webp_url

        with open(_get_cache_file(), 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Cached WebP conversion: {original_url} -> {webp_url}")
    except Exception as e:
        logger.error(f"Failed to save WebP cache: {e}")


def is_already_converted(url: str) -> bool:
    """
    Check if an image has already been converted to WebP.

    Args:
        url: Image URL to check

    Returns:
        True if already converted and cached, False otherwise
    """
    return get_cached_webp_url(url) is not None


def should_convert_webflow_image(url: str) -> bool:
    """
    Check if a Webflow-hosted image should be converted to WebP.

    Args:
        url: Webflow image URL

    Returns:
        True if the image should be converted (JPEG/PNG and not already converted)
    """
    # Already converted (cached)?
    if is_already_converted(url):
        return False

    # Already WebP?
    if url.lower().endswith('.webp'):
        return False

    # Is JPEG/PNG?
    url_lower = url.lower()
    if url_lower.endswith(('.jpg', '.jpeg', '.png')):
        return True

    return False
