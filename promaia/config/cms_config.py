"""
CMS configuration loader for promaia.

Configuration is layered, later layers winning:

1. ``DEFAULT_CMS_CONFIG`` (below)
2. ``conf/cms.json`` in the repo
3. the optional ``cms`` section of ``<data dir>/promaia.config.json``
   (per-install overrides, e.g. ``{"cms": {"target": "site"}}``)

The data-dir file is only ever READ here; this module never writes it.

``target`` picks where ``maia cms sync`` publishes: ``"webflow"`` (the
legacy Webflow CMS path, default) or ``"site"`` (content files in the
static-site repo, see ``promaia.cms_site``).
"""
import copy
import json
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Cache the loaded config to avoid repeated file reads
_cached_config: Optional[Dict[str, Any]] = None

CMS_TARGETS = ("webflow", "site")

# Default configuration values
DEFAULT_CMS_CONFIG = {
    "target": "webflow",
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
    },
    "site": {
        # Public origin of the site; used for "read on website" links,
        # newsletter image URLs and the published-check feed.
        "base_url": "https://www.koiib.com",
        "repo_url": "https://github.com/koiibenvenutto/koiib-website.git",
        "branch": "main",
        # Relative paths resolve under get_data_dir(); absolute paths are used as-is.
        "checkout_dir": "sites/koiib-website",
        "content_dir": "content/posts",
        "media_dir": "public/media/posts",
        "media_url_prefix": "/media/posts",
        "post_path": "/post/{slug}",
        "feed_path": "/feed/posts/{slug}.json",
        # "git-push": commit + push to `branch` (the site repo's CI deploys);
        # "none": write files only.
        "publish": "git-push",
        "image_max_width": 2000,
        "image_quality": 82,
        # Never write pages ticked "KOii chat" (note: 16 of the 30 posts that
        # were public on Webflow in Nov 2026 have it ticked; they are "Live",
        # so this only matters if one is set back to "To sync"/"Update on sync").
        "skip_koii_chat_pages": True
    }
}


def get_config_path() -> Path:
    """
    Get the path to the repo-level CMS config file.

    Returns:
        Path to conf/cms.json
    """
    from promaia.utils.env_writer import get_project_root
    return Path(get_project_root()) / "conf" / "cms.json"


def _read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON object from *path*; missing or invalid files give {}."""
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
    return {}


def _data_dir_cms_section() -> Dict[str, Any]:
    """The ``cms`` section of <data dir>/promaia.config.json (read-only)."""
    try:
        from promaia.utils.env_writer import get_config_path as get_promaia_config_path
        section = _read_json(get_promaia_config_path()).get("cms", {})
        return section if isinstance(section, dict) else {}
    except Exception as e:
        logger.warning(f"Could not read cms section of promaia.config.json: {e}")
        return {}


def load_cms_config(reload: bool = False) -> Dict[str, Any]:
    """
    Load the layered CMS configuration (defaults <- conf/cms.json <- data-dir cms section).

    Uses cached config by default for performance. Set reload=True to
    force reloading from disk.

    Args:
        reload: If True, reload config from disk even if cached

    Returns:
        Dictionary containing CMS configuration
    """
    global _cached_config

    if _cached_config is not None and not reload:
        return _cached_config

    config = _deep_merge(DEFAULT_CMS_CONFIG, _read_json(get_config_path()))
    config = _deep_merge(config, _data_dir_cms_section())

    if config.get("target") not in CMS_TARGETS:
        logger.warning(f"Unknown cms target {config.get('target')!r}; using 'webflow'")
        config["target"] = "webflow"

    _cached_config = config
    return config


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries, with override values taking precedence.
    Neither input is mutated.

    Args:
        base: Base dictionary with default values
        override: Dictionary with override values

    Returns:
        Merged dictionary
    """
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def get_cms_target(override: Optional[str] = None) -> str:
    """Return the CMS publish target: ``"webflow"`` or ``"site"``."""
    if override:
        if override not in CMS_TARGETS:
            raise ValueError(f"cms target must be one of {CMS_TARGETS}, got {override!r}")
        return override
    return load_cms_config().get("target", "webflow")


def get_site_config() -> Dict[str, Any]:
    """Return the ``site`` section (static-site target settings)."""
    return load_cms_config().get("site", {})


def get_site_base_url(override: Optional[str] = None) -> str:
    """Public origin of the site, without a trailing slash."""
    return (override or get_site_config().get("base_url") or "").rstrip("/")


def get_site_media_base_url(override: Optional[str] = None, base_override: Optional[str] = None) -> str:
    """Origin the newsletter reads the feed and images from.

    Defaults to the public base_url. Set ``site.media_base_url`` (or pass an
    override) to the Pages project's own URL (https://koiib-website.pages.dev)
    when emails must go out before the domain points at the new site: links
    still use base_url, images and the published check use this origin.
    """
    return (override or get_site_config().get("media_base_url") or get_site_base_url(base_override)).rstrip("/")


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
