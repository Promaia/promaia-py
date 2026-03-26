"""
Workflow storage — SQLite-backed CRUD for saved workflows and example runs.

Workflows are stored in the hybrid_metadata.db alongside other Promaia data.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / "data" / "hybrid_metadata.db"


def _get_conn() -> sqlite3.Connection:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL,
            steps TEXT NOT NULL,
            workspace TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            tool_calls TEXT NOT NULL,
            outcome TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        );
    """)


# ── CRUD ────────────────────────────────────────────────────────────────


def create_workflow(
    name: str,
    description: str,
    steps: List[Dict],
    workspace: Optional[str] = None,
    example_run: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Create a new workflow. Returns the created workflow dict."""
    conn = _get_conn()
    try:
        _ensure_tables(conn)

        workflow_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()

        conn.execute(
            "INSERT INTO workflows (id, name, description, steps, workspace, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workflow_id, name, description, json.dumps(steps), workspace, now, now),
        )

        # Save example run if provided
        run_id = None
        if example_run:
            run_id = _insert_run(conn, workflow_id, example_run)

        conn.commit()

        result = {
            "id": workflow_id,
            "name": name,
            "description": description,
            "steps": steps,
            "workspace": workspace,
            "created_at": now,
        }
        if run_id:
            result["example_run_id"] = run_id

        return result

    except sqlite3.IntegrityError:
        raise ValueError(f"Workflow '{name}' already exists")
    finally:
        conn.close()


def list_saved_workflows(workspace: Optional[str] = None) -> List[Dict]:
    """List all workflows (name + description). Optionally filter by workspace."""
    conn = _get_conn()
    try:
        _ensure_tables(conn)

        if workspace:
            rows = conn.execute(
                "SELECT id, name, description, workspace, created_at, updated_at "
                "FROM workflows WHERE workspace = ? OR workspace IS NULL "
                "ORDER BY name",
                (workspace,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, description, workspace, created_at, updated_at "
                "FROM workflows ORDER BY name"
            ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_workflow_details(name: str) -> Optional[Dict]:
    """Get full workflow definition + example runs by name."""
    conn = _get_conn()
    try:
        _ensure_tables(conn)

        row = conn.execute(
            "SELECT * FROM workflows WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None

        workflow = dict(row)
        workflow["steps"] = json.loads(workflow["steps"])

        # Load example runs
        runs = conn.execute(
            "SELECT id, tool_calls, outcome, notes, created_at "
            "FROM workflow_runs WHERE workflow_id = ? ORDER BY created_at",
            (workflow["id"],),
        ).fetchall()

        workflow["example_runs"] = []
        for run in runs:
            run_dict = dict(run)
            run_dict["tool_calls"] = json.loads(run_dict["tool_calls"])
            workflow["example_runs"].append(run_dict)

        return workflow
    finally:
        conn.close()


def update_workflow(
    name: str,
    description: Optional[str] = None,
    steps: Optional[List[Dict]] = None,
    add_example_run: Optional[Dict] = None,
) -> str:
    """Update an existing workflow. Returns status message."""
    conn = _get_conn()
    try:
        _ensure_tables(conn)

        row = conn.execute(
            "SELECT id FROM workflows WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return f"Workflow '{name}' not found."

        workflow_id = row["id"]
        now = datetime.now().isoformat()
        changes = []

        if description is not None:
            conn.execute(
                "UPDATE workflows SET description = ?, updated_at = ? WHERE id = ?",
                (description, now, workflow_id),
            )
            changes.append("description")

        if steps is not None:
            conn.execute(
                "UPDATE workflows SET steps = ?, updated_at = ? WHERE id = ?",
                (json.dumps(steps), now, workflow_id),
            )
            changes.append("steps")

        if add_example_run:
            run_id = _insert_run(conn, workflow_id, add_example_run)
            conn.execute(
                "UPDATE workflows SET updated_at = ? WHERE id = ?",
                (now, workflow_id),
            )
            changes.append(f"example run ({run_id})")

        conn.commit()

        if changes:
            return f"Workflow '{name}' updated: {', '.join(changes)}"
        return "No changes provided."
    finally:
        conn.close()


def delete_workflow(name: str) -> str:
    """Delete a workflow and its example runs. Returns status message."""
    conn = _get_conn()
    try:
        _ensure_tables(conn)

        row = conn.execute(
            "SELECT id FROM workflows WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return f"Workflow '{name}' not found."

        conn.execute("DELETE FROM workflows WHERE id = ?", (row["id"],))
        conn.commit()
        return f"Workflow '{name}' deleted."
    finally:
        conn.close()


def list_workflows_for_prompt(workspace: Optional[str] = None) -> List[Dict]:
    """Return workflow summaries for system prompt injection.

    Returns list of {"name": ..., "description": ...} dicts.
    """
    workflows = list_saved_workflows(workspace)
    return [{"name": w["name"], "description": w["description"]} for w in workflows]


# ── Internal helpers ────────────────────────────────────────────────────


def _insert_run(conn: sqlite3.Connection, workflow_id: str, run_data: Dict) -> str:
    """Insert an example run. Returns the run ID."""
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()

    tool_calls = run_data.get("tool_calls", [])
    outcome = run_data.get("outcome", "success")
    notes = run_data.get("notes", "")

    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_id, tool_calls, outcome, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, workflow_id, json.dumps(tool_calls), outcome, notes, now),
    )
    return run_id
