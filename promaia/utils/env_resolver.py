"""
Environment variable resolution utility for configuration files.

This module provides functions to resolve environment variable references 
in configuration data, supporting ${VAR_NAME} syntax.
"""
import os
import re
import logging
from typing import Any, Dict, Union

logger = logging.getLogger(__name__)

def resolve_env_variables(data: Union[str, Dict, list, Any]) -> Any:
    """
    Recursively resolve environment variables in configuration data.
    
    Supports ${VAR_NAME} syntax and substitutes with environment variable values.
    
    Args:
        data: Configuration data (string, dict, list, or other)
        
    Returns:
        Data with environment variables resolved
    """
    if isinstance(data, str):
        return resolve_env_string(data)
    elif isinstance(data, dict):
        return {key: resolve_env_variables(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [resolve_env_variables(item) for item in data]
    else:
        return data

def resolve_env_string(text: str) -> str:
    """
    Resolve environment variables in a string.
    
    Supports patterns like:
    - ${VAR_NAME}
    - ${VAR_NAME:-default_value}
    
    Args:
        text: String that may contain environment variable references
        
    Returns:
        String with environment variables resolved
    """
    # Pattern to match ${VAR_NAME} or ${VAR_NAME:-default}
    pattern = r'\$\{([^}]+)\}'
    
    def replace_var(match):
        var_expr = match.group(1)
        
        # Check for default value syntax (VAR_NAME:-default)
        if ':-' in var_expr:
            var_name, default_value = var_expr.split(':-', 1)
            return os.getenv(var_name.strip(), default_value.strip())
        else:
            var_name = var_expr.strip()
            env_value = os.getenv(var_name)
            
            if env_value is None:
                logger.warning(f"Environment variable '{var_name}' not found, keeping original reference")
                return match.group(0)  # Return original ${VAR_NAME} if not found
            
            return env_value
    
    return re.sub(pattern, replace_var, text)

def validate_required_env_vars(config_data: Dict[str, Any], required_vars: list = None) -> list:
    """
    Validate that required environment variables are available.
    
    Args:
        config_data: Configuration data to scan for env var references
        required_vars: Optional list of required environment variables
        
    Returns:
        List of missing environment variable names
    """
    if required_vars is None:
        required_vars = []
    
    # Extract env var references from config
    referenced_vars = extract_env_var_references(config_data)
    
    # Combine with explicitly required vars
    all_required = set(required_vars + referenced_vars)
    
    # Check which ones are missing
    missing_vars = []
    for var_name in all_required:
        if os.getenv(var_name) is None:
            missing_vars.append(var_name)
    
    return missing_vars

def extract_env_var_references(data: Any) -> list:
    """
    Extract all environment variable references from configuration data.
    
    Args:
        data: Configuration data to scan
        
    Returns:
        List of environment variable names referenced in the data
    """
    var_names = []
    
    if isinstance(data, str):
        pattern = r'\$\{([^}:]+)(?::-[^}]*)?\}'
        matches = re.findall(pattern, data)
        var_names.extend([match.strip() for match in matches])
    elif isinstance(data, dict):
        for value in data.values():
            var_names.extend(extract_env_var_references(value))
    elif isinstance(data, list):
        for item in data:
            var_names.extend(extract_env_var_references(item))
    
    return list(set(var_names))  # Remove duplicates

def load_env_file(env_file_path: str = ".env") -> bool:
    """
    Load environment variables from .env file.
    
    Args:
        env_file_path: Path to .env file
        
    Returns:
        True if file was loaded successfully, False otherwise
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file_path)
        logger.debug(f"Loaded environment variables from {env_file_path}")
        return True
    except ImportError:
        logger.warning("python-dotenv not available, cannot load .env file")
        return False
    except Exception as e:
        logger.warning(f"Could not load environment variables from {env_file_path}: {e}")
        return False 