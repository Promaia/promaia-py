"""Unified browser for interactive source selection (databases + Discord channels)."""

import asyncio
import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import Application
from prompt_toolkit.layout.containers import HSplit, Window, VSplit, FloatContainer, Float
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import TextArea, Frame
from prompt_toolkit.styles import Style
from rich.console import Console

logger = logging.getLogger(__name__)


@dataclass
class BrowserEntry:
    """Represents a single source row in the browser table."""
    qualified_name: str      # "koii.journal" or "koii.discord#general"
    source_type: str         # "notion", "discord", "slack", "gmail", etc.
    load_value: str          # Days value for the Load field (e.g. "7", "all")
    default_days: int        # From DatabaseConfig.default_days
    enabled: bool            # Checkbox state
    is_channel: bool         # True for discord/slack channels
    db_config: Any           # Reference to DatabaseConfig
    channel_name: str = ""   # Channel name if is_channel
    server_name: str = ""    # Server/db nickname for discord/slack channels
    marked_for_deletion: bool = False
    default_include: bool = False  # Default column toggle state (from db_config.default_include)


@dataclass
class BrowserResult:
    """Result returned by the browser TUI."""
    sources: List[str] = field(default_factory=list)              # Selected source specs
    deleted_sources: List[str] = field(default_factory=list)      # Sources to delete
    default_changes: Dict[str, int] = field(default_factory=dict)    # qualified_name -> new default_days
    default_include_changes: Dict[str, bool] = field(default_factory=dict)  # qualified_name -> new default_include
    cancelled: bool = False                                          # True if user pressed Esc


@dataclass
class DisplayRow:
    """A row in the browser display — either a group header or a source entry."""
    is_header: bool              # True for group header rows
    group_type: str              # "notion", "discord", etc.
    group_label: str             # "Notion", "Discord", etc. (title-cased)
    entry: Optional[BrowserEntry] = None  # Set for source rows AND single-source headers
    entry_index: int = -1        # Index into entries[] (-1 for pure headers)
    children_indices: List[int] = field(default_factory=list)  # For headers: indices into entries[]


_NON_TEMPORAL_TYPES = frozenset({"google_sheets", "task_queue"})


def safe_parse_days(source_spec: str, fallback_days):
    """Safely parse days from source spec, handling special values like 'all'."""
    if ':' not in source_spec:
        return fallback_days

    days_part = source_spec.split(':')[-1]

    # Handle special values
    if days_part.lower() in ['all', 'unlimited', 'max']:
        return days_part  # Keep as string for special values

    # Try to parse as integer
    try:
        return int(days_part)
    except ValueError:
        # If parsing fails, return fallback
        return fallback_days

def launch_unified_browser(
    workspace: Optional[str],
    default_days: Optional[int] = None,
    database_filter: Optional[List[str]] = None,
    current_sources: Optional[List[str]] = None,
    respect_defaults: bool = False,
    recents_mode: bool = False,
) -> BrowserResult:
    """Launch unified browser for both database sources and Discord channels.

    Args:
        workspace: Workspace name (None for multi-workspace via database_filter)
        default_days: Override default days for all sources
        database_filter: List of workspace/database names to filter by
        current_sources: Current source specs for pre-populating checkboxes
        respect_defaults: When True, use default_include from config for checkbox state
                         regardless of current_sources (current_sources only provides
                         day value overrides). Fixes pre-selection bug when Ctrl+B is
                         used after auto-loading default sources.
        recents_mode: When True, enables Ctrl+Left/Right cycling with query preview

    Returns:
        BrowserResult with sources, deletions, default changes, and cancelled flag.
    """
    return asyncio.run(_interactive_unified_browser(
        workspace, default_days, database_filter, current_sources,
        respect_defaults, recents_mode,
    ))


# Keep backward compatibility
def launch_workspace_browser(workspace: str, default_days: Optional[int] = None) -> BrowserResult:
    """Launch interactive workspace source browser (backward compatibility)."""
    return launch_unified_browser(workspace, default_days)


def _gather_workspace_databases(workspace, database_filter, console):
    """Gather databases from workspace(s) and apply filters.

    Returns (workspace_databases, workspace_names, workspace_display) or raises ValueError.
    """
    from promaia.config.databases import get_database_manager
    from promaia.config.workspaces import get_workspace_manager

    db_manager = get_database_manager()
    workspace_manager = get_workspace_manager()

    # Handle multiple workspaces case
    if workspace is None and database_filter:
        workspace_names = []
        for filter_item in database_filter:
            base_name = filter_item.split(':')[0]
            if workspace_manager.validate_workspace(base_name):
                if base_name not in workspace_names:
                    workspace_names.append(base_name)
            elif '.' in base_name:
                potential_workspace = base_name.split('.')[0]
                if workspace_manager.validate_workspace(potential_workspace):
                    if potential_workspace not in workspace_names:
                        workspace_names.append(potential_workspace)

        if not workspace_names:
            raise ValueError(f"No valid workspaces found in database filter: {database_filter}")

        workspace_databases = []
        for ws_name in workspace_names:
            workspace_databases.extend(db_manager.get_workspace_databases(ws_name))

        workspace_display = ', '.join(workspace_names)
    else:
        if workspace is None:
            raise ValueError("No workspace specified")

        workspace_databases = db_manager.get_workspace_databases(workspace)
        workspace_display = workspace
        workspace_names = [workspace]

    # Add workspace-agnostic databases
    agnostic_databases = db_manager.get_workspace_agnostic_databases()
    workspace_databases.extend(agnostic_databases)

    if not workspace_databases:
        raise ValueError(f"No databases found in workspace(s) '{workspace_display}'")

    # Filter databases if specified
    if database_filter:
        workspace_names_in_filter = []
        database_names_in_filter = []

        for filter_item in database_filter:
            base_name = filter_item.split(':')[0]
            if workspace_manager.validate_workspace(base_name):
                workspace_names_in_filter.append(base_name)
            else:
                database_names_in_filter.append(filter_item)

        if workspace is None:
            if workspace_names_in_filter:
                workspace_databases = [
                    db for db in workspace_databases
                    if db.workspace in workspace_names_in_filter or db.get_qualified_name() in database_names_in_filter
                ]
            else:
                workspace_databases = [db for db in workspace_databases if db.get_qualified_name() in database_names_in_filter]
        else:
            if workspace in workspace_names_in_filter:
                workspace_databases = [
                    db for db in workspace_databases
                    if db.workspace == workspace or db.get_qualified_name() in database_names_in_filter
                ]
            else:
                workspace_databases = [db for db in workspace_databases if db.get_qualified_name() in database_names_in_filter]

    return workspace_databases, workspace_names, workspace_display


def _build_browser_entries(
    workspace_databases, workspace_names, default_days,
    current_sources, respect_defaults, database_filter,
) -> List[BrowserEntry]:
    """Build BrowserEntry objects from workspace databases.

    Args:
        workspace_databases: List of DatabaseConfig objects
        workspace_names: List of workspace names for conversation filtering
        default_days: Override default days
        current_sources: Current source specs for pre-population
        respect_defaults: When True, use default_include from config for checkbox state
        database_filter: Database filter list (for browser_include override)

    Returns:
        Sorted list of BrowserEntry objects.
    """
    entries = []

    # Build lookup for current sources to preserve user day edits
    current_source_lookup = {}
    current_enabled_set = set()
    if current_sources:
        for source in current_sources:
            if '#' in source:
                db_channel = source.rsplit(':', 1)[0]
                current_source_lookup[db_channel] = source
                current_enabled_set.add(db_channel)
            else:
                db_name = source.split(':')[0]
                current_source_lookup[db_name] = source
                current_enabled_set.add(db_name)

    # Build a name->config lookup for quick access
    db_config_by_name = {}
    for db in workspace_databases:
        db_config_by_name[db.get_qualified_name()] = db

    for db in workspace_databases:
        if not db.browser_include and not (database_filter and db.get_qualified_name() in database_filter):
            continue

        qualified_name = db.get_qualified_name()
        default_days_for_db = default_days if default_days is not None else db.default_days

        if db.source_type in ("discord", "slack"):
            _build_channel_entries(
                entries, db, qualified_name, default_days_for_db,
                current_source_lookup, current_enabled_set,
                respect_defaults, database_filter,
            )
        else:
            # Regular database
            # Special handling for convos database - filter by workspace
            if db.source_type == "conversation" and workspace_names:
                try:
                    from promaia.storage.hybrid_storage import get_hybrid_registry
                    registry = get_hybrid_registry()
                    matching_convos = registry.query_conversations_by_workspace(workspace_names)
                    if not matching_convos:
                        continue
                except Exception:
                    continue

            # Determine load value from current sources or default
            if qualified_name in current_source_lookup:
                load_val = str(safe_parse_days(current_source_lookup[qualified_name], default_days_for_db))
            else:
                load_val = str(default_days_for_db)

            # Determine enabled state: actual loaded sources take priority
            if current_sources is not None and current_enabled_set:
                is_enabled = qualified_name in current_enabled_set
            elif respect_defaults:
                is_enabled = db.default_include
            else:
                is_enabled = db.default_include

            entries.append(BrowserEntry(
                qualified_name=qualified_name,
                source_type=db.source_type,
                load_value=load_val,
                default_days=db.default_days,
                enabled=is_enabled,
                is_channel=False,
                db_config=db,
                default_include=db.default_include,
            ))

    # Inject task queue entry if the file exists
    try:
        from promaia.agents.task_queue_file import task_queue_exists
        if task_queue_exists():
            is_enabled = "task_queue" in current_enabled_set if current_sources is not None else False
            entries.append(BrowserEntry(
                qualified_name="task_queue",
                source_type="task_queue",
                load_value="all",
                default_days=0,
                enabled=is_enabled,
                is_channel=False,
                db_config=None,
                default_include=False,
            ))
    except Exception:
        pass

    # Sort: databases first, then discord, then slack, non-temporal last
    def sort_key(e):
        if e.source_type in _NON_TEMPORAL_TYPES:
            primary = 4
        elif not e.is_channel and e.source_type not in ('discord', 'slack'):
            primary = 0
        elif e.source_type == 'discord':
            primary = 1
        elif e.source_type == 'slack':
            primary = 2
        else:
            primary = 3
        return (primary, e.qualified_name, e.channel_name)

    entries.sort(key=sort_key)
    return entries


def _build_channel_entries(
    entries, db, qualified_name, default_days_for_db,
    current_source_lookup, current_enabled_set,
    respect_defaults, database_filter,
):
    """Build BrowserEntry objects for discord/slack channel sources."""
    synced_channels = get_synced_channels_from_filesystem(db)
    synced_names = {ch['name'] for ch in synced_channels}

    # Include configured-but-not-yet-synced channels
    configured_ids = db.property_filters.get('channel_id', [])
    if isinstance(configured_ids, str):
        configured_ids = [configured_ids]

    if configured_ids:
        channel_names_map = db.property_filters.get('channel_names', {})
        id_to_name = dict(channel_names_map) if channel_names_map else {}

        if not id_to_name:
            try:
                import json as _json
                from promaia.utils.env_writer import get_cache_dir
                cache_prefix = f"{db.source_type}_channels"
                cache_file = get_cache_dir() / f"{cache_prefix}_{db.workspace}_{db.database_id}.json"
                if cache_file.exists():
                    cache_data = _json.loads(cache_file.read_text())
                    id_to_name = {ch['id']: ch['name'] for ch in cache_data.get('channels', [])}
            except Exception:
                pass

        for cid in configured_ids:
            cname = id_to_name.get(cid)
            if cname and cname not in synced_names:
                synced_channels.append({"id": cid, "name": cname, "message_count": 0, "last_activity": "not synced"})
                synced_names.add(cname)

    channels = synced_channels

    # Build channel_name -> days map
    saved_channel_days = db.property_filters.get('channel_days', {})
    channel_name_days = {}
    if saved_channel_days:
        channel_names_map = db.property_filters.get('channel_names', {})
        id_to_name = dict(channel_names_map) if channel_names_map else {}
        if not id_to_name:
            try:
                import json as _json
                from promaia.utils.env_writer import get_cache_dir
                cache_prefix = f"{db.source_type}_channels"
                _cf = get_cache_dir() / f"{cache_prefix}_{db.workspace}_{db.database_id}.json"
                if _cf.exists():
                    _cd = _json.loads(_cf.read_text())
                    id_to_name = {ch['id']: ch['name'] for ch in _cd.get('channels', [])}
            except Exception:
                pass
        for cid, cdays in saved_channel_days.items():
            cname = id_to_name.get(cid)
            if cname:
                channel_name_days[cname] = cdays

    if channels:
        for channel in channels:
            channel_name = channel['name']
            per_channel_days = channel_name_days.get(channel_name, default_days_for_db)
            db_channel_key = f"{qualified_name}#{channel_name}"

            if db_channel_key in current_source_lookup:
                load_val = str(safe_parse_days(current_source_lookup[db_channel_key], per_channel_days))
            else:
                load_val = str(per_channel_days)

            # Actual loaded sources take priority over defaults
            if current_source_lookup is not None and current_enabled_set:
                is_enabled = db_channel_key in current_enabled_set
            elif respect_defaults:
                is_enabled = db.default_include
            else:
                is_enabled = db.default_include

            entries.append(BrowserEntry(
                qualified_name=f"{qualified_name}#{channel_name}",
                source_type=db.source_type,
                load_value=load_val,
                default_days=per_channel_days if isinstance(per_channel_days, int) else default_days_for_db,
                enabled=is_enabled,
                is_channel=True,
                db_config=db,
                channel_name=channel_name,
                server_name=db.nickname,
                default_include=db.default_include,
            ))
    else:
        # No channels synced — show database entry with 0 days
        if qualified_name in current_source_lookup:
            load_val = str(safe_parse_days(current_source_lookup[qualified_name], 0))
        else:
            load_val = "0"

        entries.append(BrowserEntry(
            qualified_name=qualified_name,
            source_type=db.source_type,
            load_value=load_val,
            default_days=0,
            enabled=False,
            is_channel=False,
            db_config=db,
            server_name=db.nickname,
            default_include=db.default_include,
        ))


def _build_display_rows(entries: List[BrowserEntry]) -> List[DisplayRow]:
    """Group entries by source_type into DisplayRows with headers.

    Non-temporal sources (google_sheets) appear first, then a section break,
    then temporal sources. Each section has its own header/separator.

    Single-source groups: the entry IS the header row (no child row).
    Multi-source groups: a header row + indented child rows.
    Discord/Slack with multiple servers get a 3-level hierarchy:
      type header → server sub-header → channel children.
    """
    groups: Dict[str, List[Tuple[int, BrowserEntry]]] = {}
    for idx, e in enumerate(entries):
        groups.setdefault(e.source_type, []).append((idx, e))

    # Preserve order of first appearance (matches existing sort)
    seen_types: List[str] = []
    for e in entries:
        if e.source_type not in seen_types:
            seen_types.append(e.source_type)

    display_rows: List[DisplayRow] = []

    def _append_type_rows(source_type):
        group = groups[source_type]
        label = source_type.replace("_", " ").title()
        if source_type in ("discord", "slack"):
            _build_server_grouped_rows(display_rows, group, source_type, label)
        elif len(group) == 1:
            orig_idx, entry = group[0]
            display_rows.append(DisplayRow(
                is_header=True,
                group_type=source_type,
                group_label=label,
                entry=entry,
                entry_index=orig_idx,
                children_indices=[orig_idx],
            ))
        else:
            child_indices = [orig_idx for orig_idx, _ in group]
            display_rows.append(DisplayRow(
                is_header=True,
                group_type=source_type,
                group_label=label,
                entry=None,
                entry_index=-1,
                children_indices=child_indices,
            ))
            for orig_idx, entry in group:
                display_rows.append(DisplayRow(
                    is_header=False,
                    group_type=source_type,
                    group_label=label,
                    entry=entry,
                    entry_index=orig_idx,
                ))

    # All source types in one table (sort_key already puts non-temporal last)
    for st in seen_types:
        _append_type_rows(st)

    return display_rows


def _build_server_grouped_rows(
    display_rows: List[DisplayRow],
    group: List[Tuple[int, 'BrowserEntry']],
    source_type: str,
    type_label: str,
):
    """Build 3-level display rows for discord/slack: type → server → channels."""
    # Group by server_name, preserving order
    servers: Dict[str, List[Tuple[int, 'BrowserEntry']]] = {}
    server_order: List[str] = []
    for orig_idx, entry in group:
        sname = entry.server_name or entry.db_config.nickname
        if sname not in servers:
            servers[sname] = []
            server_order.append(sname)
        servers[sname].append((orig_idx, entry))

    all_indices = [orig_idx for orig_idx, _ in group]

    if len(servers) == 1:
        # Single server — same as before: type header → channels
        server_name = server_order[0]
        server_entries = servers[server_name]

        if len(server_entries) == 1:
            orig_idx, entry = server_entries[0]
            display_rows.append(DisplayRow(
                is_header=True,
                group_type=source_type,
                group_label=type_label,
                entry=entry,
                entry_index=orig_idx,
                children_indices=[orig_idx],
            ))
        else:
            display_rows.append(DisplayRow(
                is_header=True,
                group_type=source_type,
                group_label=type_label,
                entry=None,
                entry_index=-1,
                children_indices=all_indices,
            ))
            for orig_idx, entry in server_entries:
                display_rows.append(DisplayRow(
                    is_header=False,
                    group_type=source_type,
                    group_label=type_label,
                    entry=entry,
                    entry_index=orig_idx,
                ))
    else:
        # Multiple servers — type header → server sub-headers → channels
        display_rows.append(DisplayRow(
            is_header=True,
            group_type=source_type,
            group_label=type_label,
            entry=None,
            entry_index=-1,
            children_indices=all_indices,
        ))

        for server_name in server_order:
            server_entries = servers[server_name]
            server_indices = [orig_idx for orig_idx, _ in server_entries]

            server_group_type = f"{source_type}:{server_name}"
            if len(server_entries) == 1 and not server_entries[0][1].is_channel:
                # Single non-channel entry (no channels synced) — show as child row
                orig_idx, entry = server_entries[0]
                display_rows.append(DisplayRow(
                    is_header=False,
                    group_type=server_group_type,
                    group_label=server_name,
                    entry=entry,
                    entry_index=orig_idx,
                ))
            else:
                # Server sub-header with channel children
                display_rows.append(DisplayRow(
                    is_header=True,
                    group_type=server_group_type,
                    group_label=server_name,
                    entry=None,
                    entry_index=-1,
                    children_indices=server_indices,
                ))
                for orig_idx, entry in server_entries:
                    display_rows.append(DisplayRow(
                        is_header=False,
                        group_type=server_group_type,
                        group_label=server_name,
                        entry=entry,
                        entry_index=orig_idx,
                    ))


# ---------------------------------------------------------------------------
# Table TUI
# ---------------------------------------------------------------------------

# Column widths
_COL_SOURCE_MIN = 26
_COL_LOAD = 18
_COL_DEFAULT = 8


def _make_header_line():
    """Build the formatted text for the table header row."""
    src = "Source".ljust(_COL_SOURCE_MIN)
    load = "Load".center(_COL_LOAD + 2)  # +2 for dot+space
    default = "Default".center(_COL_DEFAULT + 2)
    return f"  {src}  {load}  {default}"


def _make_separator_line():
    total = _COL_SOURCE_MIN + 2 + 2 + _COL_LOAD + 2 + 2 + _COL_DEFAULT + 2
    return "\u2500" * total


async def _interactive_unified_browser(
    workspace, default_days, database_filter, current_sources,
    respect_defaults, recents_mode,
) -> BrowserResult:
    """Interactive unified browser with table layout."""
    console = Console()

    try:
        workspace_databases, workspace_names, workspace_display = _gather_workspace_databases(
            workspace, database_filter, console
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return BrowserResult(cancelled=True)

    entries = _build_browser_entries(
        workspace_databases, workspace_names, default_days,
        current_sources, respect_defaults, database_filter,
    )

    if not entries:
        if database_filter:
            hidden_dbs = [db for db in workspace_databases if not db.browser_include and db.get_qualified_name() in database_filter]
            if hidden_dbs:
                db_names = [db.get_qualified_name() for db in hidden_dbs]
                console.print(f"[red]Requested databases are hidden from browser: {', '.join(db_names)}[/red]")
                console.print("[yellow]Set 'browser_include': true in config to show them[/yellow]")
            else:
                console.print(f"[red]No databases found matching filter: {', '.join(database_filter)}[/red]")
        else:
            console.print(f"[red]No databases or channels available in workspace '{workspace}'[/red]")
        return BrowserResult()

    display_rows = _build_display_rows(entries)
    is_multi_workspace = len(workspace_names) > 1

    # --- Recents state ---
    recents_list = []
    recents_index = -1  # -1 = no recent selected (user's own edits)
    recents_modified = False
    recents_query_preview = ""
    if recents_mode:
        try:
            from promaia.storage.recents import RecentsManager
            rm = RecentsManager()
            recents_list = rm.get_recents()
        except Exception:
            pass

    # --- Classify special row indices ---
    break_indices: set = set()
    nontemporal_leaf_indices: set = set()
    for dr_idx, dr in enumerate(display_rows):
        if dr.group_type in ("__break_spacer__", "__break_header__", "__break_sep__"):
            break_indices.add(dr_idx)
        elif (dr.entry is not None and dr.entry.source_type in _NON_TEMPORAL_TYPES):
            # Non-temporal entries: no TextArea (no days field).
            # Includes both child rows and single-source headers.
            # Multi-source parent headers (entry is None) keep TextAreas for Ctrl+P push.
            nontemporal_leaf_indices.add(dr_idx)

    # --- Build TUI widgets ---
    # TextAreas for rows that have editable day fields.
    # Break rows and non-temporal leaf rows do NOT get TextAreas.
    row_load_areas: Dict[int, TextArea] = {}
    row_default_areas: Dict[int, TextArea] = {}

    for dr_idx, dr in enumerate(display_rows):
        if dr_idx in break_indices or dr_idx in nontemporal_leaf_indices:
            continue

        if dr.entry is not None:
            # Child row or single-source header — values from entry
            load_text = dr.entry.load_value
            default_text = str(dr.entry.default_days)
        else:
            # Multi-source group header — show default_days as a convenience value
            # for Ctrl+P push. Use the first child's default as a starting point.
            children = [entries[i] for i in dr.children_indices]
            child_defaults = [c.default_days for c in children]
            default_val = child_defaults[0] if child_defaults else 7
            load_text = str(default_val)
            default_text = str(default_val)

        load_area = TextArea(
            text=load_text,
            height=1,
            multiline=False,
            wrap_lines=False,
            scrollbar=False,
            focusable=True,
            width=_COL_LOAD,
        )
        load_area.buffer.cursor_position = len(load_text)
        row_load_areas[dr_idx] = load_area

        default_area = TextArea(
            text=default_text,
            height=1,
            multiline=False,
            wrap_lines=False,
            scrollbar=False,
            focusable=True,
            width=_COL_DEFAULT,
        )
        row_default_areas[dr_idx] = default_area

    # Wrap TextAreas in HSplit with dynamic style for greyed-out effect
    row_load_wrappers: Dict[int, HSplit] = {}
    row_default_wrappers: Dict[int, HSplit] = {}
    for dr_idx in row_load_areas:
        dr = display_rows[dr_idx]

        if dr.entry is not None:
            # Single entry row — check entry directly
            def make_load_style(e=dr.entry):
                def get_style():
                    return "class:disabled" if not e.enabled else ""
                return get_style

            def make_default_style(e=dr.entry):
                def get_style():
                    return "class:disabled" if not e.default_include else ""
                return get_style
        else:
            # Multi-source header — always grey (it's a staging area for Ctrl+P)
            def make_load_style():
                def get_style():
                    return "class:disabled"
                return get_style

            def make_default_style():
                def get_style():
                    return "class:disabled"
                return get_style

        row_load_wrappers[dr_idx] = HSplit([row_load_areas[dr_idx]], style=make_load_style())
        row_default_wrappers[dr_idx] = HSplit([row_default_areas[dr_idx]], style=make_default_style())

    # State
    current_row = 0
    while current_row in break_indices and current_row < len(display_rows) - 1:
        current_row += 1
    current_col = 0  # 0=Load, 1=Default
    confirmed = False

    def _display_name(entry):
        """Return a clean display name for an entry."""
        if entry.is_channel:
            return f"#{entry.channel_name}"
        if is_multi_workspace:
            return entry.qualified_name
        # Single workspace: use nickname (strips workspace prefix)
        if entry.db_config is None:
            return entry.qualified_name
        return entry.db_config.nickname

    def _source_label(dr_idx):
        """Build the source column text for a display row."""
        row = display_rows[dr_idx]

        if row.group_type in ("__break_spacer__", "__break_header__", "__break_sep__"):
            return ""

        if row.is_header and row.entry is None:
            children = [entries[i] for i in row.children_indices]
            any_del = any(c.marked_for_deletion for c in children)
            prefix = "[DEL] " if any_del else ""
            # Server sub-header (e.g. "discord:servername")
            if ":" in row.group_type:
                return f"  {prefix}{row.group_label}"
            # Top-level group header
            return f"{prefix}{row.group_label}"

        elif row.is_header and row.entry is not None:
            # Single-source header
            e = row.entry
            prefix = "[DEL] " if e.marked_for_deletion else ""
            return f"{prefix}{row.group_label} \u00b7 {_display_name(e)}"

        else:
            # Child row — extra indent for channels under a server sub-header
            e = row.entry
            prefix = "[DEL] " if e.marked_for_deletion else ""
            if ":" in row.group_type and e.is_channel:
                indent = "    "
            else:
                indent = "  "
            return f"{indent}{prefix}{_display_name(e)}"

    def _get_row_style(dr_idx):
        """Return style suffix for a row based on state."""
        row = display_rows[dr_idx]
        if row.group_type in ("__break_spacer__", "__break_header__", "__break_sep__"):
            return ""
        if row.entry is not None and row.entry.marked_for_deletion:
            return "class:deleted"
        elif row.is_header and row.entry is None:
            children = [entries[i] for i in row.children_indices]
            if all(c.marked_for_deletion for c in children):
                return "class:deleted"
        if dr_idx == current_row:
            return "class:focused-row"
        return ""

    # Build row windows using VSplit for proper column alignment
    row_windows = []
    row_source_windows: Dict[int, Window] = {}  # For focusing non-textarea rows
    for dr_idx in range(len(display_rows)):
        dr = display_rows[dr_idx]

        # --- Section break rows: purely visual ---
        if dr.group_type == "__break_spacer__":
            row_windows.append(Window(height=1))
            continue
        if dr.group_type == "__break_header__":
            row_windows.append(Window(
                FormattedTextControl(text=_make_header_line),
                height=1, style="class:header",
            ))
            continue
        if dr.group_type == "__break_sep__":
            row_windows.append(Window(
                FormattedTextControl(text=_make_separator_line),
                height=1, style="class:separator",
            ))
            continue

        def make_source_control(i=dr_idx):
            def get_text():
                return _source_label(i).ljust(_COL_SOURCE_MIN)
            return get_text

        def make_row_style(i=dr_idx):
            def get_style():
                return _get_row_style(i)
            return get_style

        is_header_row = dr.is_header and dr.entry is None
        is_nontemporal_leaf = dr_idx in nontemporal_leaf_indices

        source_win = Window(
            FormattedTextControl(text=make_source_control()),
            width=_COL_SOURCE_MIN + 2,
            dont_extend_width=True,
            style=make_row_style(),
        )

        # Focus sink for rows that have no editable TextArea
        # (non-temporal leaves and pure header rows without TextAreas)
        needs_focus_sink = is_nontemporal_leaf
        if needs_focus_sink:
            focus_sink = Window(FormattedTextControl(text="", focusable=True), width=0)
            row_source_windows[dr_idx] = focus_sink
        elif is_header_row:
            # Temporal parent rows keep TextAreas — only add focus sink if
            # they somehow don't have one (shouldn't happen, but be safe)
            if dr_idx not in row_load_areas:
                focus_sink = Window(FormattedTextControl(text="", focusable=True), width=0)
                row_source_windows[dr_idx] = focus_sink

        # Dot indicators — ● when on, ○ when off
        # For parent rows: ● all children on, ● (grey) some on, ○ none on
        def make_load_dot(i=dr_idx):
            def get_dot():
                d = display_rows[i]
                if d.entry is not None:
                    return "\u25cf " if d.entry.enabled else "\u25cb "
                else:
                    children = [entries[ci] for ci in d.children_indices]
                    return "\u25cf " if any(c.enabled for c in children) else "\u25cb "
            return get_dot

        def make_load_dot_style(i=dr_idx):
            def get_style():
                d = display_rows[i]
                if d.entry is not None:
                    return "class:disabled" if not d.entry.enabled else ""
                else:
                    children = [entries[ci] for ci in d.children_indices]
                    on = sum(1 for c in children if c.enabled)
                    if on == 0:
                        return "class:disabled"
                    elif on < len(children):
                        return "class:disabled"  # partial = grey
                    else:
                        return ""  # all on = white
            return get_style

        def make_default_dot(i=dr_idx):
            def get_dot():
                d = display_rows[i]
                if d.entry is not None:
                    return "\u25cf " if d.entry.default_include else "\u25cb "
                else:
                    children = [entries[ci] for ci in d.children_indices]
                    return "\u25cf " if any(c.default_include for c in children) else "\u25cb "
            return get_dot

        def make_default_dot_style(i=dr_idx):
            def get_style():
                d = display_rows[i]
                if d.entry is not None:
                    return "class:disabled" if not d.entry.default_include else ""
                else:
                    children = [entries[ci] for ci in d.children_indices]
                    on = sum(1 for c in children if c.default_include)
                    if on == 0:
                        return "class:disabled"
                    elif on < len(children):
                        return "class:disabled"  # partial = grey
                    else:
                        return ""  # all on = white
            return get_style

        if is_nontemporal_leaf:
            # Non-temporal leaf: dot indicators but blank placeholders instead of TextAreas
            row_children = [
                source_win,
                Window(width=2, dont_extend_width=True),
                Window(FormattedTextControl(text=make_load_dot()), width=2, dont_extend_width=True),
                Window(width=_COL_LOAD, dont_extend_width=True),  # blank placeholder
                Window(width=2, dont_extend_width=True),
                Window(FormattedTextControl(text=make_default_dot()), width=2, dont_extend_width=True),
                Window(width=_COL_DEFAULT, dont_extend_width=True),  # blank placeholder
            ]
        else:
            row_children = [
                source_win,
                Window(width=2, dont_extend_width=True),
                HSplit([Window(FormattedTextControl(text=make_load_dot()), width=2, dont_extend_width=True)],
                       style=make_load_dot_style()),
                row_load_wrappers[dr_idx],
                Window(width=2, dont_extend_width=True),
                HSplit([Window(FormattedTextControl(text=make_default_dot()), width=2, dont_extend_width=True)],
                       style=make_default_dot_style()),
                row_default_wrappers[dr_idx],
            ]

        if dr_idx in row_source_windows:
            row_children.append(row_source_windows[dr_idx])
        row_windows.append(VSplit(row_children, height=1))

    def get_status_display():
        enabled_count = sum(1 for e in entries if e.enabled)
        total_count = len(entries)
        del_count = sum(1 for e in entries if e.marked_for_deletion)

        parts = []
        if is_multi_workspace:
            parts.append(workspace_display)
        parts.append(f"{enabled_count}/{total_count} selected")

        if del_count > 0:
            parts.append(f"{del_count} marked for deletion")

        if recents_mode and recents_list:
            mod_marker = " (modified)" if recents_modified else ""
            if recents_index >= 0:
                parts.append(f"Recent {recents_index + 1}/{len(recents_list)}{mod_marker}")

        space_label = "Space Load" if current_col == 0 else "Space Default"
        cur_row = display_rows[current_row] if current_row < len(display_rows) else None
        is_parent = (cur_row and cur_row.is_header and cur_row.entry is None)
        is_nontemporal = (cur_row and cur_row.entry is not None
                          and cur_row.entry.source_type in _NON_TEMPORAL_TYPES)
        keys = f"Up/Dn Nav  Tab Fields  {space_label}  d Del  Enter OK  Esc Cancel"
        if is_nontemporal:
            keys = "Non-temporal (loads all)  " + keys
        if is_parent:
            keys = f"Ctrl+O Push  " + keys
        if recents_mode and recents_list:
            keys = "Ctrl+Left/Right Recents  " + keys

        return f"{' | '.join(parts)} | {keys}"

    status_window = Window(
        FormattedTextControl(text=get_status_display),
        height=1,
        style="class:status",
    )

    # Query preview for recents mode
    query_preview_window = Window(
        FormattedTextControl(text=lambda: f"  {recents_query_preview}" if recents_query_preview else ""),
        height=1 if recents_mode else 0,
        style="class:preview",
    )

    header_window = Window(
        FormattedTextControl(text=_make_header_line),
        height=1,
        style="class:header",
    )
    separator_window = Window(
        FormattedTextControl(text=_make_separator_line),
        height=1,
        style="class:separator",
    )

    container = HSplit([
        header_window,
        separator_window,
        *row_windows,
        Window(height=1),  # Spacer
        query_preview_window,
        status_window,
    ])

    layout = Layout(container)

    # Focus initial field
    if current_row in row_source_windows:
        layout.focus(row_source_windows[current_row])
    elif current_row in row_load_areas:
        layout.focus(row_load_areas[current_row])
    elif row_load_areas:
        layout.focus(row_load_areas[min(row_load_areas.keys())])

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_row, recents_modified
        new_row = current_row - 1
        while new_row >= 0 and new_row in break_indices:
            new_row -= 1
        if new_row >= 0:
            current_row = new_row
            _focus_current_field(event)
            recents_modified = True

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_row, recents_modified
        new_row = current_row + 1
        while new_row < len(display_rows) and new_row in break_indices:
            new_row += 1
        if new_row < len(display_rows):
            current_row = new_row
            _focus_current_field(event)
            recents_modified = True

    # Cmd+Up/Down: jump to first row of previous/next table section
    # Build section start indices (first non-break row after each break group)
    _section_starts: List[int] = []
    if display_rows:
        _section_starts.append(0)
        for i in range(len(display_rows)):
            if i in break_indices:
                # Find first non-break row after this break
                j = i + 1
                while j < len(display_rows) and j in break_indices:
                    j += 1
                if j < len(display_rows) and (not _section_starts or _section_starts[-1] != j):
                    _section_starts.append(j)

    @bindings.add('c-up')
    def jump_section_up(event):
        nonlocal current_row, recents_modified
        for s in reversed(_section_starts):
            if s < current_row:
                current_row = s
                _focus_current_field(event)
                recents_modified = True
                return

    @bindings.add('c-down')
    def jump_section_down(event):
        nonlocal current_row, recents_modified
        for s in _section_starts:
            if s > current_row:
                current_row = s
                _focus_current_field(event)
                recents_modified = True
                return

    @bindings.add(Keys.Tab)
    def next_field(event):
        nonlocal current_col
        current_col = 1 - current_col  # Toggle between 0 and 1
        _focus_current_field(event)

    @bindings.add(Keys.BackTab)  # Shift+Tab
    def prev_field(event):
        nonlocal current_col
        current_col = 1 - current_col
        _focus_current_field(event)

    @bindings.add(' ')
    def toggle_source(event):
        nonlocal recents_modified
        if current_row in break_indices:
            return
        row = display_rows[current_row]
        if current_col == 0:
            # Load column: toggle enabled
            if row.is_header and row.entry is None:
                children = [entries[i] for i in row.children_indices]
                new_state = not all(c.enabled for c in children)
                for c in children:
                    c.enabled = new_state
            elif row.entry is not None:
                row.entry.enabled = not row.entry.enabled
        else:
            # Default column: toggle default_include
            if row.is_header and row.entry is None:
                children = [entries[i] for i in row.children_indices]
                new_state = not all(c.default_include for c in children)
                for c in children:
                    c.default_include = new_state
            elif row.entry is not None:
                row.entry.default_include = not row.entry.default_include
        recents_modified = True
        event.app.invalidate()

    @bindings.add('c-o', eager=True)
    def push_parent_value(event):
        """Push the current parent row's value to all its children (Ctrl+P)."""
        nonlocal recents_modified
        if current_row in break_indices:
            return
        row = display_rows[current_row]
        if not (row.is_header and row.entry is None and row.children_indices):
            return
        parent_load = row_load_areas.get(current_row)
        parent_default = row_default_areas.get(current_row)
        parent_load_text = parent_load.text.strip() if parent_load else ""
        parent_default_text = parent_default.text.strip() if parent_default else ""
        for ci in row.children_indices:
            for cdr_idx, cdr in enumerate(display_rows):
                if cdr.entry_index == ci and cdr.entry is not None:
                    if cdr_idx in row_load_areas and parent_load_text:
                        row_load_areas[cdr_idx].text = parent_load_text
                        cdr.entry.load_value = parent_load_text
                    if cdr_idx in row_default_areas and parent_default_text:
                        row_default_areas[cdr_idx].text = parent_default_text
                    break
        recents_modified = True
        event.app.invalidate()

    @bindings.add('d')
    def mark_deletion(event):
        nonlocal recents_modified
        if current_row in break_indices:
            return
        row = display_rows[current_row]
        # Don't intercept 'd' when typing in a TextArea
        focused = event.app.layout.current_buffer
        if focused and current_row in row_load_areas:
            la = row_load_areas[current_row]
            da = row_default_areas[current_row]
            if focused == la.buffer or focused == da.buffer:
                buf = focused
                if buf.text and buf.cursor_position < len(buf.text):
                    buf.insert_text('d')
                    return
        if row.is_header and row.entry is None:
            # Multi-source header: toggle deletion for all children
            children = [entries[i] for i in row.children_indices]
            new_state = not all(c.marked_for_deletion for c in children)
            for c in children:
                c.marked_for_deletion = new_state
        elif row.entry is not None:
            row.entry.marked_for_deletion = not row.entry.marked_for_deletion
        recents_modified = True
        event.app.invalidate()

    if recents_mode and recents_list:
        @bindings.add('c-right')  # Ctrl+Right
        def next_recent(event):
            nonlocal recents_index, recents_modified, recents_query_preview
            if recents_index < len(recents_list) - 1:
                recents_index += 1
                _apply_recent(recents_list[recents_index])
                recents_modified = False
                event.app.invalidate()

        @bindings.add('c-left')  # Ctrl+Left
        def prev_recent(event):
            nonlocal recents_index, recents_modified, recents_query_preview
            if recents_index > 0:
                recents_index -= 1
                _apply_recent(recents_list[recents_index])
                recents_modified = False
                event.app.invalidate()

    def _apply_recent(recent):
        """Apply a RecentQuery's sources to the browser entries."""
        nonlocal recents_query_preview
        # Build lookup from recent sources
        recent_enabled = set()
        recent_days = {}
        if recent.sources:
            for src in recent.sources:
                if '#' in src:
                    key = src.rsplit(':', 1)[0]
                    days = src.rsplit(':', 1)[1] if ':' in src else ""
                else:
                    key = src.split(':')[0]
                    days = src.split(':')[1] if ':' in src else ""
                recent_enabled.add(key)
                recent_days[key] = days

        # Update entries and corresponding TextAreas
        for dr_idx, dr in enumerate(display_rows):
            if dr.entry is None:
                continue
            entry = dr.entry
            key = entry.qualified_name
            entry.enabled = key in recent_enabled
            if key in recent_days and dr_idx in row_load_areas:
                entry.load_value = recent_days[key]
                row_load_areas[dr_idx].text = recent_days[key]

        # Build query preview
        parts = []
        if recent.sql_query_prompt:
            parts.append(f"-sql {recent.sql_query_prompt}")
        if recent.original_browse_command and '-vs' in recent.original_browse_command:
            # Extract -vs part from original command
            import re
            vs_match = re.findall(r'-vs\s+"([^"]*)"|-vs\s+(\S+)', recent.original_browse_command)
            for match in vs_match:
                vs_text = match[0] or match[1]
                parts.append(f"-vs {vs_text}")
        recents_query_preview = "  ".join(parts) if parts else ""

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    @bindings.add(Keys.ControlC)
    def cancel(event):
        event.app.exit()

    def _focus_current_field(event=None):
        """Focus the appropriate TextArea for current_row/current_col."""
        if current_row in break_indices:
            # Should not happen (navigation skips these), but be safe
            pass
        elif current_row in row_source_windows:
            # Header row or non-temporal leaf — focus sink (no text cursor)
            layout.focus(row_source_windows[current_row])
        elif current_row in row_load_areas:
            if current_col == 0:
                target = row_load_areas[current_row]
            else:
                target = row_default_areas[current_row]
            layout.focus(target)
        if event:
            event.app.invalidate()

    # Styles — prompt_toolkit only supports: bold, underline, italic, reverse,
    # hidden, blink, nobold, nounderline, noitalic, and fg:/bg: colors.
    # "dim" and "strikethrough" are not valid style strings.
    style = Style.from_dict({
        'header': 'bold',
        'separator': '',
        'status': 'reverse',
        'deleted': 'fg:ansired',
        'focused-row': 'bold',
        'preview': 'italic',
        'disabled': 'fg:ansibrightblack',
    })

    app = Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=False,
        mouse_support=False,
    )

    await app.run_async()

    if not confirmed:
        return BrowserResult(cancelled=True)

    # --- Process results ---
    from promaia.config.databases import get_database_manager
    db_manager = get_database_manager()

    result = BrowserResult()

    # Collect deletions
    pending_deletions = [e for e in entries if e.marked_for_deletion]
    if pending_deletions:
        # Safety confirmation outside the TUI
        print()
        console.print(f"About to delete {len(pending_deletions)} source(s):", style="bold red")
        for e in pending_deletions:
            console.print(f"  - {e.qualified_name}", style="red")
        print()
        try:
            confirm_input = input("Type 'delete' to confirm: ").strip()
        except (KeyboardInterrupt, EOFError):
            confirm_input = ""

        if confirm_input == 'delete':
            for e in pending_deletions:
                result.deleted_sources.append(e.qualified_name)
                # Perform deletion
                if e.is_channel:
                    # Remove channel from channel_id list
                    channel_ids = e.db_config.property_filters.get('channel_id', [])
                    if isinstance(channel_ids, str):
                        channel_ids = [channel_ids]
                    channel_names_map = e.db_config.property_filters.get('channel_names', {})
                    # Find the ID for this channel name
                    id_to_remove = None
                    for cid, cname in channel_names_map.items():
                        if cname == e.channel_name:
                            id_to_remove = cid
                            break
                    if id_to_remove and id_to_remove in channel_ids:
                        channel_ids.remove(id_to_remove)
                        e.db_config.property_filters['channel_id'] = channel_ids
                        db_manager.save_database_field(e.db_config, "property_filters")
                        console.print(f"  Removed channel: {e.channel_name}", style="green")
                elif e.db_config is None:
                    # Synthetic entry (e.g. task_queue) — skip database removal
                    console.print(f"  Skipped (not a database): {e.qualified_name}", style="yellow")
                else:
                    # Remove full database
                    removed = db_manager.remove_database(e.db_config.name, e.db_config.workspace)
                    if removed:
                        console.print(f"  Removed database: {e.qualified_name}", style="green")
                    else:
                        console.print(f"  Failed to remove: {e.qualified_name}", style="yellow")
        else:
            console.print("Deletion cancelled. Source selections still applied.", style="yellow")

    # Collect default_days changes
    for dr_idx, dr in enumerate(display_rows):
        if dr.entry is None or dr_idx not in row_default_areas:
            continue
        entry = dr.entry
        new_default_text = row_default_areas[dr_idx].text.strip()
        try:
            new_default = int(new_default_text)
        except ValueError:
            continue
        if new_default != entry.default_days:
            result.default_changes[entry.qualified_name] = new_default
            # Persist the change
            entry.db_config.default_days = new_default
            db_manager.save_database_field(entry.db_config, "default_days")

    # Collect default_include changes
    for dr_idx, dr in enumerate(display_rows):
        if dr.entry is None or dr.entry.db_config is None:
            continue
        entry = dr.entry
        if entry.default_include != entry.db_config.default_include:
            result.default_include_changes[entry.qualified_name] = entry.default_include
            entry.db_config.default_include = entry.default_include
            db_manager.save_database_field(entry.db_config, "default_include")

    # Collect selected sources
    for dr_idx, dr in enumerate(display_rows):
        if dr.entry is None:
            continue
        entry = dr.entry
        if entry.enabled and not entry.marked_for_deletion:
            if entry.source_type in _NON_TEMPORAL_TYPES:
                # Non-temporal: no days suffix
                result.sources.append(entry.qualified_name)
            else:
                load_text = row_load_areas[dr_idx].text.strip()
                result.sources.append(f"{entry.qualified_name}:{load_text}")

    # Store recents query preview for the caller
    if recents_mode and recents_query_preview:
        result._recents_query_preview = recents_query_preview

    return result


def get_synced_channels_from_filesystem(db_config) -> List[Dict]:
    """Get list of channels that have already been synced by checking filesystem.

    Only returns channels whose directories exist AND are configured in the
    data source's property_filters.channel_id list.  This prevents unrelated
    subdirectories (e.g. from another data source sharing a parent directory)
    from appearing.
    """
    channels = []

    try:
        from pathlib import Path
        import json as _json

        # Resolve markdown directory to an absolute path
        md_dir = db_config.markdown_directory
        if not os.path.isabs(md_dir):
            from promaia.utils.env_writer import get_data_dir
            md_dir = os.path.join(str(get_data_dir()), md_dir)

        if not os.path.exists(md_dir):
            return channels

        # Build set of allowed channel names from the config.
        # channel_id stores IDs; we need names for filesystem matching.
        configured_ids = db_config.property_filters.get('channel_id', [])
        if isinstance(configured_ids, str):
            configured_ids = [configured_ids]

        allowed_names: set = set()
        if configured_ids:
            # Primary: use channel_names map from config (persisted at add time)
            channel_names_map = db_config.property_filters.get('channel_names', {})
            if channel_names_map:
                for cid in configured_ids:
                    cname = channel_names_map.get(cid)
                    if cname:
                        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in cname)
                        safe = safe.strip("_").replace(" ", "_")
                        allowed_names.add(safe)
                        allowed_names.add(cname)
            else:
                # Fallback: resolve IDs → names via the channel cache
                try:
                    from promaia.utils.env_writer import get_cache_dir
                    cache_prefix = f"{db_config.source_type}_channels"
                    cache_file = get_cache_dir() / f"{cache_prefix}_{db_config.workspace}_{db_config.database_id}.json"
                    if cache_file.exists():
                        cache_data = _json.loads(cache_file.read_text())
                        id_to_name = {ch['id']: ch['name'] for ch in cache_data.get('channels', [])}
                        for cid in configured_ids:
                            cname = id_to_name.get(cid)
                            if cname:
                                safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in cname)
                                safe = safe.strip("_").replace(" ", "_")
                                allowed_names.add(safe)
                                allowed_names.add(cname)
                except Exception as e:
                    logger.debug(f"Could not load channel cache for name resolution: {e}")

        for channel_dir in Path(md_dir).iterdir():
            if not channel_dir.is_dir() or channel_dir.name.startswith('.'):
                continue

            # If channels are configured, only show those channels.
            # If we couldn't resolve IDs to names (no cache or channel_names),
            # skip filesystem scan entirely — the browser's caller will add
            # configured channels from the cache separately.
            if configured_ids:
                if not allowed_names:
                    return channels  # can't resolve, skip filesystem
                if channel_dir.name not in allowed_names:
                    continue

            message_files = list(channel_dir.glob("*.md"))
            last_sync = "unknown"

            if message_files:
                newest_file = max(message_files, key=lambda f: f.stat().st_mtime)
                import datetime
                last_sync = datetime.datetime.fromtimestamp(
                    newest_file.stat().st_mtime
                ).strftime("%m/%d %H:%M")

            channels.append({
                "id": "unknown",
                "name": channel_dir.name,
                "message_count": len(message_files),
                "last_activity": last_sync
            })

    except Exception as e:
        logger.error(f"Error reading synced channels from filesystem: {e}")

    return channels
