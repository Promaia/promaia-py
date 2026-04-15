"""Persistent memory storage for Promaia agents.

Stores memories as markdown files with YAML frontmatter, per workspace.
MEMORY.md is a compact index loaded into every prompt. Topic files are
loaded on demand via the memory tool.

Storage layout:
    maia-data/memory/{workspace}/
    ├── MEMORY.md              # Index (always in prompt)
    ├── user_preferences.md
    ├── project_context.md
    └── ...
"""

import datetime
import logging
import re
from pathlib import Path
from typing import Optional

from promaia.utils.env_writer import get_data_dir

logger = logging.getLogger(__name__)


def get_memory_dir(workspace: str) -> Path:
    """Return the memory directory for a workspace, creating it if needed."""
    mem_dir = get_data_dir() / "memory" / workspace
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir


def load_memory_index(workspace: str) -> str:
    """Load MEMORY.md content. Returns empty string if no memories exist."""
    if not workspace:
        return ""
    index_path = get_memory_dir(workspace) / "MEMORY.md"
    try:
        if index_path.exists():
            content = index_path.read_text().strip()
            # Cap at 100 lines to keep prompt lean
            lines = content.split("\n")
            if len(lines) > 100:
                content = "\n".join(lines[:100]) + "\n\n[... truncated, use memory(action='list') for full index]"
            return content
    except Exception as e:
        logger.warning(f"Failed to load memory index: {e}")
    return ""


def load_memory_file(workspace: str, name: str) -> str:
    """Load a specific memory topic file. Returns content or error message."""
    if not workspace or not name:
        return "Error: workspace and name are required."
    mem_dir = get_memory_dir(workspace)
    # Sanitize name to filename
    filename = _name_to_filename(name)
    file_path = mem_dir / filename
    if not file_path.exists():
        # Try with .md extension
        file_path = mem_dir / f"{filename}.md"
    if not file_path.exists():
        available = [f.stem for f in mem_dir.glob("*.md") if f.name != "MEMORY.md"]
        return f"Memory '{name}' not found. Available: {', '.join(available) or '(none)'}"
    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading memory '{name}': {e}"


def save_memory(workspace: str, name: str, content: str, mem_type: str = "project") -> str:
    """Save a memory: write topic file with frontmatter, update MEMORY.md index.

    Returns confirmation message.
    """
    if not workspace:
        return "Error: no workspace set."
    if not name:
        return "Error: memory name is required."
    if not content:
        return "Error: memory content is required."

    mem_dir = get_memory_dir(workspace)
    filename = _name_to_filename(name) + ".md"
    file_path = mem_dir / filename
    today = datetime.date.today().isoformat()

    # Build frontmatter
    is_update = file_path.exists()
    created = today
    if is_update:
        # Preserve original created date
        existing = file_path.read_text()
        created_match = re.search(r"created:\s*(\S+)", existing)
        if created_match:
            created = created_match.group(1)

    file_content = f"""---
type: {mem_type}
created: {created}
updated: {today}
---

{content}
"""
    file_path.write_text(file_content)

    # Update MEMORY.md index
    _update_index(mem_dir, name, filename, content)

    action = "updated" if is_update else "saved"
    logger.info(f"Memory {action}: {name} ({filename}, {len(content)} chars)")
    return f"Memory '{name}' {action} ({len(content)} chars)."


def delete_memory(workspace: str, name: str) -> str:
    """Delete a memory entry and its topic file."""
    if not workspace or not name:
        return "Error: workspace and name are required."

    mem_dir = get_memory_dir(workspace)
    filename = _name_to_filename(name) + ".md"
    file_path = mem_dir / filename

    if not file_path.exists():
        return f"Memory '{name}' not found."

    file_path.unlink()
    _remove_from_index(mem_dir, name, filename)
    logger.info(f"Memory deleted: {name}")
    return f"Memory '{name}' deleted."


# ── Internal helpers ──────────────────────────────────────────────────────

def _name_to_filename(name: str) -> str:
    """Convert a memory name to a safe filename (without extension)."""
    # Lowercase, replace spaces/special chars with underscores
    safe = re.sub(r"[^\w\s-]", "", name.lower())
    safe = re.sub(r"[\s-]+", "_", safe).strip("_")
    return safe[:60]  # Cap length


def _summarize_for_index(content: str) -> str:
    """Create a one-line summary from memory content for the index."""
    # Take first line or first 100 chars
    first_line = content.strip().split("\n")[0].strip()
    if len(first_line) > 100:
        first_line = first_line[:97] + "..."
    return first_line


def _update_index(mem_dir: Path, name: str, filename: str, content: str):
    """Add or update an entry in MEMORY.md."""
    index_path = mem_dir / "MEMORY.md"
    summary = _summarize_for_index(content)
    new_entry = f"- [{name}]({filename}) — {summary}"

    if index_path.exists():
        index_content = index_path.read_text()
        # Check if entry already exists (by filename reference)
        pattern = re.compile(rf"^- \[.*?\]\({re.escape(filename)}\).*$", re.MULTILINE)
        if pattern.search(index_content):
            # Update existing entry
            index_content = pattern.sub(new_entry, index_content)
        else:
            # Append new entry
            index_content = index_content.rstrip() + "\n" + new_entry + "\n"
        index_path.write_text(index_content)
    else:
        # Create new index
        index_path.write_text(f"# Memory\n\n{new_entry}\n")


def _remove_from_index(mem_dir: Path, name: str, filename: str):
    """Remove an entry from MEMORY.md."""
    index_path = mem_dir / "MEMORY.md"
    if not index_path.exists():
        return
    index_content = index_path.read_text()
    pattern = re.compile(rf"^- \[.*?\]\({re.escape(filename)}\).*\n?", re.MULTILINE)
    index_content = pattern.sub("", index_content)
    index_path.write_text(index_content)
