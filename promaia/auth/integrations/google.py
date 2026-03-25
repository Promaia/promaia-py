"""Unified Google integration — OAuth for Gmail + Calendar.

Tokens are stored **per-account** (keyed by Google email address):

    ``maia-data/credentials/google/{email}/token.json``

A legacy global path (``maia-data/credentials/google/token.json``) is
checked as a fallback for backward compatibility during migration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from promaia.auth.base import AuthMode, Integration
from promaia.auth.token_refresh import (
    is_token_expired,
    refresh_google_token,
    refresh_google_token_direct,
)

logger = logging.getLogger(__name__)


class GoogleIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="google",
            display_name="Google",
            auth_modes=[AuthMode.OAUTH, AuthMode.USER_OAUTH],
            oauth_provider="google",
            key_url="https://myaccount.google.com",
            help_lines=[
                "Authorize Google access for Gmail and Calendar.",
                "This grants read/send email and calendar management permissions.",
            ],
        )
        self.oauth_scopes = " ".join([
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])

    # ── token path helpers ────────────────────────────────────────────

    @staticmethod
    def _token_path(account: str | None = None):
        """Return the token path for a given account (email) or the legacy global path."""
        from promaia.utils.env_writer import get_data_dir

        base = get_data_dir() / "credentials" / "google"
        if account:
            return base / account.lower() / "token.json"
        return base / "token.json"

    # ── credential storage (account-scoped) ───────────────────────────

    def get_default_credential(self) -> str | None:
        """Return any available access token.

        Checks the first authenticated account, then falls back to the
        legacy global token.  Use ``get_account_credential()`` when you
        need a specific account's token.
        """
        accounts = self.list_authenticated_accounts()
        if accounts:
            return self.get_account_credential(accounts[0])
        # Legacy global fallback
        return self.get_account_credential(None)

    def get_account_credential(self, account: str | None) -> str | None:
        """Return the access token for a specific account, or None.

        Falls back to the legacy global token when *account* is given but
        no account-specific token exists.
        """
        path = self._token_path(account)
        if not path.exists():
            if account:
                path = self._token_path()
                if not path.exists():
                    return None
            else:
                return None
        try:
            data = json.loads(path.read_text())
            return data.get("access_token") or None
        except Exception:
            return None

    def store_credential(self, value: str, **kwargs) -> None:
        """Store full token response as JSON.

        If ``account`` kwarg is provided (email address), stores per-account.
        Falls back to ``workspace`` for backward compat, then global.

        For user-owned OAuth (``mode="user_oauth"``), the ``client_id``
        and ``client_secret`` are persisted alongside the tokens so that
        token refresh can go directly to Google without the proxy.
        """
        account = kwargs.get("account") or kwargs.get("workspace")
        path = self._token_path(account)
        path.parent.mkdir(parents=True, exist_ok=True)

        token_data = {
            "access_token": value,
            "refresh_token": kwargs.get("refresh_token"),
            "expires_in": kwargs.get("expires_in", 3600),
            "scope": kwargs.get("scope", ""),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        }

        # User-owned OAuth: persist client credentials for direct refresh
        if kwargs.get("mode") == "user_oauth":
            token_data["mode"] = "user_oauth"
            token_data["client_id"] = kwargs["client_id"]
            token_data["client_secret"] = kwargs["client_secret"]

        path.write_text(json.dumps(token_data, indent=2) + "\n")

    def clear_credential(self) -> None:
        """Remove the legacy global token file (contract-compliant, zero-arg).

        Use ``clear_account_credential()`` to revoke a specific account.
        """
        path = self._token_path()
        if path.exists():
            path.unlink()

    def clear_account_credential(self, account: str) -> None:
        """Remove the token file for a specific Google account."""
        path = self._token_path(account)
        if path.exists():
            path.unlink()

    # ── account listing / discovery ───────────────────────────────────

    def list_authenticated_accounts(self) -> list[str]:
        """Return email addresses that have stored credentials."""
        from promaia.utils.env_writer import get_data_dir

        base = get_data_dir() / "credentials" / "google"
        accounts = []
        if base.exists():
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "token.json").exists():
                    accounts.append(child.name)
        return accounts

    @staticmethod
    async def get_authenticated_email(access_token: str) -> str | None:
        """Fetch the email associated with an access token from Google."""
        url = f"https://oauth2.googleapis.com/tokeninfo?access_token={access_token}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json().get("email")
        except Exception:
            pass
        return None

    # ── Google-specific: get Credentials object ──────────────────────

    def get_google_credentials(self, account: str | None = None, **kwargs):
        """Return a ``google.oauth2.credentials.Credentials`` object.

        Refreshes the token via the proxy if expired.

        Resolution order: account-specific → legacy global → ``None``.

        Args:
            account: Google account email address.
            **kwargs: Accepts deprecated ``workspace`` for backward compat.
        """
        # Backward compat: accept workspace= as account
        if not account:
            account = kwargs.get("workspace")

        # Build candidate paths
        paths = []
        if account:
            paths.append(self._token_path(account))
        paths.append(self._token_path())  # legacy global fallback

        token_data = None
        token_path = None
        for p in paths:
            if p.exists():
                try:
                    token_data = json.loads(p.read_text())
                    token_path = p
                    break
                except Exception:
                    continue

        if not token_data:
            return None

        # Refresh if expired
        if is_token_expired(token_data):
            refresh_token = token_data.get("refresh_token")
            if not refresh_token:
                return None
            try:
                if token_data.get("mode") == "user_oauth":
                    new_tokens = refresh_google_token_direct(
                        refresh_token,
                        token_data["client_id"],
                        token_data["client_secret"],
                    )
                else:
                    new_tokens = refresh_google_token(refresh_token)
            except Exception:
                return None

            token_data["access_token"] = new_tokens["access_token"]
            if "refresh_token" in new_tokens:
                token_data["refresh_token"] = new_tokens["refresh_token"]
            token_data["expires_in"] = new_tokens.get("expires_in", 3600)
            token_data["obtained_at"] = datetime.now(timezone.utc).isoformat()
            token_path.write_text(json.dumps(token_data, indent=2) + "\n")

        from google.oauth2.credentials import Credentials

        return Credentials(
            token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
        )

    # ── validation ───────────────────────────────────────────────────

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        """Validate by checking token info (works with any scope)."""
        url = f"https://oauth2.googleapis.com/tokeninfo?access_token={value}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                email = data.get("email", "")
                scope = data.get("scope", "")
                label = f"Connected as {email}" if email else "Token valid"
                if scope:
                    label += f" (scopes: {scope})"
                return True, label
            else:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet"
        except httpx.ConnectError:
            return False, "Could not connect to Google API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
