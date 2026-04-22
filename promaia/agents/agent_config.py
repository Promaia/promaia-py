"""
Agent configuration management with JSON persistence.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from enum import Enum


class SourcePermission(Enum):
    """Permission levels for data sources"""
    READ_INITIAL = "read_initial"  # Deprecated: bulk pre-loading removed
    QUERY = "query"                # Can query dynamically at runtime
    WRITE = "write"                # Can write/modify via MCP tools


@dataclass
class SourceAccess:
    """Access configuration for a single source"""
    source_name: str               # e.g., "journal", "gmail", "tasks"
    initial_days: Optional[int]    # Days to load initially (None = all)
    permissions: List[SourcePermission]  # What agent can do
    max_query_days: Optional[int] = None  # Max days for query_source (safety limit)


# Catalog of built-in MCP servers and the tools they expose. Used by the
# `maia agent add` flow to offer granular per-tool permissions. External
# MCP servers (defined in mcp_servers.json) are not listed here — granular
# permissions for them can still be configured by editing the agent JSON.
BUILTIN_TOOL_CATALOG: Dict[str, Dict[str, str]] = {
    "promaia": {
        "query_sql": "Natural language → SQL keyword search",
        "query_vector": "Semantic search using embeddings",
        "query_source": "Load pages from a database with time filtering",
        "write_agent_journal": "Write to your private agent journal",
        "get_agent_messaging_config": "Read messaging configuration",
        "update_agent_messaging_config": "Update messaging configuration",
        "list_available_messaging_channels": "List Slack/Discord channels",
    },
    "gmail": {
        "send_message": "Send a new email message",
        "create_draft": "Create an email draft (not sent)",
        "reply_to_message": "Reply to an existing email",
        "draft_reply": "Draft a reply to an existing email (not sent)",
    },
    "calendar": {
        "create_event": "Create a calendar event",
        "update_event": "Update a calendar event",
        "delete_event": "Delete a calendar event",
    },
}


def builtin_tool_names(server: str) -> List[str]:
    """Return the catalog tool names for a built-in MCP server (or [])."""
    return list(BUILTIN_TOOL_CATALOG.get(server, {}).keys())


@dataclass
class AgentConfig:
    """Configuration for a scheduled agent."""

    name: str
    workspace: str
    databases: List[str]  # e.g., ["journal:7", "gmail:7", "stories:all"]
    prompt_file: str  # Path to .md file or inline content
    mcp_tools: List[str]  # List of MCP tool names to enable
    max_iterations: int = 40  # Maximum query iterations
    output_notion_page_id: Optional[str] = None  # Deprecated: kept for executor compatibility
    enabled: bool = True

    # Scheduling fields (new format uses schedule, old format uses interval_minutes)
    schedule: Optional[List[Tuple[str, str]]] = None  # List of (day, time) like [("Mon", "09:00"), ...]
    interval_minutes: Optional[int] = None  # Legacy: 5, 15, 30, 60, etc. (deprecated, use schedule)

    # Notion integration fields
    # IMMUTABLE after creation. Assigned once by generate_agent_id() or hardcoded
    # for default agents (e.g. "maia"). Never mutate this field on an existing
    # agent — it's the stable identity used by the self-edit guard and journal
    # lookups. Renaming the agent does NOT change agent_id.
    agent_id: str = ""                                  # "grace", "bondu", "daily-summary"
    notion_page_id: Optional[str] = None                # Agent's page in Agents database
    system_prompt_page_id: Optional[str] = None         # System Prompt subpage ID
    instructions_db_id: Optional[str] = None            # Instructions sub-database ID
    journal_db_id: Optional[str] = None                 # Journal sub-database ID

    # Optional fields
    description: Optional[str] = None
    created_at: Optional[str] = None
    last_run_at: Optional[str] = None
    calendar_event_ids: Optional[str] = None  # Comma-separated event IDs from Google Calendar
    calendar_id: Optional[str] = None  # Dedicated Google Calendar ID for this agent

    # Default agent flag — gets all tools automatically
    is_default_agent: bool = False

    # Journal memory
    journal_memory_days: int = 7  # Days of journal entries to load as memory

    # NEW: Source-level permissions (replaces databases eventually)
    source_access: Optional[List[SourceAccess]] = None

    # NEW: SDK-related fields
    sdk_enabled: bool = True  # Use SDK for execution
    sdk_permission_mode: str = "bypassPermissions"  # or "default", "acceptEdits", "plan"
    sdk_allowed_tools: Optional[List[str]] = None  # Override default tools

    # Granular per-server tool permissions.
    # Maps MCP server name → list of allowed tool names within that server.
    #   - server key absent OR value is None → all tools on that server allowed
    #     (back-compat for agents created before this field existed)
    #   - empty list → no tools allowed (server is fully blocked)
    #   - non-empty list → only the listed tools are allowed
    # Enforced in two places: the MCP server itself filters its `list_tools()`
    # output based on this list (passed via --allowed-tools), AND the SDK
    # `allowed_tools` is computed from this so the model never sees blocked
    # tools.
    tool_permissions: Optional[Dict[str, Optional[List[str]]]] = None

    # NEW: Agentic loop for conversations (tool use in tag-to-chat)
    agentic_loop_enabled: bool = True  # Use agentic turn with tools in conversations
    
    # Messaging: permission gate (platforms are environment-based, not per-agent)
    messaging_enabled: bool = False  # Agent can use messaging tools
    conversation_timeout_minutes: int = 15  # Minutes before conversation timeout

    # Channel-level permissions: restrict which Slack/Discord channels this agent
    # can respond in and query messages from.  None = all channels (backwards compat).
    allowed_channel_ids: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentConfig':
        """Create AgentConfig from dictionary, ignoring unknown fields."""
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.name:
            errors.append("Agent name is required")

        if not self.workspace:
            errors.append("Workspace is required")

        if not self.databases:
            errors.append("At least one database must be selected")

        # Prompt file is optional - uses default or Notion System Prompt

        # Scheduling is optional - agents triggered by calendar events or interval
        if self.interval_minutes is not None and self.interval_minutes <= 0:
            errors.append("Interval must be positive")

        if self.schedule is not None and len(self.schedule) == 0:
            errors.append("Schedule must have at least one run")

        if self.max_iterations <= 0:
            errors.append("Max iterations must be positive")

        return errors

    def get_initial_context_sources(self) -> Dict[str, Optional[int]]:
        """Deprecated: bulk context pre-loading removed.

        Agents now use query tools to load data on demand. Returns empty
        dict. Kept for backward compatibility.
        """
        return {}

    def _parse_legacy_databases(self) -> Dict[str, Optional[int]]:
        """Parse legacy databases field into dict of source -> days"""
        result = {}
        for source_spec in self.databases:
            if ':' in source_spec:
                database_name, days_str = source_spec.split(':', 1)
                days = None if days_str == 'all' else int(days_str)
            else:
                database_name = source_spec
                days = None
            result[database_name] = days
        return result

    def get_queryable_sources(self) -> List[str]:
        """Get sources agent can query dynamically"""
        if self.source_access:
            return [
                access.source_name
                for access in self.source_access
                if SourcePermission.QUERY in access.permissions
            ]
        else:
            # Legacy: all initial sources are queryable
            return [db.split(':')[0] for db in self.databases]

    def get_writable_sources(self) -> List[str]:
        """Get sources agent can write to via MCP"""
        if self.source_access:
            return [
                access.source_name
                for access in self.source_access
                if SourcePermission.WRITE in access.permissions
            ]
        else:
            return []  # Legacy mode: no write permissions

    def can_access_channel(self, channel_id: str) -> bool:
        """Check if agent is allowed to operate in a given channel.

        Returns True when the allowlist is None (legacy/unrestricted) or
        when *channel_id* is explicitly listed.
        """
        if self.allowed_channel_ids is None:
            return True
        return channel_id in self.allowed_channel_ids

    def get_allowed_tools_for_server(self, server: str) -> Optional[List[str]]:
        """Return the explicit allowlist for an MCP server, or None for 'all allowed'.

        - None / missing key → all tools on the server are allowed (back-compat)
        - [] → no tools are allowed
        - [...] → only listed tools are allowed
        """
        if not self.tool_permissions:
            return None
        if server not in self.tool_permissions:
            return None
        return self.tool_permissions.get(server)

    def is_tool_allowed(self, server: str, tool: str) -> bool:
        """Check whether *tool* on *server* is permitted for this agent."""
        allow = self.get_allowed_tools_for_server(server)
        if allow is None:
            return True
        return tool in allow

    def can_query_source(self, source_name: str, days: int) -> bool:
        """Check if agent can query this source with given time range"""
        if not self.source_access:
            return True  # Legacy mode: allow all queries

        for access in self.source_access:
            if access.source_name == source_name:
                if SourcePermission.QUERY not in access.permissions:
                    return False
                if access.max_query_days and days > access.max_query_days:
                    return False
                return True
        return False


def get_config_file_path() -> Path:
    """Get the path to promaia.config.json."""
    from promaia.utils.env_writer import get_config_path
    return get_config_path()


def load_config() -> Dict[str, Any]:
    """Load the entire config file."""
    config_path = get_config_file_path()

    if not config_path.exists():
        return {}

    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return {}


def save_config(config: Dict[str, Any]) -> None:
    """Save only the agents section to the config file.

    Performs a read-merge-write: reads the current file from disk, updates
    only the 'agents' key, and writes back.  This avoids overwriting
    concurrent changes to databases, workspaces, or other sections.
    """
    config_path = get_config_file_path()

    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Read fresh state from disk to preserve other sections
        existing = {}
        if config_path.exists():
            with open(config_path, 'r') as f:
                existing = json.load(f)

        # Only update the agents section
        existing['agents'] = config.get('agents', [])

        with open(config_path, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")
        raise


def _get_agents_file() -> Path:
    """Get the path to agents.json inside the data directory."""
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / "agents.json"


def load_agents() -> List[AgentConfig]:
    """
    Load all agent configurations.

    Checks separate agents.json file first (immune to database sync clearing),
    falls back to promaia.config.json for backwards compatibility.
    """
    # Try separate agents file first (preferred, won't be cleared by sync)
    agents_file = _get_agents_file()
    if agents_file.exists():
        try:
            with open(agents_file, 'r') as f:
                agents_data_file = json.load(f)
                if 'agents' in agents_data_file:
                    agents = []
                    for agent_data in agents_data_file['agents']:
                        try:
                            agents.append(AgentConfig.from_dict(agent_data))
                        except Exception as e:
                            print(f"Error loading agent from agents.json {agent_data.get('name', 'unknown')}: {e}")
                    if agents:  # Return if we found any agents
                        return agents
        except Exception as e:
            print(f"Could not load agents.json: {e}, falling back to main config")

    # Fall back to main config (backwards compatibility)
    config = load_config()
    agents_data = config.get('agents', [])

    agents = []
    for agent_data in agents_data:
        try:
            agents.append(AgentConfig.from_dict(agent_data))
        except Exception as e:
            print(f"Error loading agent {agent_data.get('name', 'unknown')}: {e}")

    return agents


def save_agent(agent: AgentConfig) -> None:
    """Save or update an agent configuration."""
    agent_dict = agent.to_dict()

    def _upsert(agents_list: list) -> list:
        for i, existing in enumerate(agents_list):
            if existing.get('name') == agent.name:
                agents_list[i] = agent_dict
                return agents_list
        agents_list.append(agent_dict)
        return agents_list

    # 1. Update promaia.config.json (legacy)
    config = load_config()
    if 'agents' not in config:
        config['agents'] = []
    _upsert(config['agents'])
    save_config(config)

    # 2. Update agents.json (preferred source for load_agents)
    agents_file = _get_agents_file()
    if agents_file.exists():
        try:
            with open(agents_file, 'r') as f:
                agents_data = json.load(f)
            if 'agents' not in agents_data:
                agents_data['agents'] = []
            _upsert(agents_data['agents'])
            with open(agents_file, 'w') as f:
                json.dump(agents_data, f, indent=2)
        except Exception as e:
            print(f"Warning: could not update agents.json: {e}")


def delete_agent(agent_name: str) -> bool:
    """Delete an agent configuration and cascade-clean all local resources.

    Cleans up:
    - Config database entries matching the agent's journal_db_id
    - SQLite table for the agent's journal (notion_{workspace}_{nickname})
    - Markdown directory for journal entries
    - System prompt markdown file

    Returns True if deleted, False if not found.
    """
    import shutil
    import sqlite3

    deleted = False

    # 1. Remove from promaia.config.json (and clean up orphaned database entries)
    config = load_config()
    if 'agents' in config:
        # Find the agent being deleted to extract cleanup info
        agent_data = next((a for a in config['agents'] if a.get('name') == agent_name), None)

        if agent_data:
            journal_db_id = agent_data.get('journal_db_id')
            agent_id = agent_data.get('agent_id', '')
            workspace = agent_data.get('workspace', '')

            # Remove orphaned database entries via DatabaseManager
            # (avoids stale-snapshot overwrites from save_config)
            if journal_db_id:
                try:
                    from promaia.config.databases import get_database_manager
                    db_manager = get_database_manager()
                    orphaned_keys = [
                        key for key, db in db_manager.databases.items()
                        if db.database_id == journal_db_id
                    ]
                    for key in orphaned_keys:
                        db_manager.remove_database(key)
                except Exception as e:
                    print(f"Warning: could not clean up orphaned databases: {e}")

            # Clean up local storage (SQLite + markdown)
            if agent_id and workspace:
                _cleanup_local_storage(agent_id, workspace)

        original_length = len(config['agents'])
        config['agents'] = [a for a in config['agents'] if a.get('name') != agent_name]
        if len(config['agents']) < original_length:
            save_config(config)
            deleted = True

    # 2. Remove from agents.json
    agents_file = _get_agents_file()
    if agents_file.exists():
        try:
            with open(agents_file, 'r') as f:
                agents_data = json.load(f)
            if 'agents' in agents_data:
                original_length = len(agents_data['agents'])
                agents_data['agents'] = [a for a in agents_data['agents'] if a.get('name') != agent_name]
                if len(agents_data['agents']) < original_length:
                    with open(agents_file, 'w') as f:
                        json.dump(agents_data, f, indent=2)
                    deleted = True
        except Exception as e:
            print(f"Warning: could not update agents.json: {e}")

    return deleted


def _cleanup_local_storage(agent_id: str, workspace: str) -> None:
    """Remove local SQLite tables, markdown directories, and system prompt files for a deleted agent."""
    import shutil
    import sqlite3

    # Journal nickname follows the pattern: agent_id with dashes→underscores + _journal
    # e.g. "chief-of-staff" → "chief_of_staff_journal"
    db_nickname = f"{agent_id.replace('-', '_')}_journal"
    table_name = f"notion_{workspace}_{db_nickname}"

    # 1. Drop SQLite table from hybrid_metadata.db
    try:
        from promaia.utils.env_writer import get_data_subdir
        db_path = get_data_subdir() / "hybrid_metadata.db"
        if db_path.exists():
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                conn.commit()
    except Exception as e:
        print(f"Warning: could not drop table {table_name}: {e}")

    # 2. Remove journal markdown directory
    try:
        from promaia.utils.env_writer import get_data_subdir
        journal_dir = get_data_subdir() / "md" / "notion" / workspace / db_nickname
        if journal_dir.exists():
            shutil.rmtree(journal_dir)
    except Exception as e:
        print(f"Warning: could not remove journal directory: {e}")

    # 3. Remove system prompt markdown file
    # Pattern: data/md/notion/{workspace}/pages/{agent-id}-system-prompt.md
    try:
        from promaia.utils.env_writer import get_data_subdir
        prompt_file = get_data_subdir() / "md" / "notion" / workspace / "pages" / f"{agent_id}-system-prompt.md"
        if prompt_file.exists():
            prompt_file.unlink()
    except Exception as e:
        print(f"Warning: could not remove system prompt file: {e}")


def get_agent(agent_name: str) -> Optional[AgentConfig]:
    """Get a specific agent by name."""
    agents = load_agents()
    for agent in agents:
        if agent.name == agent_name:
            return agent
    return None


def update_agent_last_run(agent_name: str, timestamp: str) -> None:
    """Update the last run timestamp for an agent."""
    agent = get_agent(agent_name)
    if agent:
        agent.last_run_at = timestamp
        save_agent(agent)
