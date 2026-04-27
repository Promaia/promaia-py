"""
Registry of built-in external tools the agent-permissions picker
recognises.

Each entry maps a tool's stable id (used in `mcp_tools` lists today)
to its display label, picker shape, and the underlying gates the
picker writes to.

The picker is the only consumer right now. Other code paths still
read the gate fields directly (databases, source_access,
allowed_channel_groups, mcp_tool_allowlist, etc.) — this registry
doesn't replace any of them, it's just a lookup table for the
unified picker's per-tool routing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


# Picker shapes:
#   "single_row"       — one row in the tree, R/W columns directly on it.
#                        Used by tools with no per-resource sub-list (gmail,
#                        calendar). Toggling W typically also flips an
#                        ancillary flag like messaging_enabled.
#   "sublist"          — top row expands to a list of sources (the agent's
#                        existing notion / sheets entries from `databases`).
#                        Each child row has R/W columns. Notion + Sheets.
#   "channel_sublist"  — like "sublist" but children are live-fetched
#                        Slack/Discord channels. Includes wildcard rows
#                        on top (✱ Any DM, ✱ Any non-DM channel).
PICKER_SHAPES = {"single_row", "sublist", "channel_sublist"}


@dataclass(frozen=True)
class BuiltinTool:
    """A built-in external tool the agent can be granted."""

    id: str
    label: str
    shape: str  # one of PICKER_SHAPES
    gates: Tuple[str, ...]  # field names on AgentConfig that this tool's settings touch
    description: str  # one-line summary shown in the picker

    def __post_init__(self):
        if self.shape not in PICKER_SHAPES:
            raise ValueError(
                f"Unknown picker shape {self.shape!r} for tool {self.id!r}; "
                f"expected one of {sorted(PICKER_SHAPES)}"
            )
        if not self.gates:
            raise ValueError(f"Tool {self.id!r} must declare at least one gate field")


# Order here is the order the picker shows them in. Keep stable —
# users develop muscle memory for which row is which.
BUILTIN_TOOLS: Tuple[BuiltinTool, ...] = (
    BuiltinTool(
        id="notion",
        label="Notion",
        shape="sublist",
        gates=("databases", "source_access"),
        description="read DBs + write pages",
    ),
    BuiltinTool(
        id="gmail",
        label="Gmail",
        shape="single_row",
        gates=("databases", "source_access", "messaging_enabled"),
        description="read inbox + send replies",
    ),
    BuiltinTool(
        id="calendar",
        label="Calendar",
        shape="single_row",
        gates=("databases", "source_access"),
        description="read events + write events",
    ),
    BuiltinTool(
        id="google_sheets",
        label="Google Sheets",
        shape="sublist",
        gates=("databases", "source_access"),
        description="read sheets + write rows",
    ),
    BuiltinTool(
        id="slack",
        label="Slack",
        shape="channel_sublist",
        gates=(
            "databases",
            "allowed_channel_groups",
            "allowed_output_channel_groups",
            "messaging_enabled",
        ),
        description="read channels + post",
    ),
    BuiltinTool(
        id="discord",
        label="Discord",
        shape="channel_sublist",
        gates=(
            "databases",
            "allowed_channel_groups",
            "allowed_output_channel_groups",
            "messaging_enabled",
        ),
        description="read channels + post",
    ),
)


_BY_ID = {t.id: t for t in BUILTIN_TOOLS}


def is_builtin_tool(name: str) -> bool:
    """Is *name* one of the built-in external tools the picker recognises?

    Anything else in `mcp_tools` is assumed to be a user-added MCP server
    (configured in `mcp_servers.json`).
    """
    return name in _BY_ID


def get_builtin_tool(name: str) -> BuiltinTool:
    """Return the BuiltinTool entry for *name*. Raises KeyError if unknown."""
    return _BY_ID[name]


def get_tool_shape(name: str) -> str:
    """Return the picker shape for *name*: 'single_row', 'sublist', or 'channel_sublist'."""
    return _BY_ID[name].shape


def get_tool_label(name: str) -> str:
    """Return the human-readable label for *name*."""
    return _BY_ID[name].label
