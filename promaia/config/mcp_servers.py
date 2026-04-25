"""
MCP (Model Context Protocol) server configuration and management.

This module handles the configuration, discovery, and management of MCP servers
that can be integrated into Promaia chat sessions.
"""
import json
import os
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def _resolve_default_config_path() -> str:
    """Resolve the default `mcp_servers.json` path.

    Uses the shared `_find_mcp_servers_json()` search so that the manager,
    the CLI, and the agent loader all agree on a single file. Falls back to
    `<data_dir>/mcp_servers.json` when no existing file is located, so the
    manager always has a stable place to save to if the caller later adds
    servers.
    """
    try:
        from promaia.agents.mcp_loader import _find_mcp_servers_json

        found = _find_mcp_servers_json()
        if found is not None:
            return str(found)
    except Exception:
        pass

    try:
        from promaia.utils.env_writer import get_data_dir

        return str(get_data_dir() / "mcp_servers.json")
    except Exception:
        return "mcp_servers.json"


@dataclass
class McpServerConfig:
    """Configuration for an MCP server."""
    name: str
    description: str
    command: List[str]  # Command to start the server (stdio only)
    args: List[str] = None  # Additional arguments (stdio only)
    env: Dict[str, str] = None  # Environment variables
    working_dir: str = None  # Working directory (stdio only)
    timeout: int = 30  # Connection timeout in seconds
    enabled: bool = True
    transport: str = "stdio"  # "stdio" or "streamable_http"
    url: Optional[str] = None  # URL for streamable_http transport

    def __post_init__(self):
        """Initialize optional fields."""
        if self.args is None:
            self.args = []
        if self.env is None:
            self.env = {}
    
    def get_resolved_env(self) -> Dict[str, str]:
        """Get environment variables with ${VAR_NAME} substitution resolved.
        
        Returns:
            Dictionary with environment variables resolved
        """
        resolved_env = {}
        
        for key, value in self.env.items():
            resolved_value = self._substitute_env_vars(value)
            resolved_env[key] = resolved_value
            
        return resolved_env
    
    def _substitute_env_vars(self, value: str) -> str:
        """Substitute environment variables in the format ${VAR_NAME}.
        
        Args:
            value: String that may contain ${VAR_NAME} patterns
            
        Returns:
            String with environment variables substituted
        """
        def replace_var(match):
            var_name = match.group(1)
            env_value = os.getenv(var_name)
            if env_value is None:
                logger.warning(f"Environment variable {var_name} not found, leaving as-is")
                return match.group(0)  # Return original ${VAR_NAME} if not found
            return env_value
        
        # Replace ${VAR_NAME} patterns with environment variable values
        return re.sub(r'\$\{([^}]+)\}', replace_var, value)

class McpServerManager:
    """Manages MCP server configurations and connections."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize the MCP server manager.

        Args:
            config_path: Path to the MCP servers configuration file. When None,
                resolves via `promaia.agents.mcp_loader._find_mcp_servers_json()`
                (same search used by the rest of the stack) and falls back to
                `<data_dir>/mcp_servers.json` if nothing is found. The default
                is deliberately NOT the relative string "mcp_servers.json" —
                that caused phantom default configs to be written into CWD.
        """
        if config_path is None:
            config_path = _resolve_default_config_path()
        self.config_path = config_path
        self.servers: Dict[str, McpServerConfig] = {}
        self.load_config()

    def load_config(self) -> None:
        """Load MCP server configurations from file."""
        if not os.path.exists(self.config_path):
            # No file: start with an empty registry. We deliberately do NOT
            # auto-create a defaults file here — doing so in CWD poisoned the
            # mcp_servers.json search order (see _find_mcp_servers_json) and
            # made `maia mcp remove` / agent internals show stale
            # filesystem/git/sqlite entries that nobody configured.
            logger.debug(
                "MCP config file not found at %s; starting with empty server list",
                self.config_path,
            )
            self.servers = {}
            return
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            self.servers = {}
            for server_name, server_data in config_data.get('servers', {}).items():
                self.servers[server_name] = McpServerConfig(
                    name=server_name,
                    description=server_data.get('description', ''),
                    command=server_data.get('command', []),
                    args=server_data.get('args', []),
                    env=server_data.get('env', {}),
                    working_dir=server_data.get('working_dir'),
                    timeout=server_data.get('timeout', 30),
                    enabled=server_data.get('enabled', True),
                    transport=server_data.get('transport', 'stdio'),
                    url=server_data.get('url'),
                )
            
            logger.info(f"Loaded {len(self.servers)} MCP server configurations")
            
        except Exception as e:
            logger.error(f"Error loading MCP config from {self.config_path}: {e}")
            self.servers = {}

    def get_server(self, name: str) -> Optional[McpServerConfig]:
        """Get a specific MCP server configuration.
        
        Args:
            name: Name of the MCP server
            
        Returns:
            McpServerConfig if found, None otherwise
        """
        return self.servers.get(name)
    
    def list_servers(self, enabled_only: bool = False) -> List[str]:
        """List available MCP server names.
        
        Args:
            enabled_only: If True, only return enabled servers
            
        Returns:
            List of server names
        """
        if enabled_only:
            return [name for name, config in self.servers.items() if config.enabled]
        return list(self.servers.keys())
    
    def get_enabled_servers(self) -> Dict[str, McpServerConfig]:
        """Get all enabled MCP servers.
        
        Returns:
            Dictionary of enabled server configurations
        """
        return {name: config for name, config in self.servers.items() if config.enabled}
    
    def validate_server_config(self, config: McpServerConfig) -> List[str]:
        """Validate an MCP server configuration.
        
        Args:
            config: Server configuration to validate
            
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if not config.name:
            errors.append("Server name is required")

        if config.transport not in ("stdio", "streamable_http"):
            errors.append(f"Unknown transport '{config.transport}' (expected 'stdio' or 'streamable_http')")

        if config.transport == "streamable_http":
            if not config.url:
                errors.append("URL is required for streamable_http transport")
        else:
            if not config.command:
                errors.append("Server command is required for stdio transport")

            import shutil
            if config.command and not shutil.which(config.command[0]):
                errors.append(f"Command '{config.command[0]}' not found in PATH")

            if config.working_dir and not os.path.isdir(config.working_dir):
                errors.append(f"Working directory '{config.working_dir}' does not exist")

        return errors

    def add_server(self, name: str, config_dict: dict) -> McpServerConfig:
        """Add a new server from a config dict and persist to disk."""
        server = McpServerConfig(
            name=name,
            description=config_dict.get('description', ''),
            command=config_dict.get('command', []),
            args=config_dict.get('args', []),
            env=config_dict.get('env', {}),
            working_dir=config_dict.get('working_dir'),
            timeout=config_dict.get('timeout', 30),
            enabled=config_dict.get('enabled', True),
            transport=config_dict.get('transport', 'stdio'),
            url=config_dict.get('url'),
        )
        self.servers[name] = server
        self._save()
        return server

    def remove_server(self, name: str) -> bool:
        """Remove a server by name and persist to disk.  Returns True if removed."""
        if name not in self.servers:
            return False
        del self.servers[name]
        self._save()
        return True

    def _save(self) -> None:
        """Write current server configs back to disk."""
        data = {"servers": {}}
        for name, cfg in self.servers.items():
            entry = {
                "description": cfg.description,
                "enabled": cfg.enabled,
                "transport": cfg.transport,
            }
            if cfg.transport == "streamable_http":
                entry["url"] = cfg.url
            else:
                entry["command"] = cfg.command
                entry["args"] = cfg.args
                if cfg.working_dir:
                    entry["working_dir"] = cfg.working_dir
            if cfg.env:
                entry["env"] = cfg.env
            if cfg.timeout != 30:
                entry["timeout"] = cfg.timeout
            data["servers"][name] = entry

        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {len(self.servers)} MCP server configs to {self.config_path}")

# Global MCP server manager instance
_mcp_manager = None

def get_mcp_manager() -> McpServerManager:
    """Get the global MCP server manager instance."""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = McpServerManager()
    return _mcp_manager 