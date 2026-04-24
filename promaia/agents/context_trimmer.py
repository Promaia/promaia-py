"""
Proactive context trimming — hybrid threshold-multiplier model.

Two layers prevent context-window overflow before it happens:

Layer 1: Trim context entries in the system prompt (database pages).
Layer 2: Bucket-aware trim — measure sources / history / other and trim
         the largest bucket first. Sources are LRU'd off (lossless,
         re-mountable via turn_on_source) before any history is dropped.
"""

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from promaia.utils.ai import estimate_token_count

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section parsing (mirrors agentic_turn._parse_page_sections)
# ---------------------------------------------------------------------------

_DB_HEADER_RE = re.compile(r'^### === .+ DATABASE \(\d+ entries\) ===$', re.MULTILINE)
_PAGE_ENTRY_RE = re.compile(
    r'^(?:\*\*[\w\-]+\*\* entry \(|'       # Standard: **db_name** entry (Date: ...
    r'\*\*`.+`\*\*)',                       # Discord: **`timestamp  author  #channel  file`**
    re.MULTILINE,
)
_DATE_RE = re.compile(r'(?:Date:\s*|^|\s)(\d{4}-\d{2}-\d{2})')


@dataclass
class _Section:
    start: int
    end: int
    header: str
    body: str
    date_str: str
    is_db_header: bool


def _parse_sections(text: str) -> List[_Section]:
    """Split formatted context data into page sections."""
    boundaries = []
    for m in _DB_HEADER_RE.finditer(text):
        boundaries.append((m.start(), True))
    for m in _PAGE_ENTRY_RE.finditer(text):
        boundaries.append((m.start(), False))

    if not boundaries:
        return []

    boundaries.sort(key=lambda x: x[0])
    sections = []
    for i, (start, is_db) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        chunk = text[start:end]

        newline_pos = chunk.find("\n")
        if newline_pos >= 0:
            header = chunk[:newline_pos]
            body = chunk[newline_pos + 1:]
        else:
            header = chunk
            body = ""

        date_str = ""
        date_match = _DATE_RE.search(header)
        if date_match:
            date_str = date_match.group(1)
        elif not is_db and body:
            date_match = _DATE_RE.search(body[:200])
            if date_match:
                date_str = date_match.group(1)

        sections.append(_Section(
            start=start, end=end, header=header,
            body=body, date_str=date_str, is_db_header=is_db,
        ))

    return sections


# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------

def _estimate_messages_tokens(messages: List[Dict]) -> int:
    """Estimate total tokens across all messages, handling both string and list content."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_token_count(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # tool_use blocks: count name + input JSON
                    if block.get("type") == "tool_use":
                        total += estimate_token_count(block.get("name", ""))
                        total += estimate_token_count(json.dumps(block.get("input", {})))
                    # tool_result blocks
                    elif block.get("type") == "tool_result":
                        result = block.get("content", "")
                        if isinstance(result, str):
                            total += estimate_token_count(result)
                        elif isinstance(result, list):
                            for sub in result:
                                if isinstance(sub, dict):
                                    total += estimate_token_count(sub.get("text", ""))
                    # text blocks
                    elif block.get("type") == "text":
                        total += estimate_token_count(block.get("text", ""))
                    # image blocks: Anthropic charges ~1600 tokens per image
                    # based on resolution, NOT on base64 data size
                    elif block.get("type") == "image":
                        total += 1600
                    else:
                        total += estimate_token_count(json.dumps(block))
                elif isinstance(block, str):
                    total += estimate_token_count(block)
        # Per-message overhead (role, framing)
        total += 4
    return total


# ---------------------------------------------------------------------------
# Layer 1: Context prompt trimming
# ---------------------------------------------------------------------------

_IMMUNITY_THRESHOLD = 200  # tokens — entries smaller than this are never trimmed


def _trim_context_entries(system_prompt: str, target_tokens: int) -> str:
    """Trim context data entries in the system prompt to fit within target_tokens.

    Uses proportional trimming with recency weighting:
    - Today's entries: 0.5x weight (protected)
    - 7-day-old: ~1.0x
    - 30+ days: 2.0x (trimmed aggressively)

    Entries under 200 tokens are immune.
    """
    sections = _parse_sections(system_prompt)
    if not sections:
        # No structured context — hard truncate to approximate char count
        target_chars = target_tokens * 4
        if len(system_prompt) > target_chars:
            return system_prompt[:target_chars] + "\n\n[context trimmed to fit context limit]"
        return system_prompt

    current_tokens = estimate_token_count(system_prompt)
    overflow_tokens = current_tokens - target_tokens
    if overflow_tokens <= 0:
        return system_prompt

    today = _dt.date.today()

    # Identify trimmable entries (not DB headers, not tiny)
    trimmable = []
    for sec in sections:
        if sec.is_db_header:
            continue
        body_tokens = estimate_token_count(sec.body)
        if body_tokens < _IMMUNITY_THRESHOLD:
            continue
        trimmable.append((sec, body_tokens))

    if not trimmable:
        # Nothing to trim structurally — hard truncate
        target_chars = target_tokens * 4
        if len(system_prompt) > target_chars:
            return system_prompt[:target_chars] + "\n\n[context trimmed to fit context limit]"
        return system_prompt

    total_trimmable_tokens = sum(bt for _, bt in trimmable)

    # Calculate recency weights
    weights = []
    for sec, _ in trimmable:
        age_days = 30  # default: moderate trim
        if sec.date_str:
            try:
                page_date = _dt.date.fromisoformat(sec.date_str)
                age_days = max(0, (today - page_date).days)
            except ValueError:
                pass
        # Today: 0.5x (protected), 7-day: ~1.0x, 30+: 2.0x
        weight = 0.5 + min(age_days / 15.0, 1.5)
        weights.append(weight)

    # Weighted proportional shares
    raw_shares = [bt / total_trimmable_tokens for _, bt in trimmable]
    weighted_shares = [rs * w for rs, w in zip(raw_shares, weights)]
    ws_sum = sum(weighted_shares) or 1.0
    normalized = [ws / ws_sum for ws in weighted_shares]

    # Per-entry trim amounts (in characters, since we rebuild from text)
    trim_map: Dict[int, str] = {}  # id(sec) -> trimmed body
    for i, (sec, body_tokens) in enumerate(trimmable):
        trim_tokens = int(overflow_tokens * normalized[i])
        trim_chars = trim_tokens * 4  # rough tokens-to-chars
        if trim_chars <= 0:
            continue
        # Cap: retain at least 200 chars of body
        max_trim = max(0, len(sec.body) - 200)
        actual_trim = min(trim_chars, max_trim)
        if actual_trim > 0:
            keep = len(sec.body) - actual_trim
            removed_tokens = estimate_token_count(sec.body[keep:])
            trimmed_body = sec.body[:keep] + f"\n[context trimmed — {removed_tokens} tokens removed]\n"
            trim_map[id(sec)] = trimmed_body

    # Reassemble the system prompt
    parts = []
    prev_end = 0
    for sec in sections:
        if sec.start > prev_end:
            parts.append(system_prompt[prev_end:sec.start])
        parts.append(sec.header + "\n")
        if id(sec) in trim_map:
            parts.append(trim_map[id(sec)])
        else:
            parts.append(sec.body)
        prev_end = sec.end

    if prev_end < len(system_prompt):
        parts.append(system_prompt[prev_end:])

    result = "".join(parts)

    # Safety: hard truncate if still over
    result_tokens = estimate_token_count(result)
    if result_tokens > target_tokens + 1000:
        target_chars = target_tokens * 4
        result = result[:target_chars] + "\n\n[context trimmed to fit context limit]"

    return result


# ---------------------------------------------------------------------------
# Layer 2: Bucket-aware trim
# ---------------------------------------------------------------------------

# Margin above the strict overflow so the trimmer doesn't immediately re-fire
# next iteration. ~1 turn's worth of expected growth.
_TRIM_MARGIN_TOKENS = 10_000

# Sources mounted within this many recent iterations are protected from LRU
# off, so the model can actually use what it just asked for.
_RECENT_MOUNT_GRACE = 2


def _measure_buckets(
    system_tokens: int,
    tools_tokens: int,
    messages: List[Dict],
    tool_executor: Any,
) -> Tuple[int, int, int]:
    """Return (sources_tokens, history_tokens, other_tokens).

    Sources are part of the system prompt via build_active_source_content,
    so we subtract them from system to get the 'other' bucket and avoid
    double-counting.
    """
    sources_tokens = 0
    if tool_executor is not None and hasattr(tool_executor, "_sources"):
        for src in tool_executor._sources.values():
            if not src.get("on"):
                continue
            content = src.get("content", "") or ""
            sources_tokens += estimate_token_count(content)

    history_tokens = _estimate_messages_tokens(messages)
    other_tokens = max(0, system_tokens + tools_tokens - sources_tokens)
    return sources_tokens, history_tokens, other_tokens


def _trim_sources_bucket(
    tool_executor: Any,
    need_to_free: int,
    current_iteration: int,
) -> int:
    """Toggle sources OFF in LRU order until ~need_to_free tokens are freed.

    Skips sources mounted within _RECENT_MOUNT_GRACE iterations. Returns the
    number of tokens actually freed.
    """
    if tool_executor is None or not hasattr(tool_executor, "_sources"):
        return 0

    candidates = []
    for name, src in tool_executor._sources.items():
        if not src.get("on"):
            continue
        mounted_at = src.get("mounted_at_iteration", 0) or 0
        if current_iteration - mounted_at < _RECENT_MOUNT_GRACE:
            continue
        size = estimate_token_count(src.get("content", "") or "")
        candidates.append((mounted_at, size, name))

    # LRU: oldest mounted_at first
    candidates.sort(key=lambda x: (x[0], -x[1]))

    freed = 0
    turned_off = []
    for _, size, name in candidates:
        if freed >= need_to_free:
            break
        tool_executor._sources[name]["on"] = False
        freed += size
        turned_off.append(name)

    if turned_off:
        logger.info(
            f"[context_trimmer] Sources bucket trim: turned OFF {turned_off}, "
            f"freed ~{freed} tokens"
        )
    return freed


def _drop_oldest_history(messages: List[Dict], need_to_free: int) -> List[Dict]:
    """Drop oldest complete turns until ~need_to_free tokens are freed.

    Pair safety: a tool_use block in an assistant message and its matching
    tool_result block in the next user message must be dropped together.
    Walks forward in turn-pairs (user → assistant → optional tool_results).
    """
    if not messages:
        return messages

    freed = 0
    drop_until = 0  # exclusive index — messages[:drop_until] will be dropped

    i = 0
    n = len(messages)
    while i < n and freed < need_to_free:
        # Identify the end of the current "turn group": one user message,
        # the following assistant message, and any subsequent user message
        # that contains tool_result blocks (which pair with tool_use blocks
        # in the assistant message).
        group_end = i + 1
        # Pull in trailing assistant + tool_result user messages
        while group_end < n:
            nxt = messages[group_end]
            content = nxt.get("content", "")
            is_tool_result_msg = (
                nxt.get("role") == "user"
                and isinstance(content, list)
                and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
            )
            if nxt.get("role") == "assistant" or is_tool_result_msg:
                group_end += 1
                continue
            break

        group_tokens = _estimate_messages_tokens(messages[i:group_end])
        freed += group_tokens
        drop_until = group_end
        i = group_end

    if drop_until == 0:
        return messages

    if drop_until >= len(messages):
        # Refuse to drop everything — leave at least the most recent turn.
        # Find the start of the last user message and keep from there.
        for j in range(len(messages) - 1, -1, -1):
            if messages[j].get("role") == "user":
                drop_until = j
                break
        if drop_until == 0:
            return messages

    dropped = messages[:drop_until]
    kept = messages[drop_until:]

    # Pair safety: if the first kept message contains tool_result blocks,
    # we orphaned its tool_use. Strip orphan tool_result blocks from the
    # head of `kept`.
    while kept:
        first = kept[0]
        content = first.get("content", "")
        if first.get("role") == "user" and isinstance(content, list):
            has_tool_result = any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
            if has_tool_result:
                # Drop this orphaned tool_result message entirely
                kept = kept[1:]
                continue
        break

    if not kept:
        # The orphan-strip cascade ate everything. Sending an empty messages
        # list to Anthropic yields a 400 ("at least one message is required")
        # that masks the real problem. Keep the most recent message so the
        # caller at least surfaces an honest token-overflow error (and Fix B
        # — shelving fat tool results into context sources — should mean
        # this branch never fires in practice).
        logger.warning(
            "[context_trimmer] History trim would have returned empty; "
            "falling back to the most recent message to avoid an "
            "empty-messages API error. Upstream tools are likely not "
            "shelving fat bodies."
        )
        kept = messages[-1:]

    logger.info(
        f"[context_trimmer] History bucket trim: dropped {len(dropped)} oldest "
        f"messages, freed ~{freed} tokens"
    )
    return kept


async def _bucket_trim(
    messages: List[Dict],
    tool_executor: Any,
    system_tokens: int,
    tools_tokens: int,
    available: int,
    current_iteration: int,
) -> Tuple[List[Dict], int]:
    """Bucket-aware trim. Returns (new_messages, sources_freed_tokens).

    Picks the largest bucket (sources / history / other) and trims it.
    Sources are tried first when they alone could close the gap, since
    they're losslessly recoverable via turn_on_source.
    """
    sources_tokens, history_tokens, other_tokens = _measure_buckets(
        system_tokens, tools_tokens, messages, tool_executor
    )
    total = sources_tokens + history_tokens + other_tokens
    overflow = total - available

    logger.info(
        f"[context_trimmer] Bucket measurement: sources={sources_tokens}, "
        f"history={history_tokens}, other={other_tokens} (untrimmable), "
        f"total={total}, available={available}, overflow={overflow}"
    )

    if overflow <= 0:
        return messages, 0

    need = overflow + _TRIM_MARGIN_TOKENS
    sources_freed = 0

    # Strategy: try sources first if they can cover the need OR if they're
    # the largest bucket. Otherwise go to history.
    try_sources_first = (
        sources_tokens >= need or sources_tokens >= history_tokens
    )

    if try_sources_first and sources_tokens > 0:
        sources_freed = _trim_sources_bucket(
            tool_executor, need, current_iteration
        )
        need -= sources_freed

    if need > 0 and history_tokens > 0:
        messages = _drop_oldest_history(messages, need)

    return messages, sources_freed


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def trim_context_to_fit(
    system_prompt: str,
    messages: List[Dict],
    max_context_tokens: int = 200_000,
    max_output_tokens: int = 4096,
    tools: Optional[List[Dict]] = None,
    tool_executor: Any = None,
    current_iteration: int = 0,
    rebuild_system_prompt: Optional[Any] = None,
) -> Tuple[str, List[Dict]]:
    """Proactively trim system prompt and messages to stay within context budget.

    Returns (trimmed_system_prompt, trimmed_messages). No-op if already within
    the 3/4 threshold.

    Layer 1: Trim context entries in the system prompt (database pages).
    Layer 2: Bucket-aware trim — sources LRU off (lossless), then history.

    If `rebuild_system_prompt` is provided, it is called after Layer 2 toggles
    sources off so the system prompt's active-source block reflects the new
    state.
    """
    available = int(max_context_tokens * 0.75) - max_output_tokens

    system_tokens = estimate_token_count(system_prompt)
    tools_tokens = estimate_token_count(json.dumps(tools)) if tools else 0
    messages_tokens = _estimate_messages_tokens(messages)
    total = system_tokens + tools_tokens + messages_tokens

    if total <= available:
        return system_prompt, messages

    logger.info(
        f"[context_trimmer] Over budget: {total} tokens "
        f"(system={system_tokens}, tools={tools_tokens}, msgs={messages_tokens}) "
        f"> available={available}"
    )

    # Layer 1: Trim context entries in system prompt
    # Target: system prompt should take at most 60% of available budget
    system_target = int(available * 0.6)
    if system_tokens > system_target:
        system_prompt = _trim_context_entries(system_prompt, system_target)
        system_tokens = estimate_token_count(system_prompt)
        total = system_tokens + tools_tokens + messages_tokens
        logger.info(
            f"[context_trimmer] After Layer 1: system={system_tokens}, total={total}"
        )

    if total <= available:
        return system_prompt, messages

    # Layer 2: Bucket-aware trim
    messages, sources_freed = await _bucket_trim(
        messages=messages,
        tool_executor=tool_executor,
        system_tokens=system_tokens,
        tools_tokens=tools_tokens,
        available=available,
        current_iteration=current_iteration,
    )

    # If sources were toggled off, the system prompt's active-source block is
    # now stale — rebuild it.
    if sources_freed > 0 and rebuild_system_prompt is not None:
        try:
            system_prompt = rebuild_system_prompt()
            system_tokens = estimate_token_count(system_prompt)
        except Exception as e:
            logger.warning(f"[context_trimmer] rebuild_system_prompt failed: {e}")

    messages_tokens = _estimate_messages_tokens(messages)
    total = system_tokens + tools_tokens + messages_tokens
    logger.info(
        f"[context_trimmer] After Layer 2: system={system_tokens}, "
        f"msgs={messages_tokens}, total={total}"
    )

    # Safety net: if still over, aggressively trim system prompt
    if total > available:
        remaining_for_system = available - tools_tokens - messages_tokens
        if remaining_for_system > 0:
            system_prompt = _trim_context_entries(system_prompt, remaining_for_system)
        else:
            # Extreme case: hard truncate system prompt to minimum
            min_system_chars = 2000
            system_prompt = system_prompt[:min_system_chars] + "\n\n[context heavily trimmed to fit limit]"

    return system_prompt, messages


def trim_context_to_fit_sync(
    system_prompt: str,
    messages: List[Dict],
    max_context_tokens: int = 200_000,
    max_output_tokens: int = 4096,
    tools: Optional[List[Dict]] = None,
) -> Tuple[str, List[Dict]]:
    """Synchronous version — applies Layer 1 (context trimming) only.

    Layer 2 (Haiku summarization) is skipped because it requires async I/O.
    Use the async ``trim_context_to_fit`` when an event loop is available.
    """
    available = int(max_context_tokens * 0.75) - max_output_tokens

    system_tokens = estimate_token_count(system_prompt)
    tools_tokens = estimate_token_count(json.dumps(tools)) if tools else 0
    messages_tokens = _estimate_messages_tokens(messages)
    total = system_tokens + tools_tokens + messages_tokens

    if total <= available:
        return system_prompt, messages

    logger.info(
        f"[context_trimmer] Sync trim — over budget: {total} > {available}"
    )

    # Layer 1 only: trim context entries in system prompt
    system_target = int(available * 0.6)
    if system_tokens > system_target:
        system_prompt = _trim_context_entries(system_prompt, system_target)
        system_tokens = estimate_token_count(system_prompt)
        total = system_tokens + tools_tokens + messages_tokens

    # Safety net: aggressive system prompt trim
    if total > available:
        remaining_for_system = available - tools_tokens - messages_tokens
        if remaining_for_system > 0:
            system_prompt = _trim_context_entries(system_prompt, remaining_for_system)
        else:
            min_system_chars = 2000
            system_prompt = system_prompt[:min_system_chars] + "\n\n[context heavily trimmed to fit limit]"

    return system_prompt, messages
