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
import subprocess
import sys
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import IO, List, Optional

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


def _today_session_path() -> Optional[Path]:
    """Resolve today's session log path. Returns None if logs dir is unavailable."""
    try:
        from promaia.utils.env_writer import get_logs_dir

        session_dir = get_logs_dir() / _SESSION_DIR_NAME
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    except Exception:
        return None


def spawn_logged_child(
    cmd: List[str],
    service_name: str,
    **popen_kwargs,
) -> subprocess.Popen:
    """Spawn a subprocess that mirrors stdout+stderr to the shared session log.

    Designed for `promaia.services.supervisor`: every non-CLI container
    (web, scheduler, slack, discord, calendar, mail) routes its child
    through this so its output also lands in
    `<data_dir>/logs/sessions/today.log` with a `[service_name]` prefix.

    Capturing happens at the OS pipe level, so anything the child writes
    to FD 1 or FD 2 — including its own subprocesses — is included.

    The original stdout/stderr (which is the docker container's log
    stream) still receives every byte unchanged, so `docker logs <svc>`
    keeps working exactly as before.
    """
    # Resolve the shared log path once. If unavailable, fall back to a
    # plain Popen so the supervisor never breaks just because logging
    # infrastructure isn't ready.
    log_path = _today_session_path()
    if log_path is None:
        return subprocess.Popen(cmd, **popen_kwargs)

    try:
        log_handle: Optional[IO] = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return subprocess.Popen(cmd, **popen_kwargs)

    header = (
        f"\n=== [{service_name}] supervisor child start "
        f"{datetime.now().isoformat(timespec='seconds')} "
        f"pid={os.getpid()} cmd={' '.join(cmd)} ===\n"
    )
    try:
        log_handle.write(header)
        log_handle.flush()
    except Exception:
        pass

    # Refresh today.log symlink so external readers can always tail one path.
    try:
        _refresh_today_symlink(log_path.parent, log_path)
    except Exception:
        pass

    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.STDOUT)
    popen_kwargs.setdefault("bufsize", 1)
    popen_kwargs.setdefault("text", True)
    popen_kwargs.setdefault("errors", "replace")

    proc = subprocess.Popen(cmd, **popen_kwargs)

    def _relay() -> None:
        prefix = f"[{service_name}] "
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                # Echo to docker's captured stream verbatim
                try:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except Exception:
                    pass
                # Tee into shared session log, ANSI-stripped + prefixed
                try:
                    clean = _ANSI_RE.sub("", line)
                    if not clean.endswith("\n"):
                        clean += "\n"
                    log_handle.write(prefix + clean)
                    log_handle.flush()
                except Exception:
                    pass
        finally:
            try:
                log_handle.close()
            except Exception:
                pass

    relay_thread = threading.Thread(
        target=_relay,
        name=f"log-relay-{service_name}",
        daemon=True,
    )
    relay_thread.start()
    # Stash the relay thread on the proc object so callers can join if they
    # want a clean shutdown — supervisor.py doesn't have to bother.
    proc._log_relay = relay_thread  # type: ignore[attr-defined]
    return proc
