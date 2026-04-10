"""
CMS configuration loader for promaia.

This module loads and validates CMS-specific configuration from conf/cms.json.
Provides defaults if the config file is missing or incomplete.
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Cache the loaded config to avoid repeated file reads
_cached_config: Optional[Dict[str, Any]] = None

# Default configuration values
DEFAULT_CMS_CONFIG = {
    "image_processing": {
        "webp_conversion": {
            "enabled": True,
            "quality": 85,
            "convert_formats": ["image/jpeg", "image/png"],
            "skip_formats": ["image/gif", "image/webp", "image/svg+xml"],
            "max_dimension": 2048
        }
    },
    "webflow": {
        "max_concurrent_uploads": 8,
        "retry_attempts": 3
    }
}


def get_config_path() -> Path:
    """
    Get the path to the CMS config file.

    Returns:
        Path to conf/cms.json
    """
    # Get project root (parent of promaia package)
    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent
    return project_root / "conf" / "cms.json"


def load_cms_config(reload: bool = False) -> Dict[str, Any]:
    """
    Load CMS configuration from conf/cms.json.

    Uses cached config by default for performance. Set reload=True to
    force reloading from disk.

    Args:
        reload: If True, reload config from disk even if cached

    Returns:
        Dictionary containing CMS configuration
    """
    global _cached_config

    # Return cached config if available and not forcing reload
    if _cached_config is not None and not reload:
        return _cached_config

    config_path = get_config_path()

    # If config file doesn't exist, return defaults
    if not config_path.exists():
        logger.warning(f"CMS config file not found at {config_path}, using defaults")
        _cached_config = DEFAULT_CMS_CONFIG.copy()
        return _cached_config

    try:
        with open(config_path, 'r') as f:
            loaded_config = json.load(f)

        # Merge with defaults to ensure all keys exist
        config = _deep_merge(DEFAULT_CMS_CONFIG.copy(), loaded_config)

        _cached_config = config
        return config

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in CMS config file {config_path}: {e}")
        _cached_config = DEFAULT_CMS_CONFIG.copy()
        return _cached_config
    except Exception as e:
        logger.error(f"Error loading CMS config from {config_path}: {e}")
        _cached_config = DEFAULT_CMS_CONFIG.copy()
        return _cached_config


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries, with override values taking precedence.

    Args:
        base: Base dictionary with default values
        override: Dictionary with override values

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def get_webp_config() -> Dict[str, Any]:
    """
    Get WebP conversion configuration.

    Returns:
        Dictionary containing WebP conversion settings
    """
    config = load_cms_config()
    return config.get("image_processing", {}).get("webp_conversion", {})


def is_webp_conversion_enabled() -> bool:
    """
    Check if WebP conversion is enabled.

    Returns:
        True if WebP conversion is enabled, False otherwise
    """
    webp_config = get_webp_config()
    return webp_config.get("enabled", True)


def get_webp_quality() -> int:
    """
    Get WebP conversion quality setting.

    Returns:
        Quality value (1-100), default 85
    """
    webp_config = get_webp_config()
    return webp_config.get("quality", 85)


def should_convert_to_webp(mime_type: str) -> bool:
    """
    Check if a given MIME type should be converted to WebP.

    Args:
        mime_type: MIME type of the image (e.g., "image/jpeg")

    Returns:
        True if the image should be converted to WebP, False otherwise
    """
    if not is_webp_conversion_enabled():
        return False

    webp_config = get_webp_config()
    convert_formats = webp_config.get("convert_formats", ["image/jpeg", "image/png"])
    skip_formats = webp_config.get("skip_formats", ["image/gif", "image/webp", "image/svg+xml"])

    # Skip if in skip list
    if mime_type in skip_formats:
        return False

    # Convert if in convert list
    return mime_type in convert_formats


def get_webflow_config() -> Dict[str, Any]:
    """
    Get Webflow-specific configuration.

    Returns:
        Dictionary containing Webflow settings
    """
    config = load_cms_config()
    return config.get("webflow", {})
