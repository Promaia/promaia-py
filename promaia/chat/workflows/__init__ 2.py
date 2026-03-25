"""
Core workflow registry for Promaia interviews.

Core workflows are built-in, kernel-level interview flows that guide users
through system configuration tasks. They are always available and don't
depend on any external connector or synced data.
"""

from typing import Dict, List, Optional

# Registry: workflow_name -> {"name", "description", "system_prompt_insert"}
CORE_WORKFLOWS: Dict[str, Dict] = {}


def register_workflow(name: str, description: str, system_prompt_insert: str):
    """Register a core workflow."""
    CORE_WORKFLOWS[name] = {
        "name": name,
        "description": description,
        "system_prompt_insert": system_prompt_insert,
    }


def get_workflow(name: str) -> Optional[Dict]:
    """Get a workflow by name."""
    return CORE_WORKFLOWS.get(name)


def list_workflows() -> List[Dict]:
    """List all available workflows (name + description only)."""
    return [
        {"name": w["name"], "description": w["description"]}
        for w in CORE_WORKFLOWS.values()
    ]


# Auto-register built-in workflows on import
from promaia.chat.workflows import database_add as _  # noqa: F401
from promaia.chat.workflows import edit_channels as _ec  # noqa: F401
