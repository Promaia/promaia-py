"""Local markdown task queue file management.

Provides helpers to read/write a simple checkbox-based task queue
stored at {data_dir}/data/md/promaia/user_queue/queue.md.

This is a user-level queue (not per-workspace), living in the md
content tree alongside synced sources.
"""

from pathlib import Path

from promaia.utils.env_writer import get_data_dir


def _get_queue_dir() -> Path:
    return Path(get_data_dir()) / "data" / "md" / "promaia" / "user_queue"


def get_task_queue_path() -> Path:
    """Return the task queue file path, creating the dir and file if missing."""
    queue_dir = _get_queue_dir()
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_file = queue_dir / "queue.md"
    if not queue_file.exists():
        queue_file.write_text("# Task Queue\n\n")
    return queue_file


def read_task_queue() -> str:
    """Return the full content of the task queue file."""
    return get_task_queue_path().read_text()


def append_task(task: str) -> str:
    """Append an unchecked task and return a confirmation message."""
    path = get_task_queue_path()
    content = path.read_text()
    # Ensure trailing newline before appending
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"- [ ] {task}\n"
    path.write_text(content)
    return f"Added to task queue: {task}"


def mark_task_done(task_substr: str) -> str:
    """Mark the first matching unchecked task as done."""
    path = get_task_queue_path()
    lines = path.read_text().splitlines(keepends=True)
    needle = task_substr.lower()
    for i, line in enumerate(lines):
        if line.strip().startswith("- [ ]") and needle in line.lower():
            lines[i] = line.replace("- [ ]", "- [x]", 1)
            path.write_text("".join(lines))
            return f"Marked done: {line.strip()[6:]}"
    return f"No matching unchecked task found for: {task_substr}"


def task_queue_exists() -> bool:
    """Check if the task queue file exists (without creating it)."""
    queue_file = _get_queue_dir() / "queue.md"
    return queue_file.exists()
