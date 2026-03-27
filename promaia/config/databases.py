"""
Database configuration management for Maia.

This module provides a unified configuration system for managing multiple databases
and their sync settings, filters, and properties.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
from pathlib import Path

from promaia.utils.env_resolver import resolve_env_variables, load_env_file

logger = logging.getLogger(__name__)

def _default_config_file():
    from promaia.utils.env_writer import get_config_path
    return str(get_config_path())

class DatabaseConfig:
    """Configuration for a single database."""
    
    def __init__(self, name: str, config_data: Dict[str, Any]):
        self.name = name
        self.source_type = config_data.get("source_type", "notion")
        self.database_id = config_data["database_id"]
        self.nickname = config_data.get("nickname", name)
        self.description = config_data.get("description", "")
        
        # Workspace assignment
        self.workspace = config_data.get("workspace", "koii")

        # Workspace scope: "single" (workspace-specific) or "all" (cross-workspace)
        # "all" makes content accessible from any workspace query (immutable content)
        self.workspace_scope = config_data.get("workspace_scope", "single")

        # Sync settings
        self.sync_enabled = config_data.get("sync_enabled", True)
        self.include_properties = config_data.get("include_properties", True)
        self.sync_frequency = config_data.get("sync_frequency", "daily")
        self.default_days = config_data.get("default_days", 7)
        self.default_include = config_data.get("default_include", False)
        self.browser_include = config_data.get("browser_include", True)  # Whether to show in browser UI
        self.last_sync_time = config_data.get("last_sync_time", None)
        
        # Filtering settings
        self.filters = config_data.get("filters", {}) or {}
        # property_filters now supports both name-based and ID-based formats:
        # - Name-based: {"Team": "Consumer Product"} or {"Team": ["Consumer Product", "Engineering"]}
        # - ID-based: {"prop_abc123": "opt_xyz789"} or {"prop_abc123": ["opt_xyz789", "opt_def456"]}
        self.property_filters = config_data.get("property_filters", {}) or {}
        self.date_filters = config_data.get("date_filters", {}) or {}

        # Storage settings - new generalized structure: data/{app}/{workspace}/
        source_type = config_data.get("source_type", "notion")
        if source_type == "gmail":
            # For Gmail, use data/md/gmail/{workspace}/ structure
            default_md_dir = f"data/md/gmail/{self.workspace}"
        elif source_type == "discord":
            # For Discord, use data/md/discord/{workspace}/{nickname} structure to separate different servers
            default_md_dir = f"data/md/discord/{self.workspace}/{self.nickname}"
        elif source_type == "slack":
            # For Slack, use data/md/slack/{workspace}/{nickname} structure
            default_md_dir = f"data/md/slack/{self.workspace}/{self.nickname}"
        else:
            # For other sources (Notion), use data/md/notion/{workspace}/{nickname}/ per database
            default_md_dir = f"data/md/notion/{self.workspace}/{self.nickname}"

        self.markdown_directory = config_data.get("markdown_directory", default_md_dir)
        
        # Backward compatibility: keep output_directory for legacy systems
        self.output_directory = config_data.get("output_directory", self.markdown_directory)
            
        self.primary_format = "markdown"  # Always markdown
        self.save_markdown = True  # Always save markdown
        
        # Subpage sync settings
        self.sync_subpages = config_data.get("sync_subpages", False)

        # Property mapping - defensive: ensure never None
        self.property_mapping = config_data.get("property_mapping", {}) or {}
        self.required_properties = config_data.get("required_properties", []) or []
        self.excluded_properties = config_data.get("excluded_properties", []) or []

        # Authentication (for future extensibility) - defensive: ensure never None
        self.auth_config = config_data.get("auth", {}) or {}

        # Google account email (for google_sheets source type)
        self.google_account = config_data.get("google_account")
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert database config to dictionary."""
        result = {
            "source_type": self.source_type,
            "database_id": self.database_id,
            "nickname": self.nickname,
            "description": self.description,
            "workspace": self.workspace,
            "workspace_scope": self.workspace_scope,
            "sync_enabled": self.sync_enabled,
            "include_properties": self.include_properties,
            "sync_frequency": self.sync_frequency,
            "default_days": self.default_days,
            "default_include": self.default_include,
            "browser_include": self.browser_include,
            "last_sync_time": self.last_sync_time,
            "filters": self.filters or {},
            "property_filters": self.property_filters or {},
            "date_filters": self.date_filters or {},
            "markdown_directory": self.markdown_directory,
            "primary_format": self.primary_format,
            "save_markdown": self.save_markdown,
            "sync_subpages": self.sync_subpages,
            "property_mapping": self.property_mapping,
            "required_properties": self.required_properties,
            "excluded_properties": self.excluded_properties,
            "auth": self.auth_config
        }
        
        # Include optional fields only when set
        if self.google_account:
            result["google_account"] = self.google_account
        if self.output_directory != self.markdown_directory:
            result["output_directory"] = self.output_directory
        
        return result
        
    def get_qualified_name(self) -> str:
        """Get the workspace-qualified database name."""
        if self.workspace == "koii":
            return self.nickname
        else:
            # Check if nickname already starts with workspace prefix
            if self.nickname.startswith(f"{self.workspace}."):
                return self.nickname
            else:
                return f"{self.workspace}.{self.nickname}"
    
    def get_stable_identifier(self) -> str:
        """Get a stable identifier for this database that doesn't change with nickname updates."""
        if self.source_type == "discord" and self.database_id:
            return f"discord_{self.database_id}"
        return f"{self.workspace}_{self.name}"
    
    def get_discord_server_id(self) -> Optional[str]:
        """Get the Discord server ID for this database."""
        if self.source_type == "discord":
            return self.database_id
        return None

class DatabaseManager:
    """Manages all database configurations."""
    
    def __init__(self, config_file: str = None):
        if config_file is None:
            config_file = _default_config_file()
        self.config_file = config_file
        self.global_settings = {}
        self.databases: Dict[str, DatabaseConfig] = {}
        
        # Import workspace manager
        from promaia.config.workspaces import get_workspace_manager
        self.workspace_manager = get_workspace_manager(config_file)
        
        # Initialize configuration
        if not os.path.exists(config_file):
            self.create_default_config()
        else:
            self.load_config()
    
    def load_config(self):
        """Load configuration from file."""
        if not os.path.exists(self.config_file):
            logger.info(f"Configuration file {self.config_file} not found. Creating default configuration.")
            self.create_default_config()
            return
        
        try:
            # Load environment variables first
            load_env_file()
            
            with open(self.config_file, 'r') as f:
                config_data = json.load(f)
            
            # Resolve environment variables in configuration
            config_data = resolve_env_variables(config_data)
            
            # Load global settings
            self.global_settings = config_data.get("global", {})
            
            # Load database configurations
            databases_config = config_data.get("databases", {})
            for name, db_config in databases_config.items():
                self.databases[name] = DatabaseConfig(name, db_config)
                
            logger.info(f"Loaded configuration for {len(self.databases)} databases")
            
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            self.create_default_config()
    
    def save_config(self):
        """Update config on disk — never add or remove databases.

        Reads the current file from disk and only updates database entries that
        are ALREADY present on disk.  It never adds entries from memory that are
        absent on disk (avoids restoring deleted databases from stale in-memory
        state), and never removes entries that are on disk but absent from memory
        (deletions must go through remove_database()).

        For a completely fresh config file (no file exists yet), it falls back to
        writing all in-memory databases so that create_default_config() works.

        For adding a new database use _write_database_to_disk(); for deletions use
        remove_database(); for single-field patches use save_database_field().
        """
        is_fresh = not os.path.exists(self.config_file)
        try:
            with open(self.config_file, 'r') as f:
                file_data = json.load(f)
        except Exception:
            file_data = {}
            is_fresh = True

        file_data["global"] = self.global_settings

        file_databases = file_data.get("databases", {})
        if is_fresh:
            # No file yet — write everything so create_default_config() works.
            for name, db in self.databases.items():
                file_databases[name] = db.to_dict()
        else:
            # Existing file — only update entries already on disk.
            # A stale long-running service calling save_config() will NOT restore
            # a database that was removed from disk by remove_database().
            for name in list(file_databases.keys()):
                if name in self.databases:
                    file_databases[name] = self.databases[name].to_dict()
        file_data["databases"] = file_databases

        try:
            with open(self.config_file, 'w') as f:
                json.dump(file_data, f, indent=2)
            logger.debug(f"Configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
    
    def save_database_field(self, db_config: 'DatabaseConfig', field: str):
        """Update a single field for one database in the config file.

        Reads the current file from disk, patches only the specified field for
        the given database, and writes back.  This avoids overwriting changes
        made by concurrent processes (e.g. channel edits from another session).
        """
        try:
            with open(self.config_file, 'r') as f:
                file_data = json.load(f)
        except Exception as e:
            logger.warning(f"save_database_field: could not read config, falling back to full save: {e}")
            self.save_config()
            return

        databases = file_data.get("databases", {})

        # Try to find the database key on disk — it may be stored under
        # db_config.name OR the qualified name (workspace.nickname).
        db_key = db_config.name
        db_section = databases.get(db_key)
        if db_section is None:
            qualified = db_config.get_qualified_name()
            if qualified in databases:
                db_key = qualified
                db_section = databases[db_key]

        if db_section is None:
            logger.debug(f"save_database_field: database '{db_config.name}' not found on disk, falling back to full save")
            self.save_config()
            return

        value = getattr(db_config, field)
        # For nested dict fields, convert DatabaseConfig attribute to serializable form
        if field == "property_filters":
            db_section[field] = dict(value) if value else {}
        else:
            db_section[field] = value

        try:
            with open(self.config_file, 'w') as f:
                json.dump(file_data, f, indent=2)
            logger.debug(f"Patched {field} for database '{db_key}'")
        except Exception as e:
            logger.error(f"Error patching config field: {e}")

    def _write_database_to_disk(self, qualified_name: str):
        """Add or overwrite a single database entry in the config file.

        Used by add_database() so that new databases are written atomically
        without touching any other database entry.
        """
        try:
            with open(self.config_file, 'r') as f:
                file_data = json.load(f)
        except Exception:
            file_data = {}

        file_databases = file_data.get("databases", {})
        file_databases[qualified_name] = self.databases[qualified_name].to_dict()
        file_data["databases"] = file_databases

        try:
            with open(self.config_file, 'w') as f:
                json.dump(file_data, f, indent=2)
            logger.debug(f"Wrote database '{qualified_name}' to disk")
        except Exception as e:
            logger.error(f"Error writing database '{qualified_name}' to disk: {e}")

    def create_default_config(self):
        """Create a default configuration file."""
        default_config = {
            "global": {
            "default_sync_days": 7,
            "default_output_directory": "data",
            "markdown_base_directory": "data",  # Updated to new structure
                "json_base_directory": "data/json",
                "json_registry_db": "data/maia_content.db",
                "registry_db": "data/hybrid_metadata.db",
                "vector_db_enabled": False,
                "vector_db_type": "chroma",
                "vector_db_path": "vector_db",
                "storage_format": "json",
            "enable_ai_editing": True,
                "ai_edit_safety_mode": True
            },
            "databases": {}
        }
        
        # Migrate existing environment variables to new config
        self._migrate_from_env_vars()
        
        self.save_config()
    
    def get_database_by_qualified_name(self, qualified_name: str) -> Optional[DatabaseConfig]:
        """Get a database configuration by its qualified name."""
        # First, try exact matches
        for db in self.databases.values():
            # Check against the key in the config (e.g., "trass.journal")
            # and the generated qualified name (e.g., "trass.journal")
            if db.name == qualified_name or db.get_qualified_name() == qualified_name:
                return db
        
        # If no exact match, try to resolve workspace.nickname format
        if '.' in qualified_name:
            workspace, nickname = qualified_name.rsplit('.', 1)
            for db in self.databases.values():
                if db.workspace == workspace and db.nickname == nickname:
                    return db
        
        return None
    
    def get_database_by_server_id(self, server_id: str) -> Optional[DatabaseConfig]:
        """Get a Discord database configuration by its server ID."""
        for db in self.databases.values():
            if db.source_type == "discord" and db.database_id == server_id:
                return db
        return None
    
    def find_database_by_legacy_name(self, legacy_name: str) -> Optional[DatabaseConfig]:
        """Find a database that might have been renamed, using multiple lookup strategies."""
        # First try exact match
        result = self.get_database_by_qualified_name(legacy_name)
        if result:
            return result
        
        # For Discord databases, try to find by checking if any database points to same directory
        # This helps when nicknames change but files are still in the same location
        for db in self.databases.values():
            if db.source_type == "discord":
                # Check if the legacy name could be an old nickname for this database
                if f"{db.workspace}.{legacy_name}" == f"{db.workspace}.{db.nickname}":
                    return db
                # Check if legacy name matches the current qualified name pattern
                if legacy_name in [db.name, db.nickname, f"{db.workspace}.{db.nickname}"]:
                    return db
        
        return None
    
    def _migrate_from_env_vars(self):
        """Migrate existing environment variable configurations."""
        # Journal database
        journal_db_id = os.getenv("NOTION_JOURNAL_DATABASE_ID")
        if journal_db_id:
            self.databases["journal"] = DatabaseConfig("journal", {
                "source_type": "notion",
                "database_id": journal_db_id,
                "nickname": "koii_journal",
                "description": "Personal journal entries",
                "output_directory": "data/koii/md/journal",
                "default_days": 7,
                "include_properties": False,
                "save_markdown": True,
                "property_filters": {},
                "date_filters": {
                    "property": "Date",
                    "type": "date"
                }
            })
        
        # CMS database
        cms_db_id = os.getenv("NOTION_CMS_DATABASE_ID")
        if cms_db_id:
            self.databases["cms"] = DatabaseConfig("cms", {
                "source_type": "notion",
                "database_id": cms_db_id,
                "nickname": "koii_cms",
                "description": "Content management system",
                "output_directory": "data/koii/md/cms",
                "default_days": 30,
                "include_properties": False,
                "save_markdown": True,
                "property_filters": {
                    "Blog Status": ["To sync", "Update on sync", "Don't sync", "Live"]
                }
            })
        
        logger.info("Migrated existing environment variables to new configuration")
    
    def add_database(self, name: str, config_data: Dict[str, Any], workspace: str = None) -> bool:
        """Add a new database configuration."""
        # Set workspace if not specified
        if workspace is None:
            workspace = self.workspace_manager.get_default_workspace() or "default"
        
        # Check if name is already qualified (contains workspace prefix)
        if '.' in name:
            # Split to get workspace and database name
            name_workspace, name_part = name.rsplit('.', 1)
            if name_workspace == workspace:
                # Already properly qualified, use as-is
                qualified_name = name
                database_name = name_part
            else:
                # Different workspace in name vs parameter - use parameter workspace
                qualified_name = f"{workspace}.{name_part}" if workspace != "default" else name_part
                database_name = name_part
        else:
            # Simple name, add workspace prefix if needed
            qualified_name = f"{workspace}.{name}" if workspace != "default" else name
            database_name = name
        
        # Add workspace to config data
        config_data["workspace"] = workspace
        
        if qualified_name in self.databases:
            logger.warning(f"Database '{qualified_name}' already exists")
            return False
        
        self.databases[qualified_name] = DatabaseConfig(database_name, config_data)
        self._write_database_to_disk(qualified_name)

        logger.info(f"Added database '{qualified_name}' to workspace '{workspace}'")
        return True
    
    def get_database(self, name: str, workspace: str = None) -> Optional[DatabaseConfig]:
        """Get database configuration by name, with workspace-aware lookup."""
        # If workspace specified, try workspace.name format first
        if workspace:
            qualified_name = f"{workspace}.{name}"
            if qualified_name in self.databases:
                return self.databases[qualified_name]
        
        # Try exact match if no workspace specified or qualified name not found
        if name in self.databases:
            db = self.databases[name]
            # If workspace specified, ensure it matches
            if workspace is None or db.workspace == workspace:
                return db
        
        # Try searching across all workspaces for the nickname as fallback
        for db_name, db_config in self.databases.items():
            if db_config.nickname == name:
                # If workspace specified, ensure it matches
                if workspace is None or db_config.workspace == workspace:
                    return db_config
        
        return None
    
    def list_databases(self, workspace: str = None, include_archived: bool = False) -> List[str]:
        """
        List database names, optionally filtered by workspace.

        Args:
            workspace: Filter by workspace name (optional)
            include_archived: If True, include databases from archived workspaces. Default False.

        Returns:
            List of database names
        """
        # Import here to avoid circular dependency
        from promaia.config.workspaces import get_workspace_manager

        workspace_manager = get_workspace_manager()

        if workspace is None:
            # Filter all databases by archived status
            if include_archived:
                return list(self.databases.keys())

            return [
                name for name, config in self.databases.items()
                if not self._is_workspace_archived(workspace_manager, config.workspace)
            ]

        # Check if the specified workspace is archived
        if not include_archived and self._is_workspace_archived(workspace_manager, workspace):
            return []

        return [
            name for name, config in self.databases.items()
            if config.workspace == workspace
        ]

    def list_databases_by_workspace(self, include_archived: bool = False) -> Dict[str, List[str]]:
        """
        List databases grouped by workspace.

        Args:
            include_archived: If True, include databases from archived workspaces. Default False.

        Returns:
            Dictionary mapping workspace names to lists of database names
        """
        # Import here to avoid circular dependency
        from promaia.config.workspaces import get_workspace_manager

        workspace_manager = get_workspace_manager()
        result = {}

        for name, config in self.databases.items():
            workspace = config.workspace

            # Skip archived workspaces unless explicitly included
            if not include_archived and self._is_workspace_archived(workspace_manager, workspace):
                continue

            if workspace not in result:
                result[workspace] = []
            result[workspace].append(name)

        return result

    def _is_workspace_archived(self, workspace_manager, workspace_name: str) -> bool:
        """Check if a workspace is archived."""
        workspace = workspace_manager.get_workspace(workspace_name)
        return workspace.archived if workspace else False
    
    def remove_database(self, name: str, workspace: str = None) -> bool:
        """Remove a database configuration."""
        db_config = self.get_database(name, workspace)
        if not db_config:
            logger.warning(f"Database '{name}' not found")
            return False
        
        # Find the actual key in self.databases
        key_to_remove = None
        for key, config in self.databases.items():
            if config == db_config:
                key_to_remove = key
                break
        
        if key_to_remove:
            del self.databases[key_to_remove]
            # Patch the on-disk config directly for an atomic delete.
            try:
                with open(self.config_file, 'r') as f:
                    file_data = json.load(f)
                file_databases = file_data.get("databases", {})
                file_databases.pop(key_to_remove, None)
                file_data["databases"] = file_databases
                with open(self.config_file, 'w') as f:
                    json.dump(file_data, f, indent=2)
            except Exception as e:
                logger.warning(f"remove_database: direct patch failed, falling back to save_config: {e}")
                self.save_config()
            logger.info(f"Removed database '{name}' from workspace '{db_config.workspace}'")
            return True
        
        return False
    
    def get_workspace_databases(self, workspace: str, include_archived: bool = False) -> List[DatabaseConfig]:
        """
        Get all databases for a specific workspace.

        Args:
            workspace: Workspace name
            include_archived: If True, include databases from archived workspaces. Default False.

        Returns:
            List of database configurations
        """
        # Import here to avoid circular dependency
        from promaia.config.workspaces import get_workspace_manager

        # Check if workspace is archived (unless explicitly including archived)
        if not include_archived:
            workspace_manager = get_workspace_manager()
            workspace_obj = workspace_manager.get_workspace(workspace)
            if workspace_obj and workspace_obj.archived:
                # Return empty list for archived workspaces by default
                return []

        return [
            config for config in self.databases.values()
            if config.workspace == workspace
        ]

    def get_workspace_agnostic_databases(self) -> List[DatabaseConfig]:
        """Get all databases with workspace_scope='all' (accessible from any workspace)."""
        return [
            config for config in self.databases.values()
            if config.workspace_scope == "all"
        ]

# Global database manager instance
_db_manager = None

def get_database_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager

def get_database_config(name: str, workspace: str = None) -> Optional[DatabaseConfig]:
    """Get database configuration by name."""
    manager = get_database_manager()
    return manager.get_database(name, workspace)

def list_databases(workspace: str = None) -> List[str]:
    """List all configured databases."""
    return get_database_manager().list_databases(workspace)

def add_database(name: str, config: Dict[str, Any]) -> DatabaseConfig:
    """Add a new database configuration."""
    return get_database_manager().add_database(name, config) 