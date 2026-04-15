"""Slack bot integration — two tokens (bot + app) for Socket Mode.

The bot token (xoxb-) is used for REST API calls.
The app token (xapp-) is used for Socket Mode WebSocket connection.
Both are required. Neither can be obtained via OAuth — the user creates
the Slack app via a manifest redirect and copies the tokens manually.

Storage:
  - Global:    ``maia-data/credentials/slack_credentials.json``
  - Workspace: ``maia-data/credentials/{workspace}/slack_credentials.json``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from promaia.auth.base import AuthMode, Integration

logger = logging.getLogger(__name__)


class SlackIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="slack",
            display_name="Slack",
            auth_modes=[AuthMode.API_KEY],
            key_url="https://api.slack.com/apps",
            help_lines=[
                "To connect Slack:",
                "  1. Visit the link above to create your Slack bot",
                "  2. Copy the Bot Token (xoxb-...) from OAuth & Permissions",
                "  3. Copy the App Token (xapp-...) from Basic Information",
            ],
        )

    # ── path helpers ──────────────────────────────────────────────────

    @staticmethod
    def _cred_path(workspace: str | None = None):
        from promaia.utils.env_writer import get_data_dir
        base = get_data_dir() / "credentials"
        if workspace:
            return base / workspace / "slack_credentials.json"
        return base / "slack_credentials.json"

    # ── credential access ─────────────────────────────────────────────

    def get_slack_credentials(self, workspace: str | None = None) -> dict | None:
        """Return slack credentials dict, or None.

        Resolution order: workspace-specific -> global -> None.
        """
        paths = []
        if workspace:
            paths.append(self._cred_path(workspace))
        paths.append(self._cred_path())  # global

        for p in paths:
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text())
                if data.get("bot_token") and data.get("app_token"):
                    return data
            except Exception:
                continue
        return None

    def get_default_credential(self) -> str | None:
        """Return bot token for credential check."""
        try:
            from promaia.config.workspaces import get_workspace_manager
            ws = get_workspace_manager().get_default_workspace() or "default"
        except Exception:
            ws = "default"
        creds = self.get_slack_credentials(ws)
        return creds.get("bot_token") if creds else None

    def store_credential(self, value: str, **kwargs) -> None:
        """Store both tokens. value=bot_token, app_token in kwargs."""
        workspace = kwargs.get("workspace")
        path = self._cred_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "bot_token": value,
            "app_token": kwargs.get("app_token", ""),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        }
        if workspace:
            data["workspace"] = workspace

        path.write_text(json.dumps(data, indent=2) + "\n")

    def clear_credential(self) -> None:
        for path in [self._cred_path(), self._cred_path("default")]:
            if path.exists():
                path.unlink()

    # ── validation ────────────────────────────────────────────────────

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        """Validate a bot token via Slack auth.test."""
        if not value or not value.startswith("xoxb-"):
            return False, "Bot token should start with xoxb-"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {value}"},
                )
            data = resp.json()
            if data.get("ok"):
                team = data.get("team", "Unknown")
                self._last_validated_team = team
                return True, f"Connected to workspace: {team}"
            else:
                error = data.get("error", "unknown error")
                return False, f"Slack auth failed: {error}"
        except httpx.TimeoutException:
            return False, "Connection timed out"
        except httpx.ConnectError:
            return False, "Could not connect to Slack API"
        except Exception as e:
            return False, f"Validation error: {e}"
