"""
Always-on session logging for the maia CLI.

Captures both Python `logging` output and raw stdout/stderr (the chat
conversation, table renders, click prompts) into rotating files under
``<data_dir>/logs/`` so future Claude sessions can `tail` them without
needing the user to copy-paste from the terminal.

Layout:

    <data_dir>/logs/
        maia.log                ← stdlib logging, daily rotation, 14 backups
        sessions/
            YYYY-MM-DD.log      ← stdout+stderr appended through the day
            today.log           ← symlink to today's file (stable path for tail)

Old session files (>30 days) are pruned on startup.

Call ``setup_session_logging()`` once at the top of ``cli.main()``.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import IO, Optional

# Strip ANSI CSI/OSC sequences so the on-disk log is plain text and grep-able.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")

_SESSION_DIR_NAME = "sessions"
_TODAY_SYMLINK = "today.log"
_PRUNE_AFTER_DAYS = 30

_setup_done = False


class _StreamTee:
    """File-like wrapper that mirrors writes to a backing log file.

    Designed to wrap ``sys.stdout`` / ``sys.stderr`` without disturbing
    interactive behavior:

    - the original stream still receives every byte unchanged (colors and
      cursor moves keep working in the terminal)
    - the log file gets the same bytes with ANSI sequences stripped
    - any IO error on the log side is swallowed; we never break the user's
      output for a logging failure
    """

    def __init__(self, original: IO, log_handle: IO) -> None:
        self._original = original
        self._log = log_handle

    def write(self, data: str) -> int:
        n = self._original.write(data)
        try:
            self._log.write(_ANSI_RE.sub("", data))
            self._log.flush()
        except Exception:
            pass
        return n

    def flush(self) -> None:
        self._original.flush()
        try:
            self._log.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._original, name)


def _prune_old_sessions(session_dir: Path, days: int) -> None:
    """Delete session log files older than ``days`` days."""
    if not session_dir.is_dir():
        return
    cutoff = time.time() - days * 86400
    for entry in session_dir.iterdir():
        if not entry.is_file() or entry.is_symlink():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _refresh_today_symlink(session_dir: Path, target: Path) -> None:
    """Point ``sessions/today.log`` at the file for the current day."""
    link = session_dir / _TODAY_SYMLINK
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target.name)  # relative target, so it survives data-dir moves
    except OSError:
        pass


def setup_session_logging(level: Optional[int] = None) -> None:
    """Install always-on session logging. Idempotent.

    Args:
        level: Optional explicit level for the file handler. Defaults to
            ``INFO`` so the disk log is more verbose than what the user
            sees (which stays at WARNING in production), without spamming
            DEBUG-level chatter from libraries.
    """
    global _setup_done
    if _setup_done:
        return

    try:
        from promaia.utils.env_writer import get_logs_dir
    except Exception:
        return  # env_writer unavailable — bail silently, logging is best-effort

    try:
        logs_dir = get_logs_dir()
        session_dir = logs_dir / _SESSION_DIR_NAME
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    # 1. Rotating stdlib logger → maia.log
    try:
        handler = TimedRotatingFileHandler(
            logs_dir / "maia.log",
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
            delay=True,  # don't touch the file until something actually logs
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        )
        handler.setLevel(level if level is not None else logging.INFO)
        root = logging.getLogger()
        # Avoid duplicate handlers if some other module wired one up first.
        already = any(
            isinstance(h, TimedRotatingFileHandler)
            and getattr(h, "baseFilename", "").endswith("maia.log")
            for h in root.handlers
        )
        if not already:
            root.addHandler(handler)
            # Don't lower the root level below what the CLI configured for the
            # console handler; just make sure it isn't above our file level.
            if root.level == logging.NOTSET or root.level > handler.level:
                root.setLevel(handler.level)
    except OSError:
        pass

    # 2. Tee stdout / stderr → sessions/YYYY-MM-DD.log (+ today.log symlink)
    try:
        today_file = session_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        log_handle = open(today_file, "a", encoding="utf-8", buffering=1)
        # Write a session header so it's easy to scan for the start of a run.
        header = (
            f"\n=== maia session start "
            f"{datetime.now().isoformat(timespec='seconds')} "
            f"pid={os.getpid()} ===\n"
        )
        log_handle.write(header)
        log_handle.flush()

        sys.stdout = _StreamTee(sys.stdout, log_handle)
        sys.stderr = _StreamTee(sys.stderr, log_handle)

        _refresh_today_symlink(session_dir, today_file)
    except OSError:
        pass

    # 3. Prune sessions/ files older than 30 days. Best-effort.
    try:
        _prune_old_sessions(session_dir, _PRUNE_AFTER_DAYS)
    except Exception:
        pass

    _setup_done = True
