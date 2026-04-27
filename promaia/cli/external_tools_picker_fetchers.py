"""
Fetch callbacks that populate ChildNode lists for the unified
external-tools picker.

Each fetcher takes a ToolNode and returns the children that should
appear under it once expanded. Called lazily by the picker — only on
first expand of a row.

The dispatcher `make_fetch_children(workspace, agent)` returns a single
async callable that routes based on the tool's id / shape:

- notion / google_sheets: configured sources from the database manager
  filtered by source_type
- slack / discord: live-fetched channels via bot token (with two
  wildcard rows on top)
- mcp_server: tool list from mcp_tool_cache (refreshes if missing)
- gmail / calendar (single_row): never expanded; returns []
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

from promaia.cli.external_tools_picker_state import ChildNode, ToolNode


# ---------------------------------------------------------------------------
# Per-tool fetchers
# ---------------------------------------------------------------------------


async def _fetch_notion_children(workspace: str, agent: Any, tool: ToolNode) -> List[ChildNode]:
    """Configured Notion sources for the workspace, with R/W pre-state from agent."""
    return await _fetch_source_children(workspace, agent, tool, source_type="notion")


async def _fetch_sheets_children(workspace: str, agent: Any, tool: ToolNode) -> List[ChildNode]:
    return await _fetch_source_children(workspace, agent, tool, source_type="google_sheets")


async def _fetch_source_children(
    workspace: str, agent: Any, tool: ToolNode, source_type: str
) -> List[ChildNode]:
    """Generic source-list fetcher — used by Notion + Sheets.

    Reads configured sources from the database manager, filters by
    source_type, and pre-fills R/W state from the agent's existing
    `databases` + `source_access`.
    """
    try:
        from promaia.config.databases import get_database_manager
    except Exception:
        return []

    db_manager = get_database_manager()
    workspace_dbs = db_manager.get_workspace_databases(workspace)

    children: List[ChildNode] = []
    agent_databases = set(getattr(agent, "databases", None) or [])
    source_access = getattr(agent, "source_access", None) or []
    sa_by_name = {sa.source_name: sa for sa in source_access}

    for db_config in workspace_dbs:
        if getattr(db_config, "source_type", None) != source_type:
            continue
        nickname = getattr(db_config, "nickname", None) or ""
        # source key in databases / source_access uses just the nickname
        in_db = nickname in agent_databases or any(
            d.split(":", 1)[0] == nickname for d in agent_databases
        )
        sa = sa_by_name.get(nickname)
        r = in_db
        w = False
        if sa is not None:
            try:
                from promaia.agents.agent_config import SourcePermission
                r = SourcePermission.QUERY in sa.permissions or r
                w = SourcePermission.WRITE in sa.permissions
            except Exception:
                pass
        children.append(ChildNode(
            id=nickname,
            label=nickname,
            parent_id=tool.id,
            enabled=in_db or w,
            r_state=r,
            w_state=w,
            has_w_column=True,
        ))
    return children


async def _fetch_slack_children(workspace: str, agent: Any, tool: ToolNode) -> List[ChildNode]:
    """Wildcard rows + live-fetched Slack channels with R/W pre-state."""
    from promaia.cli.agent_creation_selector import _fetch_slack_channels

    raw = await _fetch_slack_channels(workspace) or []
    groups_in = getattr(agent, "allowed_channel_groups", None) or {}
    groups_out = getattr(agent, "allowed_output_channel_groups", None) or {}

    def _is_in_group(channel_id: str, kind: str, groups: dict) -> bool:
        if not groups:
            return False
        bucket = groups.get(kind, [])
        return "*" in bucket or channel_id in bucket

    children: List[ChildNode] = [
        ChildNode(
            id="__wildcard_dm__",
            label="✱ Any DM",
            parent_id=tool.id,
            is_wildcard=True,
            enabled=("*" in (groups_in.get("dm") or [])) or ("*" in (groups_out.get("dm") or [])),
            r_state="*" in (groups_in.get("dm") or []),
            w_state="*" in (groups_out.get("dm") or []),
            has_w_column=True,
        ),
        ChildNode(
            id="__wildcard_channel__",
            label="✱ Any non-DM channel",
            parent_id=tool.id,
            is_wildcard=True,
            enabled=("*" in (groups_in.get("channel") or [])) or ("*" in (groups_out.get("channel") or [])),
            r_state="*" in (groups_in.get("channel") or []),
            w_state="*" in (groups_out.get("channel") or []),
            has_w_column=True,
        ),
    ]

    for ch_id, ch_name, _is_member in raw:
        kind = "dm" if ch_id.startswith("D") else "channel"
        r = _is_in_group(ch_id, kind, groups_in)
        w = _is_in_group(ch_id, kind, groups_out)
        children.append(ChildNode(
            id=ch_id,
            label=f"#{ch_name}",
            parent_id=tool.id,
            enabled=r or w,
            r_state=r,
            w_state=w,
            has_w_column=True,
        ))
    return children


async def _fetch_discord_children(workspace: str, agent: Any, tool: ToolNode) -> List[ChildNode]:
    """Discord channels — same shape as Slack but Discord doesn't have a
    standardised list-channels API call wired in this repo today.

    For v1: return wildcard-only rows. Real Discord channel discovery
    plugs in once the helper exists.
    """
    groups_in = getattr(agent, "allowed_channel_groups", None) or {}
    groups_out = getattr(agent, "allowed_output_channel_groups", None) or {}
    return [
        ChildNode(
            id="__wildcard_dm__",
            label="✱ Any DM",
            parent_id=tool.id,
            is_wildcard=True,
            enabled=("*" in (groups_in.get("dm") or [])) or ("*" in (groups_out.get("dm") or [])),
            r_state="*" in (groups_in.get("dm") or []),
            w_state="*" in (groups_out.get("dm") or []),
            has_w_column=True,
        ),
        ChildNode(
            id="__wildcard_channel__",
            label="✱ Any non-DM channel",
            parent_id=tool.id,
            is_wildcard=True,
            enabled=("*" in (groups_in.get("channel") or [])) or ("*" in (groups_out.get("channel") or [])),
            r_state="*" in (groups_in.get("channel") or []),
            w_state="*" in (groups_out.get("channel") or []),
            has_w_column=True,
        ),
    ]


async def _fetch_mcp_children(workspace: str, agent: Any, tool: ToolNode) -> List[ChildNode]:
    """MCP tool list for *tool.id* (the server name) — from cache or live refresh."""
    try:
        from promaia.agents import mcp_tool_cache
        from promaia.config.mcp_servers import McpServerManager
        from promaia.agents.mcp_loader import _find_mcp_servers_json
    except Exception:
        return []

    config_path = _find_mcp_servers_json()
    if config_path is None:
        return []
    manager = McpServerManager(str(config_path))
    cfg = manager.servers.get(tool.id)
    if cfg is None:
        return []
    try:
        snap = await mcp_tool_cache.get_or_refresh(tool.id, cfg)
    except Exception:
        snap = mcp_tool_cache.load(tool.id)
        if snap is None:
            return []

    allowlist = getattr(agent, "mcp_tool_allowlist", None) or {}
    granted_for_server = allowlist.get(tool.id)  # may be None (wholesale grant) or list

    def _is_granted(tname: str) -> bool:
        if granted_for_server is None:
            # Wholesale-grant marker: every tool allowed iff server is in allowlist dict
            return tool.id in allowlist
        return tname in granted_for_server

    return [
        ChildNode(
            id=t.name,
            label=t.name,
            parent_id=tool.id,
            enabled=_is_granted(t.name),
            r_state=_is_granted(t.name),  # reuse the R column for "allowed"
            w_state=False,
            has_w_column=False,  # MCP per-tool is allow/deny only
        )
        for t in snap.tools
    ]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def make_fetch_children(workspace: str, agent: Any) -> Callable:
    """Return an async fetch_children callback for the picker.

    Routes to the right per-tool fetcher based on the ToolNode's id /
    shape. Single-row tools (gmail, calendar) never expand and so
    return [] (the picker won't call this for them anyway).
    """

    async def fetch(tool: ToolNode) -> List[ChildNode]:
        if tool.shape == "single_row":
            return []
        if tool.shape == "mcp_server":
            return await _fetch_mcp_children(workspace, agent, tool)
        if tool.id == "notion":
            return await _fetch_notion_children(workspace, agent, tool)
        if tool.id == "google_sheets":
            return await _fetch_sheets_children(workspace, agent, tool)
        if tool.id == "slack":
            return await _fetch_slack_children(workspace, agent, tool)
        if tool.id == "discord":
            return await _fetch_discord_children(workspace, agent, tool)
        return []

    return fetch
