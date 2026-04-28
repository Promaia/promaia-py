"""Append-only ledger of per-turn Slack AI costs.

Each line is a JSON object recorded the moment a Slack assistant turn
completes. The `/cost` slash command reads this file and aggregates by
day, week, and month.

Stored under ``get_data_dir() / "slack" / "cost_ledger.jsonl"``.

Schema (per line):
    {
      "ts": "2026-04-28T18:32:11+00:00",  # ISO-8601 UTC
      "channel_id": "C0123",
      "thread_id": "1730....",
      "agent_id": "maia",
      "model": "claude-opus-4-6-1m",
      "prompt_tokens": 5234,
      "response_tokens": 612,
      "cache_read_tokens": 18204,
      "cache_creation_tokens": 1024,
      "total_cost": 0.04211
    }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from promaia.utils.env_writer import get_data_dir

logger = logging.getLogger(__name__)


def _ledger_path() -> Path:
    return get_data_dir() / "slack" / "cost_ledger.jsonl"


def append_entry(
    *,
    channel_id: Optional[str],
    thread_id: Optional[str],
    agent_id: Optional[str],
    model: str,
    prompt_tokens: int,
    response_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    total_cost: float,
) -> None:
    """Append a single cost entry. Failures are logged, never raised — the
    ledger is observability, not a correctness boundary."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "thread_id": thread_id,
        "agent_id": agent_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "total_cost": total_cost,
    }
    try:
        path = _ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning(f"Failed to append cost ledger entry: {e}")


def _iter_entries() -> List[Dict]:
    path = _ledger_path()
    if not path.exists():
        return []
    out: List[Dict] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"Failed to read cost ledger: {e}")
    return out


def aggregate_since(since: datetime) -> Dict[str, float]:
    """Return totals for entries with ts >= since."""
    totals = {
        "cost": 0.0,
        "prompt_tokens": 0,
        "response_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "turns": 0,
    }
    for entry in _iter_entries():
        try:
            ts = datetime.fromisoformat(entry["ts"])
        except Exception:
            continue
        if ts < since:
            continue
        totals["cost"] += float(entry.get("total_cost") or 0)
        totals["prompt_tokens"] += int(entry.get("prompt_tokens") or 0)
        totals["response_tokens"] += int(entry.get("response_tokens") or 0)
        totals["cache_read_tokens"] += int(entry.get("cache_read_tokens") or 0)
        totals["cache_creation_tokens"] += int(entry.get("cache_creation_tokens") or 0)
        totals["turns"] += 1
    return totals


def summary() -> Dict[str, Dict[str, float]]:
    """Return {'day': {...}, 'week': {...}, 'month': {...}} totals."""
    now = datetime.now(timezone.utc)
    return {
        "day": aggregate_since(now - timedelta(days=1)),
        "week": aggregate_since(now - timedelta(days=7)),
        "month": aggregate_since(now - timedelta(days=30)),
    }


def format_cost_prefix(
    prompt_tokens: int,
    response_tokens: int,
    turn_cost: float,
    session_cost: float,
) -> str:
    """Format the per-message cost prefix for Slack.

    Mirrors the terminal footer:
        prompt, response, total
        $turn_cost  (session: $session_cost)

    Returns a string ready to prepend to the assistant message body.
    """
    total_tokens = prompt_tokens + response_tokens
    line1 = f"{prompt_tokens:,}, {response_tokens:,}, {total_tokens:,}"
    line2 = f"${turn_cost:.6f}  (session: ${session_cost:.4f})"
    return f"{line1}\n{line2}"
