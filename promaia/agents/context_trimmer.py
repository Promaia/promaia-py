"""
Proactive context trimming — hybrid threshold-multiplier model.

Two layers prevent context-window overflow before it happens:

Layer 1: Trim context entries in the system prompt (database pages).
Layer 2: Summarize old conversation turns when messages alone are too large.
"""

import asyncio
import datetime as _dt
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
# Layer 2: Conversation history summarization
# ---------------------------------------------------------------------------

async def _summarize_old_turns(messages: List[Dict], budget_tokens: int) -> List[Dict]:
    """Summarize old conversation turns, keeping the most recent 4 pairs.

    Calls Haiku to generate a summary, then replaces old messages with a
    single summary message. Maintains alternating user/assistant structure.
    """
    # Find user+assistant turn pairs from the end
    pairs_to_keep = 4
    pair_count = 0
    keep_from_idx = len(messages)

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            pair_count += 1
            if pair_count >= pairs_to_keep:
                keep_from_idx = i
                break

    # If there aren't enough old messages to summarize, return as-is
    if keep_from_idx <= 1:
        return messages

    old_messages = messages[:keep_from_idx]
    recent_messages = messages[keep_from_idx:]

    # Build text from old messages for summarization
    old_text_parts = []
    for msg in old_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        result = block.get("content", "")
                        if isinstance(result, str):
                            # Truncate large tool results in summary input
                            text_parts.append(result[:500] + "..." if len(result) > 500 else result)
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[Called tool: {block.get('name', '?')}]")
            content = "\n".join(text_parts)
        old_text_parts.append(f"{role}: {content}")

    old_transcript = "\n\n".join(old_text_parts)

    # Truncate the transcript itself if it's too large for the Haiku call
    max_summary_input_chars = 80_000
    if len(old_transcript) > max_summary_input_chars:
        old_transcript = old_transcript[:max_summary_input_chars] + "\n\n[... earlier content truncated ...]"

    summary_budget = min(budget_tokens, 2000)  # cap summary size
    summary_prompt = (
        f"Summarize this conversation history in under {summary_budget} tokens. "
        f"Preserve key facts, decisions, context the user established, and any "
        f"important tool results or data. Be concise but thorough.\n\n"
        f"CONVERSATION:\n{old_transcript}"
    )

    # Call Haiku for summarization
    summary_text = await _call_haiku_for_summary(summary_prompt)

    if not summary_text:
        # Haiku call failed — fall back to simple truncation
        summary_text = (
            "[Earlier conversation truncated to fit context limit. "
            f"Contained {len(old_messages)} messages.]"
        )

    summary_message = {
        "role": "user",
        "content": f"[Conversation summary: {summary_text}]",
    }

    # Ensure alternating structure: summary (user) then recent messages
    result = [summary_message]

    # If recent messages start with a user message, insert a placeholder assistant msg
    if recent_messages and recent_messages[0].get("role") == "user":
        result.append({"role": "assistant", "content": "[Continuing from summary above.]"})

    result.extend(recent_messages)

    # Validate alternating structure
    result = _fix_alternating_structure(result)

    logger.info(
        f"[context_trimmer] Summarized {len(old_messages)} old messages "
        f"into {estimate_token_count(summary_text)} token summary, "
        f"keeping {len(recent_messages)} recent messages"
    )

    return result


async def _call_haiku_for_summary(prompt: str) -> Optional[str]:
    """Call Haiku to generate a conversation summary."""
    try:
        from promaia.utils.ai import get_anthropic_client

        client, prefix = get_anthropic_client()
        if not client:
            return None

        response = await asyncio.to_thread(
            client.messages.create,
            model=f"{prefix}claude-haiku-4-5-20251001",
            system="You are a conversation summarizer. Output only the summary, no preamble.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        if response and response.content:
            return response.content[0].text
    except Exception as e:
        logger.warning(f"[context_trimmer] Haiku summary call failed: {e}")
    return None


def _fix_alternating_structure(messages: List[Dict]) -> List[Dict]:
    """Ensure messages alternate between user and assistant roles."""
    if not messages:
        return messages

    fixed = [messages[0]]
    for msg in messages[1:]:
        if msg.get("role") == fixed[-1].get("role"):
            if msg["role"] == "user":
                fixed.append({"role": "assistant", "content": "[continued]"})
            else:
                fixed.append({"role": "user", "content": "[continued]"})
        fixed.append(msg)
    return fixed


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def trim_context_to_fit(
    system_prompt: str,
    messages: List[Dict],
    max_context_tokens: int = 200_000,
    max_output_tokens: int = 4096,
    tools: Optional[List[Dict]] = None,
) -> Tuple[str, List[Dict]]:
    """Proactively trim system prompt and messages to stay within context budget.

    Returns (trimmed_system_prompt, trimmed_messages). No-op if already within
    the 3/4 threshold.
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

    # Layer 2: Summarize old conversation turns
    messages_budget = available - system_tokens - tools_tokens
    if messages_budget > 0 and messages_tokens > messages_budget:
        messages = await _summarize_old_turns(messages, messages_budget)
        messages_tokens = _estimate_messages_tokens(messages)
        total = system_tokens + tools_tokens + messages_tokens
        logger.info(
            f"[context_trimmer] After Layer 2: msgs={messages_tokens}, total={total}"
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
