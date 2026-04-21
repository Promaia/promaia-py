"""Chunked vectorization of Slack DM conversations.

Embeds ongoing DM threads from the live conversations.db into ChromaDB so the
agent's query_vector tool can semantic-search conversation history. Chunks are
12-message cores with 2 messages of overlap on each side for retrieval recall.

Live ingestion: a fire-and-forget task calls `vectorize_conversation_incremental`
after each turn; once the message count crosses a chunk boundary, the new full
chunk is embedded. Conversation dormancy flushes any trailing partial chunk.

Retroactive ingestion: `backfill_all` walks every Slack conversation in
conversations.db and embeds every chunk up to the current state.

Incognito: any ConversationState with `context["incognito"] is True` is skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

CHUNK_SIZE = 12
OVERLAP = 2
DATABASE_NAME = "slack_dms"
DATABASE_ID = "slack_dms"

# Serialize writes per conversation so overlapping turn completions don't
# race on the same chunk boundary.
_conv_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(conversation_id: str) -> asyncio.Lock:
    lock = _conv_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _conv_locks[conversation_id] = lock
    return lock


def compute_pending_chunks(
    total_messages: int,
    chunks_done: int,
    include_partial: bool,
) -> List[Tuple[int, int, int, int]]:
    """Return the list of (core_start, core_end, window_start, window_end) tuples to embed.

    - Full chunks are emitted as long as enough messages exist past `chunks_done`.
    - If `include_partial` is True and there are leftover messages shorter than
      CHUNK_SIZE, emit one partial trailing chunk (used on dormancy or backfill).
    """
    specs: List[Tuple[int, int, int, int]] = []
    k = chunks_done
    while total_messages >= (k + 1) * CHUNK_SIZE:
        core_start = k * CHUNK_SIZE
        core_end = core_start + CHUNK_SIZE
        window_start = max(0, core_start - OVERLAP)
        window_end = min(total_messages, core_end + OVERLAP)
        specs.append((core_start, core_end, window_start, window_end))
        k += 1
    if include_partial and total_messages > k * CHUNK_SIZE:
        core_start = k * CHUNK_SIZE
        core_end = total_messages
        window_start = max(0, core_start - OVERLAP)
        window_end = core_end
        specs.append((core_start, core_end, window_start, window_end))
    return specs


def chunks_already_done(conversation_id: str) -> int:
    """Return how many chunks (by index) are already considered complete.

    A chunk with message_count < CHUNK_SIZE is a partial trailing chunk and
    does NOT count as done — the next emission at its index will upsert and
    replace it with a full chunk when enough messages arrive.
    """
    from promaia.storage.hybrid_storage import get_hybrid_registry

    registry = get_hybrid_registry()
    with registry._connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT chunk_index, message_count FROM slack_dm_chunks
            WHERE conversation_id = ?
            ORDER BY chunk_index
            """,
            (conversation_id,),
        )
        rows = cursor.fetchall()
    if not rows:
        return 0
    last_idx, last_count = rows[-1]
    if last_count < CHUNK_SIZE:
        return last_idx  # partial — next emission replaces it
    return last_idx + 1


def _extract_text(content: Any) -> str:
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    if isinstance(content, str):
        return content
    return ""


def render_chunk_markdown(
    messages: List[Dict[str, Any]],
    spec: Tuple[int, int, int, int],
    conversation_id: str,
    partner_name: str,
) -> str:
    """Render the chunk's markdown for embedding."""
    core_start, core_end, window_start, window_end = spec
    chunk_index = core_start // CHUNK_SIZE
    window_msgs = messages[window_start:window_end]

    lines: List[str] = [
        f"# DM chunk — {partner_name}",
        "",
        f"**Conversation:** {conversation_id}",
        f"**Chunk index:** {chunk_index}",
        f"**Core messages:** {core_start}..{core_end - 1}",
        f"**Window:** {window_start}..{window_end - 1}",
        "",
        "---",
        "",
    ]
    for m in window_msgs:
        role = m.get("role", "user")
        speaker = partner_name if role == "user" else "Maia"
        ts = m.get("timestamp", "")
        text = _extract_text(m.get("content", ""))
        if not text:
            continue
        lines.append(f"### {speaker} ({ts})")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _is_incognito(context: Optional[Dict[str, Any]]) -> bool:
    return bool(context and context.get("incognito"))


async def vectorize_conversation(
    *,
    conversation_id: str,
    messages: List[Dict[str, Any]],
    channel_id: str,
    thread_id: Optional[str],
    context: Optional[Dict[str, Any]],
    workspace: str,
    include_partial: bool,
) -> int:
    """Embed any pending chunks for one conversation. Returns chunks emitted."""
    if _is_incognito(context):
        logger.debug(f"[vectorizer] Skipping incognito conversation {conversation_id}")
        return 0
    if not messages:
        return 0

    async with _lock_for(conversation_id):
        chunks_done = chunks_already_done(conversation_id)
        specs = compute_pending_chunks(
            total_messages=len(messages),
            chunks_done=chunks_done,
            include_partial=include_partial,
        )
        if not specs:
            return 0

        # Lazy imports so slack_bot startup doesn't force ChromaDB load.
        from promaia.storage.hybrid_storage import get_hybrid_registry
        from promaia.storage.vector_db import VectorDBManager
        from promaia.utils.env_writer import get_data_dir

        try:
            vector_db = VectorDBManager()
        except Exception as e:
            logger.warning(f"[vectorizer] VectorDBManager init failed: {e}")
            return 0

        registry = get_hybrid_registry()
        partner_name = (context or {}).get("user_name") or "partner"
        md_dir = Path(get_data_dir()) / "data" / "md" / "slack" / workspace / "slack_dms"
        md_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        emitted = 0

        for spec in specs:
            core_start, core_end, window_start, window_end = spec
            chunk_index = core_start // CHUNK_SIZE
            chunk_id = f"{conversation_id}_chunk_{chunk_index}"
            markdown = render_chunk_markdown(
                messages, spec, conversation_id, partner_name
            )

            md_path = md_dir / f"{chunk_id}.md"
            try:
                md_path.write_text(markdown, encoding="utf-8")
            except Exception as e:
                logger.warning(f"[vectorizer] write markdown {md_path} failed: {e}")
                continue

            start_ts = messages[core_start].get("timestamp", "") if core_start < len(messages) else ""
            last_core_idx = min(core_end - 1, len(messages) - 1)
            end_ts = messages[last_core_idx].get("timestamp", "") if messages else ""

            metadata = {
                "database_name": DATABASE_NAME,
                "workspace": workspace,
                "conversation_id": conversation_id,
                "chunk_index": chunk_index,
                "channel_id": channel_id or "",
                "thread_id": thread_id or "",
                "created_time": start_ts or now,
                "last_edited_time": end_ts or now,
            }

            try:
                ok = await asyncio.to_thread(
                    vector_db.add_content,
                    chunk_id,
                    markdown,
                    metadata,
                )
                if not ok:
                    logger.warning(f"[vectorizer] add_content returned False for {chunk_id}")
                    continue
            except Exception as e:
                logger.warning(f"[vectorizer] embedding {chunk_id} failed: {e}")
                continue

            registry.add_slack_dm_chunk(
                {
                    "chunk_id": chunk_id,
                    "conversation_id": conversation_id,
                    "chunk_index": chunk_index,
                    "workspace": workspace,
                    "database_id": DATABASE_ID,
                    "channel_id": channel_id or "",
                    "thread_id": thread_id,
                    "participants": [partner_name, "Maia"],
                    "message_count": core_end - core_start,
                    "core_start_idx": core_start,
                    "core_end_idx": core_end,
                    "window_start_idx": window_start,
                    "window_end_idx": window_end,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "file_path": str(md_path),
                    "title": f"DM with {partner_name} — chunk {chunk_index}",
                    "created_time": start_ts or now,
                    "last_edited_time": end_ts or now,
                    "synced_time": now,
                    "file_size": md_path.stat().st_size if md_path.exists() else None,
                }
            )
            emitted += 1
            logger.info(
                f"[vectorizer] embedded {chunk_id} ({core_end - core_start} msgs)"
            )

        return emitted


async def vectorize_conversation_from_state(state, *, include_partial: bool) -> int:
    """Convenience: extract needed fields from a ConversationState-like object."""
    if getattr(state, "platform", None) != "slack":
        return 0
    if getattr(state, "conversation_type", None) != "tag_to_chat":
        return 0

    # Resolve workspace via the agent config.
    workspace = "default"
    try:
        from promaia.agents.conversation_manager import ConversationManager

        cm = getattr(state, "_cm", None) or ConversationManager()
        agent = cm._get_cached_agent(state.agent_id) if hasattr(cm, "_get_cached_agent") else None
        if agent and getattr(agent, "workspace", None):
            workspace = agent.workspace
    except Exception:
        pass

    return await vectorize_conversation(
        conversation_id=state.conversation_id,
        messages=state.messages or [],
        channel_id=state.channel_id or "",
        thread_id=state.thread_id,
        context=state.context or {},
        workspace=workspace,
        include_partial=include_partial,
    )


def ensure_database_registered(workspace: str) -> None:
    """Auto-register the slack_dms DatabaseConfig in promaia.config.json.

    Idempotent — only adds if missing. Also best-effort adds slack_dms to the
    maia agent's source_access so query_vector is allowed to hit it.
    """
    try:
        from promaia.config.databases import get_database_manager

        mgr = get_database_manager()
        existing = None
        try:
            existing = mgr.get_database(DATABASE_NAME, workspace)
        except Exception:
            existing = None
        if not existing:
            config_data = {
                "source_type": "slack",
                "database_id": DATABASE_ID,
                "nickname": DATABASE_NAME,
                "description": "Chunked embeddings of Promaia's live Slack DMs",
                "sync_enabled": False,
            }
            try:
                mgr.add_database(DATABASE_NAME, config_data, workspace)
                logger.info(f"[vectorizer] registered {DATABASE_NAME} database for workspace {workspace}")
            except Exception as e:
                logger.debug(f"[vectorizer] add_database skipped: {e}")
    except Exception as e:
        logger.debug(f"[vectorizer] database registration skipped: {e}")

    try:
        _grant_source_access_to_maia(workspace)
    except Exception as e:
        logger.debug(f"[vectorizer] source_access grant skipped: {e}")


def _grant_source_access_to_maia(workspace: str) -> None:
    """Best-effort: add slack_dms to the maia agent's databases field.

    agent_config's `get_queryable_sources()` falls back to the `databases`
    list whenever `source_access` is None, which is the prevailing config on
    kb today. So the simplest grant is an idempotent append to `databases`.
    """
    from promaia.agents.agent_config import load_agents, save_agent

    for a in load_agents():
        if (a.agent_id or a.name) != "maia":
            continue
        dbs = list(a.databases or [])
        # Accept either bare "slack_dms" or qualified "slack_dms:N"
        if any(d.split(":")[0] == DATABASE_NAME for d in dbs):
            return
        dbs.append(DATABASE_NAME)
        a.databases = dbs
        save_agent(a)
        logger.info(f"[vectorizer] granted maia agent query access to {DATABASE_NAME} via databases list")
        return


async def backfill_all(
    *,
    workspace: Optional[str] = None,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """Walk conversations.db, embed every chunk for every Slack conversation.

    Returns (num_conversations_touched, num_chunks_emitted).
    Idempotent — existing chunks upsert via ChromaDB + sqlite INSERT OR REPLACE.
    """
    from promaia.agents.conversation_manager import ConversationManager

    cm = ConversationManager()
    with sqlite3.connect(cm.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM conversations
            WHERE platform = 'slack' AND conversation_type = 'tag_to_chat'
            ORDER BY last_message_at
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

    total_convos = 0
    total_chunks = 0
    for row in rows:
        try:
            state = cm._row_to_state(row)
        except Exception as e:
            logger.warning(f"[vectorizer] skip unparseable conv row: {e}")
            continue
        if _is_incognito(state.context):
            continue
        if not state.messages:
            continue

        ws = workspace
        if ws is None:
            agent = cm._get_cached_agent(state.agent_id)
            ws = (getattr(agent, "workspace", None) or "default")

        if dry_run:
            chunks_done = chunks_already_done(state.conversation_id)
            specs = compute_pending_chunks(len(state.messages), chunks_done, include_partial=True)
            total_chunks += len(specs)
            if specs:
                total_convos += 1
            continue

        n = await vectorize_conversation(
            conversation_id=state.conversation_id,
            messages=state.messages,
            channel_id=state.channel_id or "",
            thread_id=state.thread_id,
            context=state.context or {},
            workspace=ws,
            include_partial=True,
        )
        if n > 0:
            total_convos += 1
            total_chunks += n

    return total_convos, total_chunks
