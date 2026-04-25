"""
Agent configuration management with JSON persistence.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)

# Fields where going from non-empty to empty during a save_agent() call almost
# always indicates a stale-copy / auto-recreate bug rather than user intent.
# The wipe protection in save_agent() preserves the on-disk value for these
# fields unless the caller passes force=True.
# See memory/project_config_wipe_bug.md.
_WIPE_PROTECTED_FIELDS = (
    "databases",
    "mcp_tools",
    "allowed_channel_ids",
    "source_access",
)


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


def _load_agents_from_section() -> Optional[List[Dict[str, Any]]]:
    """Read the per-section agents file via atomic_io. Returns the raw list of
    agent dicts, or None if the file is missing or corrupted (atomic_io
    quarantines the bad bytes and returns None — see atomic_io.read_section)."""
    from promaia.config.atomic_io import read_section
    section = read_section("agents")
    if section is None:
        return None
    if isinstance(section, dict):
        return section.get("agents", [])
    if isinstance(section, list):
        return section
    logger.warning(f"agents.json has unexpected shape ({type(section).__name__}); ignoring")
    return None


def load_agents() -> List[AgentConfig]:
    """
    Load all agent configurations.

    Reads from agents.json (per-section, atomic, the source of truth);
    falls back to promaia.config.json's agents key only if agents.json is
    absent.
    """
    raw_agents = _load_agents_from_section()
    source = "agents.json"
    if raw_agents is None:
        # Fall back to legacy blob (one-time migration target).
        config = load_config()
        raw_agents = config.get('agents', [])
        source = "promaia.config.json (legacy)"

    agents = []
    for agent_data in raw_agents:
        try:
            agents.append(AgentConfig.from_dict(agent_data))
        except Exception as e:
            logger.error(f"Error loading agent {agent_data.get('name', 'unknown')} from {source}: {e}")

    return agents


def _apply_wipe_protection(
    new_dict: Dict[str, Any],
    existing_dict: Optional[Dict[str, Any]],
    force: bool,
) -> Dict[str, Any]:
    """If a wipe-protected field is going from non-empty (on disk) to empty
    (in the incoming save), preserve the on-disk value and log a warning.

    Most wipes have been "agentic_adapter auto-recreates a bare-bones agent
    after the top-level agents key was nuked from blob, then saves with
    databases=[]." This guard makes such a save a no-op for the wipe-prone
    fields rather than a silent data loss event.

    Pass force=True from CLI flows where the user genuinely wants to clear
    a list (e.g. `agent edit` deselect-all).
    """
    if force or existing_dict is None:
        return new_dict
    merged = dict(new_dict)
    for field in _WIPE_PROTECTED_FIELDS:
        existing_val = existing_dict.get(field)
        new_val = merged.get(field)
        if existing_val and not new_val:
            logger.warning(
                f"save_agent({merged.get('name')!r}): wipe protection — "
                f"field {field!r} is non-empty on disk ({existing_val!r}) but "
                f"empty in incoming save. Preserving disk value. Pass "
                f"force=True to override. See memory/project_config_wipe_bug.md."
            )
            merged[field] = existing_val
    return merged


def save_agent(agent: AgentConfig, *, force: bool = False) -> None:
    """Save or update an agent configuration.

    Writes are atomic per-section (agents.json) via promaia.config.atomic_io.
    The legacy promaia.config.json blob is mirror-updated best-effort for
    backward compat with code paths that still read the blob directly.

    Wipe protection: if any of the fields in `_WIPE_PROTECTED_FIELDS`
    (databases, mcp_tools, allowed_channel_ids, source_access) is non-empty
    on disk but empty in the incoming agent, the on-disk value is preserved.
    Pass force=True from intentional-clear flows (CLI deselect-all, etc.)
    to override.
    """
    from promaia.config.atomic_io import read_section, write_section

    agent_dict = agent.to_dict()

    def _upsert(agents_list: list, incoming: Dict[str, Any]) -> list:
        for i, existing in enumerate(agents_list):
            if existing.get('name') == incoming.get('name'):
                agents_list[i] = incoming
                return agents_list
        agents_list.append(incoming)
        return agents_list

    # Read existing on-disk state so wipe protection can compare.
    section = read_section("agents") or {"agents": []}
    if isinstance(section, list):
        section = {"agents": section}
    existing_agents = section.get("agents", [])
    existing_for_this_name = next(
        (a for a in existing_agents if a.get('name') == agent.name),
        None,
    )

    # Apply wipe protection BEFORE upsert.
    safe_dict = _apply_wipe_protection(agent_dict, existing_for_this_name, force=force)

    # Source of truth: agents.json (atomic + backups).
    section["agents"] = _upsert(existing_agents, safe_dict)
    write_section("agents", section)

    # Mirror to legacy blob best-effort. Failures here are NOT fatal — the
    # source of truth above already succeeded.
    try:
        config = load_config()
        if 'agents' not in config:
            config['agents'] = []
        config['agents'] = _upsert(config['agents'], safe_dict)
        save_config(config)
    except Exception as e:
        logger.warning(f"save_agent({agent.name!r}): legacy blob mirror failed (agents.json is authoritative): {e}")


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

    # 2. Remove from agents.json (atomic, source of truth)
    try:
        from promaia.config.atomic_io import read_section, write_section
        section = read_section("agents")
        if section is not None:
            if isinstance(section, list):
                section = {"agents": section}
            agents_list = section.get('agents', [])
            original_length = len(agents_list)
            section['agents'] = [a for a in agents_list if a.get('name') != agent_name]
            if len(section['agents']) < original_length:
                write_section("agents", section)
                deleted = True
    except Exception as e:
        logger.warning(f"delete_agent({agent_name!r}): could not update agents.json: {e}")

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
