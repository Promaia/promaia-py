"""
Cached snapshot of "what tools the user reviewed last time they edited
the agent" — NOT a freshness mirror of what the server currently has.

Per-server snapshot lives at ``<data_dir>/cache/mcp_tools/<server>.json``::

    {
        "server": "po_manager",
        "fetched_at": "2026-04-26T19:10:00+00:00",
        "tools": [
            {"name": "list_vendors", "description": "...",
             "schema_fingerprint": "<sha256[:12]>"},
            ...
        ]
    }

Two consumers:

1. **Agent-edit interview / UI** — when the user configures
   ``mcp_tool_allowlist`` for a server, the UI calls
   ``refresh_from_server`` once to pull the current tool list, displays
   it for the user to pick from, and on save writes the snapshot. This
   is the only writer.

2. **Runtime permission gate** — agents already open an MCP connection
   at run start; we list tools on that existing connection (one extra
   round-trip, no scheduler job) and ``diff()`` against this cached
   snapshot. The diff tells us:
     - new tools: deny by default; log "X new tools available — review
       with maia agent edit"
     - removed tools: ignore (allow list naturally ignores absent tools)
     - changed schema fingerprint: deferred (Q5d, internal-MCP-only for
       now); see ``schema_fingerprint`` field for the foundation.

The cache is intentionally NEVER refreshed at runtime. Stale-by-design
is the right semantic: the user's permission decisions were made against
*this* tool list, and we should keep evaluating against it until they
explicitly review.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How long a cached entry is considered "fresh" by default. Callers may
# override per-call; the scheduler refresh job will keep this below 24h.
DEFAULT_STALE_AFTER = timedelta(hours=24)


@dataclass
class CachedTool:
    name: str
    description: str
    schema_fingerprint: str

    @classmethod
    def from_mcp_tool(cls, tool: Dict[str, Any]) -> "CachedTool":
        return cls(
            name=tool.get("name", ""),
            description=tool.get("description", "") or "",
            schema_fingerprint=_fingerprint_schema(tool.get("inputSchema") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "schema_fingerprint": self.schema_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CachedTool":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            schema_fingerprint=d.get("schema_fingerprint", ""),
        )


@dataclass
class ServerCache:
    """In-memory representation of one cache file."""

    server: str
    fetched_at: datetime
    tools: List[CachedTool] = field(default_factory=list)

    @property
    def tool_names(self) -> List[str]:
        return [t.name for t in self.tools]

    def is_fresh(self, stale_after: timedelta = DEFAULT_STALE_AFTER) -> bool:
        return (datetime.now(timezone.utc) - self.fetched_at) < stale_after

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server": self.server,
            "fetched_at": self.fetched_at.isoformat(),
            "tools": [t.to_dict() for t in self.tools],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ServerCache":
        return cls(
            server=d["server"],
            fetched_at=_parse_iso(d.get("fetched_at")),
            tools=[CachedTool.from_dict(t) for t in d.get("tools", [])],
        )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_cache_path(server: str) -> Path:
    """Resolve the cache file path for *server* via env_writer (never raw env)."""
    from promaia.utils.env_writer import get_data_dir

    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in server)
    return get_data_dir() / "cache" / "mcp_tools" / f"{safe}.json"


def load(server: str) -> Optional[ServerCache]:
    """Load the cache for *server*, or None if no cache exists."""
    path = get_cache_path(server)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return ServerCache.from_dict(data)
    except (OSError, ValueError, KeyError) as e:
        logger.warning("Failed to read mcp tool cache %s: %s", path, e)
        return None


def save(cache: ServerCache) -> None:
    path = get_cache_path(cache.server)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache.to_dict(), f, indent=2)
    tmp.replace(path)


async def refresh_from_server(
    server_name: str,
    server_config,
    timeout: int = 30,
) -> ServerCache:
    """Connect, list_tools, persist, return the new cache.

    *server_config* is an ``McpServerConfig`` from
    ``promaia.config.mcp_servers``. Caller is responsible for handling
    connection errors — we propagate them up.
    """
    from promaia.mcp.protocol import McpProtocolClient

    client = McpProtocolClient()
    connect_kwargs: Dict[str, Any] = {
        "transport": server_config.transport,
        "timeout": timeout,
    }
    if server_config.transport == "streamable_http":
        connect_kwargs["url"] = server_config.url
        if server_config.env:
            connect_kwargs["headers"] = server_config.get_resolved_env()
    else:
        connect_kwargs["command"] = server_config.command
        connect_kwargs["args"] = server_config.args
        connect_kwargs["working_dir"] = server_config.working_dir
        connect_kwargs["env"] = server_config.get_resolved_env()

    ok = await client.connect(**connect_kwargs)
    if not ok:
        await client.disconnect()
        raise RuntimeError(f"Could not connect to MCP server {server_name!r}")

    try:
        tools = await client.list_tools() or []
    finally:
        await client.disconnect()

    cache = ServerCache(
        server=server_name,
        fetched_at=datetime.now(timezone.utc),
        tools=[CachedTool.from_mcp_tool(t) for t in tools],
    )
    save(cache)
    return cache


async def get_or_refresh(
    server_name: str,
    server_config,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    timeout: int = 30,
) -> ServerCache:
    """Return a cache for *server_name*, refreshing if missing or stale."""
    existing = load(server_name)
    if existing is not None and existing.is_fresh(stale_after):
        return existing
    try:
        return await refresh_from_server(server_name, server_config, timeout=timeout)
    except Exception as e:
        if existing is not None:
            logger.warning(
                "MCP refresh failed for %s (%s); serving stale cache from %s",
                server_name,
                e,
                existing.fetched_at.isoformat(),
            )
            return existing
        raise


def diff(old: Optional[ServerCache], new: ServerCache) -> Dict[str, List[str]]:
    """Return added / removed / changed tool-name lists vs. the previous cache."""
    if old is None:
        return {"added": new.tool_names, "removed": [], "changed": []}
    old_by = {t.name: t for t in old.tools}
    new_by = {t.name: t for t in new.tools}
    added = [n for n in new_by if n not in old_by]
    removed = [n for n in old_by if n not in new_by]
    changed = [
        n
        for n in new_by
        if n in old_by and old_by[n].schema_fingerprint != new_by[n].schema_fingerprint
    ]
    return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _fingerprint_schema(schema: Dict[str, Any]) -> str:
    """Stable short hash of a tool's input schema.

    Used to detect when a tool's surface area has changed since the user
    last reviewed permissions. JSON-canonical serialization keeps the
    fingerprint stable across dict ordering jitter.
    """
    blob = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def _parse_iso(s: Optional[str]) -> datetime:
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
