"""
Workspace configuration management for Maia.

This module provides support for managing multiple Notion workspaces,
allowing users to connect and segregate data from different workspaces.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from promaia.utils.env_resolver import resolve_env_variables, load_env_file

logger = logging.getLogger(__name__)

class WorkspaceConfig:
    """Configuration for a single workspace."""

    def __init__(self, name: str, config_data: Dict[str, Any]):
        self.name = name
        self.api_key = config_data.get("api_key")
        self.description = config_data.get("description", "")
        self.enabled = config_data.get("enabled", True)
        self.created_at = config_data.get("created_at", datetime.now().isoformat())
        self.archived = config_data.get("archived", False)
        self.archived_at = config_data.get("archived_at")
        self.archived_reason = config_data.get("archived_reason", "")
        self.agents_database_id = config_data.get("agents_database_id")
        self.agents_page_id = config_data.get("agents_page_id")  # The parent Promaia Agents page
        self.promaia_page_id = config_data.get("promaia_page_id")  # The main Promaia page (template root)
        self.main_prompt_page_id = config_data.get("main_prompt_page_id")  # The Main prompt subpage
        self.prompts_database_id = config_data.get("prompts_database_id")  # The Prompts inline database
        self.mail_enabled = config_data.get("mail_enabled", False)  # Whether maia mail daemon processes this workspace

    def to_dict(self) -> Dict[str, Any]:
        """Convert workspace config to dictionary."""
        result = {
            "api_key": self.api_key,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "archived": self.archived
        }

        # Only include archive metadata if archived
        if self.archived:
            if self.archived_at:
                result["archived_at"] = self.archived_at
            if self.archived_reason:
                result["archived_reason"] = self.archived_reason

        # Include agents_database_id if set
        if self.agents_database_id:
            result["agents_database_id"] = self.agents_database_id

        # Include agents_page_id if set
        if self.agents_page_id:
            result["agents_page_id"] = self.agents_page_id

        # Include promaia_page_id if set
        if self.promaia_page_id:
            result["promaia_page_id"] = self.promaia_page_id

        # Include main_prompt_page_id if set
        if self.main_prompt_page_id:
            result["main_prompt_page_id"] = self.main_prompt_page_id

        # Include prompts_database_id if set
        if self.prompts_database_id:
            result["prompts_database_id"] = self.prompts_database_id

        # Only include mail_enabled if explicitly enabled (default is False)
        if self.mail_enabled:
            result["mail_enabled"] = True

        return result

class WorkspaceManager:
    """Manages workspace configurations and operations."""
    
    def __init__(self, config_file: str = None):
        if config_file is None:
            from promaia.utils.env_writer import get_config_path
            config_file = str(get_config_path())
        self.config_file = config_file
        self.workspaces: Dict[str, WorkspaceConfig] = {}
        self.default_workspace = None
        self.load_config()
    
    def load_config(self):
        """Load workspace configuration from file."""
        if os.path.exists(self.config_file):
            try:
                # Load environment variables first
                load_env_file()
                
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                
                # Resolve environment variables in configuration
                config = resolve_env_variables(config)
                
                # Load workspaces from config
                workspaces_data = config.get("workspaces", {})
                for name, workspace_data in workspaces_data.items():
                    self.workspaces[name] = WorkspaceConfig(name, workspace_data)
                
                # Set default workspace
                self.default_workspace = config.get("default_workspace")
                
            except Exception as e:
                logger.error(f"Error loading workspace config: {e}")
    
    def save_config(self):
        """Save workspace configuration to file."""
        config = {}
        
        # Load existing config to preserve other sections
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load existing config for merging: {e}")
        
        # Update workspaces section
        config["workspaces"] = {
            name: workspace.to_dict() 
            for name, workspace in self.workspaces.items()
        }
        
        # Set default workspace
        if self.default_workspace:
            config["default_workspace"] = self.default_workspace
        
        # Write config file
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.debug(f"Saved workspace configuration to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving workspace config: {e}")
    
    def add_workspace(self, name: str, description: str = "") -> bool:
        """Add a new workspace.

        Credentials are stored separately via the auth module
        (``maia auth configure notion`` or ``--api-key`` on add).
        """
        if name in self.workspaces:
            logger.warning(f"Workspace '{name}' already exists")
            return False

        self.workspaces[name] = WorkspaceConfig(name, {
            "description": description,
            "enabled": True,
        })

        # Set as default if it's the first workspace
        if not self.default_workspace:
            self.default_workspace = name

        self.save_config()
        logger.info(f"Added workspace '{name}'")
        return True
    
    def remove_workspace(self, name: str) -> bool:
        """Remove a workspace."""
        if name not in self.workspaces:
            logger.warning(f"Workspace '{name}' not found")
            return False

        del self.workspaces[name]

        # Update default workspace if needed
        if self.default_workspace == name:
            self.default_workspace = next(iter(self.workspaces.keys())) if self.workspaces else None

        self.save_config()
        logger.info(f"Removed workspace '{name}'")
        return True

    def archive_workspace(self, name: str, reason: str = "") -> bool:
        """
        Archive a workspace.

        Args:
            name: Workspace name
            reason: Optional reason for archiving

        Returns:
            True if successful
        """
        if name not in self.workspaces:
            logger.warning(f"Workspace '{name}' not found")
            return False

        workspace = self.workspaces[name]
        if workspace.archived:
            logger.warning(f"Workspace '{name}' is already archived")
            return False

        workspace.archived = True
        workspace.archived_at = datetime.now().isoformat()
        workspace.archived_reason = reason

        # Update default workspace if this was the default
        if self.default_workspace == name:
            # Find first non-archived workspace
            active_workspaces = [
                ws_name for ws_name, ws in self.workspaces.items()
                if not ws.archived and ws_name != name
            ]
            self.default_workspace = active_workspaces[0] if active_workspaces else None

        self.save_config()
        logger.info(f"Archived workspace '{name}'")
        return True

    def unarchive_workspace(self, name: str) -> bool:
        """
        Unarchive a workspace.

        Args:
            name: Workspace name

        Returns:
            True if successful
        """
        if name not in self.workspaces:
            logger.warning(f"Workspace '{name}' not found")
            return False

        workspace = self.workspaces[name]
        if not workspace.archived:
            logger.warning(f"Workspace '{name}' is not archived")
            return False

        workspace.archived = False
        workspace.archived_at = None
        workspace.archived_reason = ""

        # Set as default if no default exists
        if not self.default_workspace:
            self.default_workspace = name

        self.save_config()
        logger.info(f"Unarchived workspace '{name}'")
        return True

    def get_workspace(self, name: str) -> Optional[WorkspaceConfig]:
        """Get workspace configuration by name."""
        return self.workspaces.get(name)
    
    def list_workspaces(self, include_archived: bool = False) -> List[str]:
        """
        List all workspace names.

        Args:
            include_archived: If True, include archived workspaces. Default False.

        Returns:
            List of workspace names
        """
        if include_archived:
            return list(self.workspaces.keys())

        # Filter out archived workspaces by default
        return [
            name for name, workspace in self.workspaces.items()
            if not workspace.archived
        ]
    
    def get_default_workspace(self) -> Optional[str]:
        """Get the default workspace name."""
        return self.default_workspace
    
    def set_default_workspace(self, name: str) -> bool:
        """Set the default workspace."""
        if name not in self.workspaces:
            logger.warning(f"Workspace '{name}' not found")
            return False
        
        self.default_workspace = name
        self.save_config()
        logger.info(f"Set default workspace to '{name}'")
        return True
    
    def get_api_key(self, workspace_name: str = None) -> Optional[str]:
        """Get credential for a workspace via the auth module."""
        if workspace_name is None:
            workspace_name = self.default_workspace

        if workspace_name is None:
            logger.warning("No workspace specified and no default workspace set")
            return None

        from promaia.auth import get_integration
        return get_integration("notion").get_notion_credentials(workspace_name)
    
    def validate_workspace(self, name: str, allow_archived: bool = False) -> bool:
        """
        Validate that a workspace is properly configured.

        Args:
            name: Workspace name
            allow_archived: If True, archived workspaces are valid. Default False.

        Returns:
            True if workspace is valid
        """
        workspace = self.get_workspace(name)
        if not workspace:
            return False

        # Credential check via the unified auth module (covers token JSON,
        # migrated env vars, and migrated workspace config keys).
        from promaia.auth import get_integration
        if not get_integration("notion").get_notion_credentials(name):
            logger.error(f"Workspace '{name}' has no Notion credentials configured")
            return False

        if not workspace.enabled:
            logger.warning(f"Workspace '{name}' is disabled")
            return False

        if workspace.archived and not allow_archived:
            logger.warning(f"Workspace '{name}' is archived")
            return False

        return True

# Global workspace manager instance
_workspace_manager = None

def get_workspace_manager(config_file: str = None) -> WorkspaceManager:
    """Get the global workspace manager instance."""
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager(config_file)
    return _workspace_manager

def get_workspace_config(name: str) -> Optional[WorkspaceConfig]:
    """Get workspace configuration by name."""
    manager = get_workspace_manager()
    return manager.get_workspace(name)

def get_workspace_api_key(workspace_name: str = None) -> Optional[str]:
    """Get API key for a workspace."""
    manager = get_workspace_manager()
    return manager.get_api_key(workspace_name) 

def get_default_workspace() -> Optional[str]:
    """Get the default workspace name."""
    manager = get_workspace_manager()
    return manager.get_default_workspace() 