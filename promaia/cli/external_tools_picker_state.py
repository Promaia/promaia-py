"""
State model for the unified external-tools picker.

The picker (Phase 2b) drives a tree of these nodes; this module
provides the dataclass shapes and the helpers that compute the
initial state from an existing AgentConfig (which gates are
already set, which sources/channels have R/W today, etc.).

No UI here — pure data. Keeping this separate makes the model
unit-testable without prompt_toolkit in the loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from promaia.cli.builtin_tools_registry import (
    BUILTIN_TOOLS,
    BuiltinTool,
    get_builtin_tool,
    is_builtin_tool,
)


# ---------------------------------------------------------------------------
# Node shapes
# ---------------------------------------------------------------------------


@dataclass
class ToolNode:
    """A top-level row in the picker tree.

    Built-ins map 1:1 to a BuiltinTool entry; the row's `shape` (single_row
    / sublist / channel_sublist) decides whether children are rendered.
    MCP servers are also top-level rows; their shape is a synthetic
    `mcp_server` value with children = the per-tool checkbox list.
    """

    id: str  # e.g. "notion", "slack", "po-manager"
    label: str
    shape: str  # "single_row" | "sublist" | "channel_sublist" | "mcp_server"
    description: str

    # Top-level state
    enabled: bool = False  # is this tool ticked at all?
    expanded: bool = False  # has the user opened it (matters for sublist shapes)

    # For single_row shapes the R/W columns live on the tool row itself.
    # For sublist / channel_sublist / mcp_server shapes these are derived
    # from children and only used for display rollup.
    r_state: bool = False
    w_state: bool = False
    has_w_column: bool = True  # MCP servers set this to False

    children: List["ChildNode"] = field(default_factory=list)
    children_loaded: bool = False  # for lazy-load shapes (channel_sublist, mcp_server)


@dataclass
class ChildNode:
    """A child row under a ToolNode (a Notion DB, a Slack channel, an MCP tool, etc)."""

    id: str  # source name, channel id, or tool name depending on parent
    label: str
    parent_id: str

    enabled: bool = False  # child is "selected" (read column = on at minimum)
    r_state: bool = False
    w_state: bool = False
    has_w_column: bool = True  # MCP per-tool rows set this to False (allow/deny only)

    # Optional metadata used for display
    is_wildcard: bool = False  # the ✱ rows on top of channel_sublist


# ---------------------------------------------------------------------------
# Pre-state computation: AgentConfig → tree
# ---------------------------------------------------------------------------


def build_initial_tree(
    agent: Any,  # AgentConfig — Any to avoid circular imports
    mcp_server_names: Optional[List[str]] = None,
) -> List[ToolNode]:
    """Compute the picker's initial state from an existing agent config.

    Iterates the BUILTIN_TOOLS registry first (in registry order, which
    is the order shown in the picker), then appends one ToolNode per
    user-added MCP server (passed via *mcp_server_names*; the picker
    will call mcp_servers.json discovery for that list).

    Children are NOT populated here — they're loaded lazily by the
    picker when the user expands a row, via separate fetch helpers.
    What IS populated: the tool's overall enabled / R / W state where
    that's derivable from the agent's current config without hitting
    the network.
    """
    nodes: List[ToolNode] = []

    for builtin in BUILTIN_TOOLS:
        node = _node_for_builtin(agent, builtin)
        nodes.append(node)

    for mcp_name in mcp_server_names or []:
        node = _node_for_mcp_server(agent, mcp_name)
        nodes.append(node)

    return nodes


def _node_for_builtin(agent: Any, tool: BuiltinTool) -> ToolNode:
    """Build a top-level ToolNode for a built-in external tool."""
    enabled = _builtin_enabled_for_agent(agent, tool)
    # For single_row shapes the R/W is meaningful at the tool level;
    # for sublist / channel_sublist it's a roll-up that gets refreshed
    # once children are loaded.
    if tool.shape == "single_row":
        r, w = _builtin_single_row_rw(agent, tool)
    else:
        r, w = False, False  # filled in after children load
    return ToolNode(
        id=tool.id,
        label=tool.label,
        shape=tool.shape,
        description=tool.description,
        enabled=enabled,
        r_state=r,
        w_state=w,
        has_w_column=True,  # gmail/notion/etc all have a W column
    )


def _node_for_mcp_server(agent: Any, server_name: str) -> ToolNode:
    """Build a top-level ToolNode for an MCP server entry."""
    enabled = server_name in (getattr(agent, "mcp_tools", None) or [])
    return ToolNode(
        id=server_name,
        label=server_name,
        shape="mcp_server",
        description="MCP server tools",
        enabled=enabled,
        # MCP servers don't have R/W rollup; the per-tool children just
        # have an enabled column.
        r_state=False,
        w_state=False,
        has_w_column=False,
    )


# ---------------------------------------------------------------------------
# Per-tool helpers — narrow inspections of AgentConfig used to seed state
# ---------------------------------------------------------------------------


def _builtin_enabled_for_agent(agent: Any, tool: BuiltinTool) -> bool:
    """Is this built-in tool ticked at all on the agent?

    Heuristic: tool is "ticked" if any of its declared gates are
    non-default for the agent. Specifically:
    - notion / sheets: at least one entry in `databases` whose source
      type matches the tool (we can't reliably inspect type without a
      database manager call, so for v1 we approximate by name prefix).
    - gmail / calendar: presence in `databases` or in `mcp_tools`.
    - slack / discord: any non-None `allowed_channel_groups` /
      `allowed_output_channel_groups`, OR `messaging_enabled=True` for
      this platform.
    """
    databases = getattr(agent, "databases", None) or []

    if tool.id in ("gmail", "calendar"):
        return tool.id in databases or tool.id in (getattr(agent, "mcp_tools", None) or [])

    if tool.id == "notion":
        # Notion sources are typically registered with workspace-prefixed names
        # like "koii.stories" or just "stories". We can't tell without the DB
        # manager, so v1 approximation: any database that isn't gmail/calendar/
        # slack/discord/google_sheets is candidate Notion. Phase 2c can
        # tighten this with a DB-manager call.
        builtin_ids = {t.id for t in BUILTIN_TOOLS}
        for db in databases:
            short = db.split(":", 1)[0]
            if short not in builtin_ids:
                return True
        return False

    if tool.id == "google_sheets":
        return any(db.split(":", 1)[0] == "google_sheets" for db in databases)

    if tool.id in ("slack", "discord"):
        # Heuristic: ticked iff messaging is enabled OR channel groups are set
        # OR there's a slack/discord entry in `databases`.
        if getattr(agent, "messaging_enabled", False):
            return True
        if getattr(agent, "allowed_channel_groups", None):
            return True
        if tool.id in databases:
            return True
        return False

    return False


def _builtin_single_row_rw(agent: Any, tool: BuiltinTool) -> Tuple[bool, bool]:
    """For single_row tools (gmail, calendar), determine R and W state."""
    databases = getattr(agent, "databases", None) or []
    source_access = getattr(agent, "source_access", None) or []

    # Find SourceAccess entry for this tool
    sa = None
    for entry in source_access:
        if entry.source_name == tool.id:
            sa = entry
            break

    # R = readable iff in databases (legacy) OR explicit QUERY in source_access
    r = tool.id in databases
    if sa is not None:
        from promaia.agents.agent_config import SourcePermission
        r = SourcePermission.QUERY in sa.permissions or r

    # W = writable iff explicit WRITE in source_access
    w = False
    if sa is not None:
        from promaia.agents.agent_config import SourcePermission
        w = SourcePermission.WRITE in sa.permissions

    # Gmail W also requires messaging_enabled to actually do anything,
    # but that's a side-effect; the R/W picker just reflects source_access.
    return r, w


# ---------------------------------------------------------------------------
# Tree → AgentConfig diff (used by Phase 3 routing)
# ---------------------------------------------------------------------------


def collect_picker_result(nodes: List[ToolNode]) -> Dict[str, Any]:
    """Flatten the tree into a routing-friendly dict the caller can pass
    to per-tool updaters.

    Shape:
        {
          "<tool_id>": {
              "enabled": bool,
              "r": bool,                # for single_row tools
              "w": bool,                # for single_row tools
              "children": [
                  {"id": "...", "r": bool, "w": bool, "enabled": bool}
              ],
          },
          ...
        }
    """
    out: Dict[str, Any] = {}
    for node in nodes:
        out[node.id] = {
            "shape": node.shape,
            "enabled": node.enabled,
            "r": node.r_state,
            "w": node.w_state,
            "children": [
                {
                    "id": c.id,
                    "label": c.label,
                    "enabled": c.enabled,
                    "r": c.r_state,
                    "w": c.w_state,
                }
                for c in node.children
            ],
        }
    return out
