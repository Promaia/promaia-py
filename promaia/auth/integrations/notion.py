"""Notion integration — OAuth (preferred) or API key.

One ``maia auth configure notion`` stores credentials as a JSON token
file, mirroring Google's pattern.

Storage:
  - Global:    ``maia-data/credentials/notion/token.json``
  - Workspace: ``maia-data/credentials/notion/{workspace}/token.json``
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx

from promaia.auth.base import AuthMode, Integration

logger = logging.getLogger(__name__)


class NotionIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="notion",
            display_name="Notion",
            auth_modes=[AuthMode.OAUTH, AuthMode.API_KEY],
            oauth_provider="notion",
            key_url="https://www.notion.so/profile/integrations",
            key_prefix="secret_",
            help_lines=[
                "To get your Notion API key:",
                "  1. Go to https://www.notion.so/profile/integrations",
                "  2. Click 'New integration'",
                "  3. Copy the 'Internal Integration Secret' (starts with secret_)",
                "  4. Share your Notion pages/databases with the integration",
            ],
        )
        self._env_migrated = False

    # ── token path helpers ────────────────────────────────────────────

    @staticmethod
    def _token_path(workspace: str | None = None):
        from promaia.utils.env_writer import get_data_dir

        base = get_data_dir() / "credentials" / "notion"
        if workspace:
            return base / workspace / "token.json"
        return base / "token.json"

    # ── one-time legacy migration ──────────────────────────────────────

    def _maybe_migrate_legacy_tokens(self) -> None:
        """One-time migration of Notion tokens from legacy locations.

        Checks two sources (once per process):
          1. ``NOTION_TOKEN`` / ``NOTION_API_KEY`` env vars → global token file
          2. Workspace config ``api_key`` fields → per-workspace token files

        Skips any token file that already exists so user-configured
        credentials are never overwritten.
        """
        if self._env_migrated:
            return
        self._env_migrated = True

        # --- env vars → global token file ---
        global_path = self._token_path()
        if not global_path.exists():
            token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY")
            if token:
                self._write_token_file(global_path, token)
                logger.info("Migrated Notion token from environment to credentials store.")

        # --- workspace config api_key → per-workspace token files ---
        try:
            from promaia.config.workspaces import get_workspace_manager
            manager = get_workspace_manager()
            for name, ws in manager.workspaces.items():
                if not ws.api_key:
                    continue
                ws_path = self._token_path(name)
                if ws_path.exists():
                    continue
                self._write_token_file(ws_path, ws.api_key)
                logger.info(f"Migrated Notion token for workspace '{name}' to credentials store.")
        except Exception:
            # Workspace config may not exist yet (fresh install, etc.)
            pass

    @staticmethod
    def _write_token_file(path, token: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        token_data = {
            "access_token": token,
            "mode": "api_key",
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(token_data, indent=2) + "\n")

    # ── credential storage (global-by-default) ────────────────────────

    def get_default_credential(self) -> str | None:
        """Return the global access token, or None."""
        return self.get_notion_credentials()

    def store_credential(self, value: str, **kwargs) -> None:
        """Store token response as JSON.

        If ``workspace`` or ``workspace_name`` kwarg is provided, stores
        per-workspace; otherwise stores globally.
        """
        workspace = kwargs.get("workspace_name") or kwargs.get("workspace")
        path = self._token_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Determine mode from token prefix or explicit kwarg
        if kwargs.get("mode"):
            mode = kwargs["mode"]
        elif value.startswith("secret_"):
            mode = "api_key"
        else:
            mode = "oauth"

        token_data = {
            "access_token": value,
            "mode": mode,
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        }

        # OAuth responses may include these
        if kwargs.get("refresh_token"):
            token_data["refresh_token"] = kwargs["refresh_token"]
        if kwargs.get("expires_in"):
            token_data["expires_in"] = kwargs["expires_in"]
        if kwargs.get("workspace_id"):
            token_data["workspace_id"] = kwargs["workspace_id"]
        if kwargs.get("bot_id"):
            token_data["bot_id"] = kwargs["bot_id"]

        path.write_text(json.dumps(token_data, indent=2) + "\n")

    def clear_credential(self) -> None:
        """Remove the global token file."""
        path = self._token_path()
        if path.exists():
            path.unlink()

    # ── Notion-specific: get token string ─────────────────────────────

    def get_notion_credentials(self, workspace: str | None = None) -> str | None:
        """Return a Notion API token string, or None.

        Resolution order: workspace-specific → global → None.
        Triggers one-time env migration on first call.

        Notion tokens (both API keys and OAuth) do not expire, so no
        refresh logic is needed.  The structure supports adding refresh
        later if Notion changes their OAuth model.
        """
        self._maybe_migrate_legacy_tokens()

        paths = []
        if workspace:
            paths.append(self._token_path(workspace))
        paths.append(self._token_path())  # always check global

        for p in paths:
            if not p.exists():
                continue
            try:
                token_data = json.loads(p.read_text())
            except Exception:
                continue

            access_token = token_data.get("access_token")
            if not access_token:
                continue

            # Structural hook for future refresh support
            if (
                token_data.get("mode") == "oauth"
                and token_data.get("refresh_token")
                and token_data.get("expires_in")
            ):
                from promaia.auth.token_refresh import is_token_expired

                if is_token_expired(token_data):
                    # Notion doesn't currently expire tokens, but if they
                    # ever do, add refresh logic here (mirroring Google's).
                    logger.warning(
                        "Notion OAuth token appears expired but refresh "
                        "is not yet supported. Re-run: maia auth configure notion"
                    )
                    return None

            return access_token

        return None

    # ── validation ──────────────────────────────────────────────────

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        if not (value.startswith("secret_") or value.startswith("ntn_")):
            return False, "Notion keys start with 'secret_' or 'ntn_'"

        url = "https://api.notion.com/v1/users/me"
        headers = {
            "Authorization": f"Bearer {value}",
            "Notion-Version": "2022-06-28",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                name = data.get("name", "Unknown")
                return True, f"Connected as {name}"
            elif resp.status_code == 401:
                return False, "Invalid API key (authentication failed)"
            elif resp.status_code == 403:
                return False, "API key lacks required permissions"
            else:
                return False, f"Unexpected response: HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet connection"
        except httpx.ConnectError:
            return False, "Could not connect to Notion API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
