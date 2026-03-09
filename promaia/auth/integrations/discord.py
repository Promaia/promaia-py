"""Discord bot token integration.

Stores credentials globally by default, with optional workspace-scoped
overrides.

Storage:
  - Global:    ``maia-data/credentials/discord_credentials.json``
  - Workspace: ``maia-data/credentials/{workspace}/discord_credentials.json``
"""

from __future__ import annotations

import json

import httpx

from promaia.auth.base import AuthMode, Integration


class DiscordIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="discord",
            display_name="Discord Bot",
            auth_modes=[AuthMode.API_KEY],
            key_url="https://discord.com/developers/applications",
            help_lines=[
                "To get your Discord bot token:",
                "  1. Go to https://discord.com/developers/applications",
                "  2. Select your application (or create one)",
                "  3. Go to 'Bot' section, click 'Reset Token' or 'Copy'",
            ],
        )

    # ── path helpers ──────────────────────────────────────────────────

    @staticmethod
    def _cred_path(workspace: str):
        from promaia.utils.env_writer import get_data_dir
        return get_data_dir() / "credentials" / workspace / "discord_credentials.json"

    @staticmethod
    def _global_cred_path():
        from promaia.utils.env_writer import get_data_dir
        return get_data_dir() / "credentials" / "discord_credentials.json"

    # ── storage (global-by-default, workspace override) ───────────────

    def get_discord_token(self, workspace: str | None = None) -> str | None:
        """Return a Discord bot token string, or None.

        Resolution order: workspace-specific → global → None.
        """
        paths = []
        if workspace:
            paths.append(self._cred_path(workspace))
        paths.append(self._global_cred_path())

        for p in paths:
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text())
                token = data.get("bot_token")
                if token:
                    return token
            except Exception:
                continue

        return None

    def get_default_credential(self) -> str | None:
        try:
            from promaia.config.workspaces import get_workspace_manager
            mgr = get_workspace_manager()
            ws_name = mgr.get_default_workspace() or "default"
        except Exception:
            ws_name = "default"

        return self.get_discord_token(ws_name)

        return None

    def store_credential(self, value: str, **kwargs) -> None:
        workspace = kwargs.get("workspace")  # None → global
        if workspace:
            path = self._cred_path(workspace)
        else:
            path = self._global_cred_path()

        path.parent.mkdir(parents=True, exist_ok=True)

        # Preserve existing fields (channel_id, etc.)
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except Exception:
                pass

        data["bot_token"] = value
        path.write_text(json.dumps(data, indent=2) + "\n")

    def clear_credential(self) -> None:
        # Clear from workspace-specific path
        try:
            from promaia.config.workspaces import get_workspace_manager
            mgr = get_workspace_manager()
            ws_name = mgr.get_default_workspace() or "default"
        except Exception:
            ws_name = "default"

        for path in [self._cred_path(ws_name), self._global_cred_path()]:
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    data.pop("bot_token", None)
                    path.write_text(json.dumps(data, indent=2) + "\n")
                except Exception:
                    pass

    # ── validation ────────────────────────────────────────────────────

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        if not value or len(value) < 50:
            return False, "Token too short — Discord bot tokens are typically 70+ characters"

        url = "https://discord.com/api/v10/users/@me"
        headers = {"Authorization": f"Bot {value}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                username = data.get("username", "Unknown")
                return True, f"Connected as {username}"
            elif resp.status_code == 401:
                return False, "Invalid bot token (authentication failed)"
            elif resp.status_code == 403:
                return False, "Bot token lacks required permissions"
            else:
                return False, f"Unexpected response: HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet connection"
        except httpx.ConnectError:
            return False, "Could not connect to Discord API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
