"""
Routes the unified picker's result dict back into AgentConfig fields.

The picker returns a structure like:

    {
      "<tool_id>": {
        "shape": "single_row" | "sublist" | "channel_sublist" | "mcp_server",
        "enabled": bool,
        "r": bool,
        "w": bool,
        "children": [{"id": ..., "label": ..., "enabled": ..., "r": ..., "w": ...}],
      },
      ...
    }

Each shape maps to different AgentConfig fields. This module owns
that mapping. Mutates *agent* in place; caller is responsible for
calling save_agent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


def apply_picker_result(agent: Any, result: Dict[str, Any]) -> List[str]:
    """Apply the picker's result to *agent*. Returns a list of human-readable
    change strings suitable for confirmation output."""
    changes: List[str] = []

    for tool_id, tool_result in result.items():
        shape = tool_result.get("shape")
        try:
            if shape == "single_row":
                ch = _apply_single_row(agent, tool_id, tool_result)
            elif shape == "sublist":
                ch = _apply_sublist(agent, tool_id, tool_result)
            elif shape == "channel_sublist":
                ch = _apply_channel_sublist(agent, tool_id, tool_result)
            elif shape == "mcp_server":
                ch = _apply_mcp_server(agent, tool_id, tool_result)
            else:
                continue
            changes.extend(ch)
        except Exception as e:
            changes.append(f"⚠ {tool_id}: skipped ({e})")

    return changes


# ---------------------------------------------------------------------------
# single_row: gmail, calendar
# ---------------------------------------------------------------------------


def _apply_single_row(agent: Any, tool_id: str, result: Dict[str, Any]) -> List[str]:
    enabled = result.get("enabled", False)
    r = result.get("r", False)
    w = result.get("w", False)

    databases = list(getattr(agent, "databases", None) or [])
    short_names = {d.split(":", 1)[0] for d in databases}

    if not enabled:
        # Remove from databases + drop source_access entry
        databases = [d for d in databases if d.split(":", 1)[0] != tool_id]
        agent.databases = databases
        _remove_source_access(agent, tool_id)
        if tool_id == "gmail":
            agent.messaging_enabled = False
        return [f"{tool_id}: removed"]

    # Enabled — ensure in databases
    if tool_id not in short_names:
        databases.append(tool_id)
        agent.databases = databases

    # Update source_access permissions
    perms = []
    if r:
        from promaia.agents.agent_config import SourcePermission
        perms.append(SourcePermission.QUERY)
    if w:
        from promaia.agents.agent_config import SourcePermission
        perms.append(SourcePermission.WRITE)
    _set_source_access(agent, tool_id, perms)

    # Gmail W also flips messaging_enabled
    if tool_id == "gmail":
        agent.messaging_enabled = bool(w)

    label = "R+W" if (r and w) else ("R-only" if r else ("W-only" if w else "off"))
    return [f"{tool_id}: {label}"]


# ---------------------------------------------------------------------------
# sublist: notion, google_sheets (children = configured sources)
# ---------------------------------------------------------------------------


def _apply_sublist(agent: Any, tool_id: str, result: Dict[str, Any]) -> List[str]:
    children = result.get("children", [])
    databases = list(getattr(agent, "databases", None) or [])

    # Drop any existing entries this tool would own — we rewrite from the
    # picker's state. v1 detection is by short-name match with each child.
    child_ids = {c["id"] for c in children}
    for c in children:
        cid = c["id"]
        if c.get("enabled") or c.get("r") or c.get("w"):
            if cid not in {d.split(":", 1)[0] for d in databases}:
                databases.append(cid)
        else:
            databases = [d for d in databases if d.split(":", 1)[0] != cid]

    agent.databases = databases

    # Update source_access per child
    enabled_count = 0
    rw_count = 0
    for c in children:
        cid = c["id"]
        r = c.get("r", False)
        w = c.get("w", False)
        if not (r or w):
            _remove_source_access(agent, cid)
            continue
        from promaia.agents.agent_config import SourcePermission
        perms = []
        if r:
            perms.append(SourcePermission.QUERY)
        if w:
            perms.append(SourcePermission.WRITE)
        _set_source_access(agent, cid, perms)
        enabled_count += 1
        if r and w:
            rw_count += 1

    return [f"{tool_id}: {enabled_count} source(s), {rw_count} R+W"]


# ---------------------------------------------------------------------------
# channel_sublist: slack, discord
# ---------------------------------------------------------------------------


def _apply_channel_sublist(agent: Any, tool_id: str, result: Dict[str, Any]) -> List[str]:
    children = result.get("children", [])
    in_dm: List[str] = []
    in_ch: List[str] = []
    out_dm: List[str] = []
    out_ch: List[str] = []
    write_seen = False

    for c in children:
        cid = c["id"]
        r = c.get("r", False)
        w = c.get("w", False)
        if cid == "__wildcard_dm__":
            if r:
                in_dm = ["*"]
            if w:
                out_dm = ["*"]
                write_seen = True
            continue
        if cid == "__wildcard_channel__":
            if r:
                in_ch = ["*"]
            if w:
                out_ch = ["*"]
                write_seen = True
            continue
        # Concrete channel
        kind = "dm" if cid.startswith("D") else "channel"
        if r:
            (in_dm if kind == "dm" else in_ch).append(cid)
        if w:
            (out_dm if kind == "dm" else out_ch).append(cid)
            write_seen = True

    in_groups = {"dm": in_dm, "channel": in_ch}
    out_groups = {"dm": out_dm, "channel": out_ch}

    # Empty groups → None (= no restriction; legacy allow). The picker user
    # has to pick at least one row to constrain anything.
    agent.allowed_channel_groups = in_groups if (in_dm or in_ch) else None
    agent.allowed_output_channel_groups = out_groups if (out_dm or out_ch) else None

    # Clear legacy flat lists when groups are set, so resolution order
    # doesn't surprise.
    if agent.allowed_channel_groups is not None:
        agent.allowed_channel_ids = None
    if agent.allowed_output_channel_groups is not None:
        agent.allowed_output_channel_ids = None

    if write_seen:
        agent.messaging_enabled = True

    summary = f"{tool_id}: {sum(1 for c in children if c.get('r'))} read"
    summary += f", {sum(1 for c in children if c.get('w'))} write"
    return [summary]


# ---------------------------------------------------------------------------
# mcp_server: per-tool allow list
# ---------------------------------------------------------------------------


def _apply_mcp_server(agent: Any, server_name: str, result: Dict[str, Any]) -> List[str]:
    enabled = result.get("enabled", False)
    children = result.get("children", [])

    mcp_tools = list(getattr(agent, "mcp_tools", None) or [])
    allowlist = dict(getattr(agent, "mcp_tool_allowlist", None) or {})

    if not enabled:
        # Remove server from mcp_tools and from allowlist
        if server_name in mcp_tools:
            mcp_tools = [m for m in mcp_tools if m != server_name]
        allowlist.pop(server_name, None)
        agent.mcp_tools = mcp_tools
        agent.mcp_tool_allowlist = allowlist or None
        return [f"{server_name}: removed"]

    # Enabled — ensure in mcp_tools
    if server_name not in mcp_tools:
        mcp_tools.append(server_name)
    agent.mcp_tools = mcp_tools

    # Build per-tool allow list from children
    granted = [c["id"] for c in children if c.get("enabled") or c.get("r")]
    allowlist[server_name] = granted
    agent.mcp_tool_allowlist = allowlist

    return [f"{server_name}: {len(granted)} tool(s)"]


# ---------------------------------------------------------------------------
# source_access helpers
# ---------------------------------------------------------------------------


def _set_source_access(agent: Any, source_name: str, permissions: list) -> None:
    """Upsert a SourceAccess entry for *source_name* with the given permissions."""
    from promaia.agents.agent_config import SourceAccess

    existing = list(getattr(agent, "source_access", None) or [])
    found = False
    for sa in existing:
        if sa.source_name == source_name:
            sa.permissions = permissions
            found = True
            break
    if not found:
        existing.append(SourceAccess(
            source_name=source_name,
            initial_days=None,
            permissions=permissions,
        ))
    agent.source_access = existing


def _remove_source_access(agent: Any, source_name: str) -> None:
    existing = list(getattr(agent, "source_access", None) or [])
    new = [sa for sa in existing if sa.source_name != source_name]
    agent.source_access = new if new else None
