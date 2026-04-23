"""Atomic per-section config I/O.

Each top-level config section (agents, databases, mcp_servers, channels, etc.)
lives in its own JSON file under the data directory. Writes are atomic via
write-to-tmp + os.replace, with rotating backups for instant recovery.

Why this exists: the legacy single-blob `promaia.config.json` was repeatedly
losing top-level keys (agents, etc.) and nested fields (per-agent databases
list) because writers would read the whole blob, modify, and write back —
any partial-write or stale-read race could silently drop sections. Per-section
files mean each writer touches its own file; sections can't accidentally
clobber each other.

See memory/project_config_wipe_bug.md for the full incident history.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_BACKUPS = 5


def _get_data_dir() -> Path:
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir()


def section_path(name: str) -> Path:
    """Path to the per-section file for `name` (e.g. 'agents' → agents.json)."""
    return _get_data_dir() / f"{name}.json"


def _backup_path(path: Path, slot: int) -> Path:
    return path.with_name(f"{path.name}.bak.{slot}")


def read_section(name: str) -> Optional[Any]:
    """Read a per-section config file.

    Returns the parsed JSON, or None if the file is missing.

    If the file exists but is corrupted, quarantines the bad bytes to
    `<file>.corrupted-<timestamp>`, logs an ERROR, and returns None — NEVER
    silently overwrites or proceeds with empty state. Callers must treat None
    as "section unknown" and decide whether to fall back to legacy blob,
    refuse the operation, etc.
    """
    path = section_path(name)
    if not path.exists():
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        quarantine = path.with_name(f"{path.name}.corrupted-{ts}")
        try:
            shutil.copy2(path, quarantine)
            logger.error(
                f"read_section({name!r}): file is corrupted ({e}). "
                f"Backed up to {quarantine}. Returning None — caller MUST "
                f"NOT proceed as if the section is empty unless that is "
                f"genuinely safe. See memory/project_config_wipe_bug.md."
            )
        except Exception as copy_err:
            logger.error(
                f"read_section({name!r}): file is corrupted ({e}) AND "
                f"backup copy failed ({copy_err}). Returning None."
            )
        return None


def write_section(name: str, data: Any) -> None:
    """Atomic write of a per-section config file.

    Sequence:
      1. Write JSON to a temp file in the same directory (so rename is atomic
         on the same filesystem).
      2. fsync the tmp file so bytes hit disk before we rename.
      3. Rotate existing backups: bak.5 deleted, bak.4 → bak.5, ..., current → bak.1.
      4. os.replace(tmp, target) — atomic on POSIX.

    The backup-then-replace order means even if the process dies between step
    3 and step 4, the previous content is recoverable from .bak.1.

    Raises if the write itself fails. Does NOT swallow errors — the caller is
    responsible for logging context and deciding whether to retry.
    """
    path = section_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1+2: write to tmp + fsync
    fd, tmp_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Step 3: rotate backups, then move current → bak.1
        if path.exists():
            # Drop the oldest if we're at capacity
            oldest = _backup_path(path, MAX_BACKUPS)
            if oldest.exists():
                try:
                    oldest.unlink()
                except Exception as e:
                    logger.warning(f"write_section({name!r}): could not delete oldest backup {oldest}: {e}")
            # Shift bak.{N-1} → bak.N for N from MAX_BACKUPS down to 2
            for i in range(MAX_BACKUPS, 1, -1):
                src = _backup_path(path, i - 1)
                dst = _backup_path(path, i)
                if src.exists():
                    try:
                        src.replace(dst)
                    except Exception as e:
                        logger.warning(f"write_section({name!r}): backup rotation {src}→{dst} failed: {e}")
            # current → bak.1
            try:
                path.replace(_backup_path(path, 1))
            except Exception as e:
                logger.warning(f"write_section({name!r}): could not move current to bak.1: {e}")

        # Step 4: atomic rename
        os.replace(tmp_str, path)
        tmp_path = None  # success — don't unlink in finally
        logger.debug(f"write_section({name!r}): wrote {path}")
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
