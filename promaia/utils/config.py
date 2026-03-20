"""
Configuration management for Maia.
"""
import os
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

def _sync_config_file():
    from promaia.utils.env_writer import get_sync_config_path
    return str(get_sync_config_path())

def _main_config_file():
    from promaia.utils.env_writer import get_config_path
    return str(get_config_path())

# Load environment variables from .env file
def load_environment():
    """Load environment variables from .env file in the data directory."""
    from promaia.utils.env_writer import get_env_path

    dotenv_path = str(get_env_path())

    # Load environment variables
    load_dotenv(dotenv_path=dotenv_path)

    # Ensure default prompt files exist
    from promaia.utils.env_writer import ensure_default_prompts
    ensure_default_prompts()

    # Temporary OpenRouter shim: if OpenRouter key is set but Anthropic isn't,
    # use OpenRouter as the Anthropic backend via base_url redirect.
    # TODO: replace with proper multi-provider routing
    if os.getenv("OPENROUTER_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        os.environ["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        logger.info("Using OpenRouter as Anthropic backend (temporary shim)")

    # Validate required API keys
    required_keys = {
        "OPENAI_API_KEY": "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic",
        "GOOGLE_API_KEY": "Google"
    }
    
    # Optional API keys (for additional features)
    optional_keys = {
        "LLAMA_BASE_URL": "Local Llama"
    }
    
    missing_keys = []
    for key, service in required_keys.items():
        if not os.getenv(key):
            missing_keys.append(f"{service} ({key})")
    
    # Check optional keys and note if they're available
    available_optional = []
    for key, service in optional_keys.items():
        if os.getenv(key):
            available_optional.append(f"{service} ({key})")
    
    if missing_keys:
        logger.debug("The following API keys are not set in your .env file:")
        for key in missing_keys:
            logger.debug(f"  - {key}")
        logger.debug("Some features may not work without these keys.")

    if available_optional:
        logger.debug("Optional AI services available:")
        for key in available_optional:
            logger.debug(f"  - {key}")
    
    return len(missing_keys) == 0

def get_config() -> Dict[str, Any]:
    """
    Get the current configuration.
    
    Returns:
        Configuration dictionary
    """
    if os.path.exists(_sync_config_file()):
        try:
            with open(_sync_config_file(), 'r') as f:
                content = f.read().strip()
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # If it's not valid JSON, assume it's just a timestamp string
                    # This handles the old format
                    return {'last_sync': content}
        except Exception as e:
            logger.error(f"Error reading config file: {str(e)}")
    
    # Return default config if no file exists or there was an error
    return {
        'last_sync': None,
        'last_days': 5,  # Default to 5 days for sync
        'chat_context_days': 5  # Default to 5 days for chat context
    }

def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update the configuration with new values.
    
    Args:
        updates: Dictionary of configuration values to update
        
    Returns:
        Updated configuration dictionary
    """
    # Get current config
    config = get_config()
    
    # Update with new values
    config.update(updates)
    
    # Save updated config
    with open(_sync_config_file(), 'w') as f:
        json.dump(config, f)
    
    return config

def update_last_sync_time(content_type: Optional[str] = None) -> str:
    """
    Update the last sync time to now, optionally for a specific content type.
    
    Args:
        content_type: Optional content type (e.g., "journal") to specify the sync time for.
        
    Returns:
        Current time as ISO format string
    """
    current_time = datetime.now(timezone.utc)
    iso_time = current_time.isoformat()
    
    sync_key = f'last_sync_{content_type}' if content_type else 'last_sync'
    update_config({sync_key: iso_time})
    
    return iso_time

def get_last_sync_time(content_type: Optional[str] = None) -> Optional[datetime]:
    """
    Get the last sync time, optionally for a specific content type.

    Args:
        content_type: Optional content type (e.g., "journal") to get the sync time for.

    Returns:
        Last sync time as a datetime object or None if not set or invalid.
    """
    config = get_config()
    sync_key = f'last_sync_{content_type}' if content_type else 'last_sync'
    time_str = config.get(sync_key)

    if not time_str and content_type: # Fallback to generic if specific not found
        time_str = config.get('last_sync')

    if time_str:
        try:
            # Ensure the string is in the correct ISO format with timezone
            if isinstance(time_str, str):
                if not time_str.endswith('Z') and '+' not in time_str and '-' not in time_str[10:]:
                     # Attempt to fix common issue: missing timezone info, assume UTC
                    dt_obj = datetime.fromisoformat(time_str)
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    return dt_obj
                return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        except ValueError:
            logger.warning(f"Invalid date format for '{sync_key}': {time_str}. Ignoring.")
            return None
    return None

def get_sync_days_setting() -> int:
    """
    Get the number of days to look back for Notion sync.
    
    Returns:
        Number of days as integer
    """
    config = get_config()
    return config.get('last_days', 5)  # Default to 5 days

def set_sync_days_setting(days: int) -> int:
    """
    Set the number of days to look back for Notion sync.
    
    Args:
        days: Number of days to look back
        
    Returns:
        The days value that was set
    """
    if days <= 0:
        days = 1  # Ensure a minimum of 1 day
    
    update_config({'last_days': days})
    
    return days

def get_chat_days_setting() -> int:
    """
    Get the number of days to look back for chat context.
    
    Returns:
        Number of days as integer
    """
    config = get_config()
    return config.get('chat_context_days', 5)  # Default to 5 days

def set_chat_days_setting(days: int) -> int:
    """
    Set the number of days to look back for chat context.
    
    Args:
        days: Number of days to look back
        
    Returns:
        The days value that was set
    """
    if days <= 0:
        days = 1  # Ensure a minimum of 1 day
    
    update_config({'chat_context_days': days})
    
    return days

def get_chat_config() -> Dict[str, Any]:
    """
    Load chat configuration from promaia.config.json.
    
    Returns:
        Chat configuration dictionary with defaults
    """
    try:
        if os.path.exists(_main_config_file()):
            with open(_main_config_file(), 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get("global", {}).get("chat", {})
    except Exception as e:
        logger.warning(f"Error loading chat config from {MAIN_CONFIG_FILE}: {e}")
    
    # Return default chat config
    return {
        "default_days": 7,
        "default_sources": ["journal"],
        "enable_multi_source_by_default": False
    }

def get_chat_default_sources() -> List[str]:
    """
    Get the default sources for chat mode.
    
    Returns:
        List of default source nicknames (e.g., ["journal"])
    """
    chat_config = get_chat_config()
    return chat_config.get("default_sources", ["journal"])

def get_chat_default_days() -> int:
    """
    Get the default number of days for chat mode from main config.
    
    Returns:
        Number of days as integer
    """
    chat_config = get_chat_config()
    return chat_config.get("default_days", 7)

def is_multi_source_default_enabled() -> bool:
    """
    Check if multi-source mode should be enabled by default.
    
    Returns:
        True if multi-source should be default, False for simple journal-only default
    """
    chat_config = get_chat_config()
    return chat_config.get("enable_multi_source_by_default", False) 