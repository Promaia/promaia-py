"""
Shared functions for parsing chat arguments, resolving browse scope,
and applying browser/query results to context_state.

This module centralizes logic that was previously duplicated across
edit_context, handle_browse_in_edit_context, handle_manual_browse_edit,
handle_recents_in_edit_context, and /browser-inline in interface.py.
"""

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

@dataclass
class ParsedChatArgs:
    """Result of parsing CLI-style chat arguments."""
    sources: List[str] = field(default_factory=list)
    filters: List[str] = field(default_factory=list)
    workspace: Optional[str] = None
    browse_databases: List[str] = field(default_factory=list)
    sql_queries: List[str] = field(default_factory=list)       # -sql prompts
    vector_searches: List[str] = field(default_factory=list)    # -vs prompts
    vs_queries_structured: List[dict] = field(default_factory=list)  # [{query, top_k, threshold}]
    top_k: Optional[int] = None
    threshold: Optional[float] = None
    mcp_servers: List[str] = field(default_factory=list)
    draft_context: bool = False
    has_browse: bool = False


def parse_chat_args(args_string: str) -> ParsedChatArgs:
    """Parse a CLI-style argument string into structured components.

    This is the single argparse setup that replaces the 4+ duplicate
    ArgumentParser instances in interface.py.
    """
    from promaia.chat.recents_interface import safe_split_command

    result = ParsedChatArgs()
    args_list = safe_split_command(args_string)

    parser = argparse.ArgumentParser(description="Chat arguments", add_help=False)
    parser.add_argument("--source", "-s", action="append", dest="sources")
    parser.add_argument("--filter", "-f", action="append", dest="filters")
    parser.add_argument("--workspace", "-ws", dest="workspace")
    parser.add_argument("--browse", "-b", action="append", nargs="*", dest="browse")
    parser.add_argument("--sql-query", "-sql", action="append", nargs="+", dest="sql_query")
    parser.add_argument("-nl", action="append", nargs="+", dest="sql_query", help=argparse.SUPPRESS)
    parser.add_argument("--vector-search", "-vs", action="append", nargs="+", dest="vector_search")
    parser.add_argument("--top-k", "-tk", type=int)
    parser.add_argument("--threshold", "-th", type=float)
    parser.add_argument("--mcp", "-mcp", action="append", dest="mcp_servers")
    parser.add_argument("-dc", "--draft-context", action="store_true", dest="draft_context")

    parsed = parser.parse_args(args_list)

    result.sources = parsed.sources or []
    result.filters = parsed.filters or []
    result.workspace = parsed.workspace
    result.draft_context = parsed.draft_context
    result.mcp_servers = parsed.mcp_servers or []
    result.top_k = parsed.top_k
    result.threshold = parsed.threshold

    # Flatten browse lists: [['trass'], ['trass.tg']] -> ['trass', 'trass.tg']
    raw_browse = parsed.browse or []
    for item in raw_browse:
        if isinstance(item, list):
            result.browse_databases.extend(item)
        else:
            result.browse_databases.append(item)
    result.has_browse = bool(result.browse_databases)

    # Flatten sql query lists
    sql_raw = parsed.sql_query or []
    result.sql_queries = [' '.join(nl_args) for nl_args in sql_raw if nl_args]

    # Flatten vector search lists
    vs_raw = parsed.vector_search or []
    result.vector_searches = [' '.join(vs_args) for vs_args in vs_raw if vs_args]

    # Parse structured VS queries with per-query parameters
    if result.vector_searches:
        from promaia.utils.query_parsing import parse_vs_queries_with_params
        command_with_prefix = ['maia', 'chat'] + args_list
        result.vs_queries_structured = parse_vs_queries_with_params(command_with_prefix)

    return result


# ---------------------------------------------------------------------------
# Browse scope resolution
# ---------------------------------------------------------------------------

@dataclass
class BrowseScope:
    """Resolved browse scope for a browser operation."""
    workspace: Optional[str] = None
    multiple_workspaces: List[str] = field(default_factory=list)
    database_filter: Optional[List[str]] = None
    default_days: Optional[int] = None
    browse_scope_db_names: Set[str] = field(default_factory=set)


def resolve_browse_scope(
    browse_databases: Optional[List[str]] = None,
    workspace: Optional[str] = None,
    context_state: Optional[Dict[str, Any]] = None,
) -> BrowseScope:
    """Determine which databases are in scope for a browse operation.

    Replaces the duplicated scope resolution in handle_browse_in_edit_context,
    handle_manual_browse_edit, and /browser-inline handler.
    """
    from promaia.config.databases import get_database_manager
    from promaia.config.workspaces import get_workspace_manager

    scope = BrowseScope()
    db_manager = get_database_manager()
    workspace_manager = get_workspace_manager()

    if browse_databases:
        # Extract workspace names and database names from browse args
        workspace_names_found = []
        database_names = []

        for browse_spec in browse_databases:
            base_name = browse_spec.split(':')[0]
            if workspace_manager.validate_workspace(base_name):
                if base_name not in workspace_names_found:
                    workspace_names_found.append(base_name)
            elif '.' in base_name:
                potential_ws = base_name.split('.')[0]
                if workspace_manager.validate_workspace(potential_ws):
                    if potential_ws not in workspace_names_found:
                        workspace_names_found.append(potential_ws)
                database_names.append(browse_spec)
            else:
                database_names.append(browse_spec)

        if len(workspace_names_found) > 1:
            scope.workspace = None
            scope.multiple_workspaces = workspace_names_found
        elif len(workspace_names_found) == 1:
            scope.workspace = workspace_names_found[0]

        # Only set database_filter if there are specific databases (not just workspace names)
        if database_names:
            scope.database_filter = database_names
        # If only workspace names, let browser show all databases in those workspaces

        # Extract default_days from first browse spec with days
        for browse_spec in browse_databases:
            if ':' in browse_spec:
                try:
                    days = int(browse_spec.rsplit(':', 1)[1])
                    scope.default_days = days
                    break
                except ValueError:
                    continue

    # Fallback workspace from context
    if scope.workspace is None and not scope.multiple_workspaces:
        if context_state:
            scope.workspace = context_state.get('resolved_workspace') or context_state.get('workspace')
        if scope.workspace is None:
            scope.workspace = workspace_manager.get_default_workspace()

    # Build browse_scope_db_names set
    if scope.database_filter:
        for filter_item in scope.database_filter:
            filter_base = filter_item.split(':')[0]
            scope.browse_scope_db_names.add(filter_base)
            if workspace_manager.validate_workspace(filter_base):
                for db in db_manager.get_workspace_databases(filter_base):
                    scope.browse_scope_db_names.add(db.get_qualified_name())
    elif scope.multiple_workspaces:
        for ws in scope.multiple_workspaces:
            for db in db_manager.get_workspace_databases(ws):
                scope.browse_scope_db_names.add(db.get_qualified_name())
    elif scope.workspace:
        for db in db_manager.get_workspace_databases(scope.workspace):
            scope.browse_scope_db_names.add(db.get_qualified_name())

    return scope


# ---------------------------------------------------------------------------
# Browser result application
# ---------------------------------------------------------------------------

def apply_browser_result(
    selected_sources: List[str],
    context_state: Dict[str, Any],
    browse_scope_db_names: Set[str],
) -> Tuple[List[str], List[str]]:
    """Apply browser selections to context_state, merging with out-of-scope sources.

    Args:
        selected_sources: Source specs from browser (e.g., ["koii.journal:7"])
        context_state: The mutable context state dict
        browse_scope_db_names: Set of database names in the current browse scope

    Returns:
        (final_sources, processed_filters) after merging with out-of-scope sources.
    """
    # Import here to avoid circular imports
    from promaia.chat.interface import process_browser_selections

    processed_sources, processed_filters = process_browser_selections(selected_sources)

    # Keep original sources that are OUTSIDE the browse scope
    original_sources = context_state.get('sources', []) or []
    final_sources = []
    for src in original_sources:
        src_base = src.split(':')[0]
        if src_base not in browse_scope_db_names:
            final_sources.append(src)

    # Add new selections
    final_sources.extend(processed_sources)

    # Update context
    context_state['sources'] = final_sources
    context_state['filters'] = processed_filters
    context_state['browse_selections'] = selected_sources.copy()

    return final_sources, processed_filters
