"""Shared token refresh utilities for proxy-based OAuth.

Provides synchronous helpers for refreshing Google OAuth tokens via the
proxy.  Sync because the Gmail connector and Calendar manager auth
methods are sync.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from promaia.auth.callback_server import DEFAULT_PROXY_URL


def refresh_google_token_direct(
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Refresh a Google token directly (no proxy). For user-owned OAuth."""
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Direct token refresh failed: HTTP {resp.status_code}")
    return resp.json()


def refresh_google_token(refresh_token: str) -> dict:
    """Call proxy to refresh a Google OAuth token. Returns new token data."""
    proxy_url = os.environ.get(
        "PROMAIA_OAUTH_PROXY_URL", DEFAULT_PROXY_URL
    ).rstrip("/")
    resp = httpx.post(
        f"{proxy_url}/auth/google/refresh",
        json={"refresh_token": refresh_token},
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: HTTP {resp.status_code}")
    return resp.json()


def is_token_expired(token_data: dict) -> bool:
    """Check if stored token data has expired (with 5-minute buffer)."""
    obtained = token_data.get("obtained_at")
    expires_in = token_data.get("expires_in", 3600)
    if not obtained:
        return True
    obtained_dt = datetime.fromisoformat(obtained)
    elapsed = (datetime.now(timezone.utc) - obtained_dt).total_seconds()
    return elapsed > (expires_in - 300)  # 5-min buffer
