"""
Core workflow registry for Promaia interviews.

Core workflows are built-in, kernel-level interview flows that guide users
through system configuration tasks. They are always available and don't
depend on any external connector or synced data.
"""

from collections import defaultdict
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


def get_workflow(name: str, context: Optional[Dict] = None) -> Optional[Dict]:
    """Get a workflow by name, optionally resolving template variables.

    If context is provided, any {key} placeholders in the system_prompt_insert
    are resolved using the context dict. Unknown keys resolve to "unknown".
    """
    wf = CORE_WORKFLOWS.get(name)
    if not wf:
        return None
    if context:
        resolved = dict(wf)
        safe_ctx = defaultdict(lambda: "unknown", context)
        resolved["system_prompt_insert"] = wf["system_prompt_insert"].format_map(safe_ctx)
        return resolved
    return wf


def list_workflows() -> List[Dict]:
    """List all available workflows (name + description only)."""
    return [
        {"name": w["name"], "description": w["description"]}
        for w in CORE_WORKFLOWS.values()
    ]


# Auto-register built-in workflows on import
from promaia.chat.workflows import database_add as _  # noqa: F401
from promaia.chat.workflows import edit_channels as _ec  # noqa: F401
from promaia.chat.workflows import create_agent as _ca  # noqa: F401
from promaia.chat.workflows import agent_edit as _ae  # noqa: F401
from promaia.chat.workflows import database_edit as _de  # noqa: F401
from promaia.chat.workflows import onboarding_agent as _oa  # noqa: F401
from promaia.chat.workflows import onboard_tutorial as _ot  # noqa: F401
