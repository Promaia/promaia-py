"""Persistent Slack-wide settings (applies to every Slack conversation).

Stored as JSON under ``get_data_dir() / "slack" / "settings.json"``.
"""

import json
import logging
from typing import Optional

from promaia.utils.env_writer import get_data_dir

logger = logging.getLogger(__name__)

DEFAULT_SLACK_MODEL = "claude-opus-4-6-1m"


def _settings_path():
    return get_data_dir() / "slack" / "settings.json"


def _load() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"Failed to read Slack settings at {path}: {e}")
        return {}


def _save(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def get_slack_model() -> str:
    """Return the model to use for every Slack conversation."""
    return _load().get("model") or DEFAULT_SLACK_MODEL


def set_slack_model(model_id: Optional[str]) -> None:
    """Persist the Slack-wide model. Pass None to reset to the default."""
    data = _load()
    if model_id is None:
        data.pop("model", None)
    else:
        data["model"] = model_id
    _save(data)
