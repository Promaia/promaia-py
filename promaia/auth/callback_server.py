"""OAuth flow via the proxy's cross-device session endpoints.

Flow:
1. POST /auth/{provider}/sessions → get session_id, user_code, auth_url
2. Display auth_url + user_code to the user
3. Poll GET /auth/{provider}/sessions/{session_id} until completed
4. Return tokens

The proxy URL defaults to our hosted instance but can be overridden
via the ``PROMAIA_OAUTH_PROXY_URL`` environment variable.
"""

from __future__ import annotations

import asyncio
import os

import httpx

DEFAULT_PROXY_URL = "https://oauth.promaia.workers.dev"


class OAuthError(Exception):
    """Raised when an OAuth flow fails."""


async def run_oauth_flow(
    provider: str,
    scopes: str | None = None,
    timeout: float = 300.0,
    poll_interval: float = 3.0,
    display_callback: callable | None = None,
    *,
    client_id: str | None = None,
) -> dict:
    """Run a proxy-based OAuth flow and return the token/code dict.

    1. Creates a cross-device session on the proxy
    2. Calls *display_callback(info)* with auth URL and user code
    3. Polls the proxy until the user completes auth or timeout

    When *client_id* is provided, the proxy runs in **passthrough** mode:
    the poll response contains ``code`` and ``redirect_uri`` instead of
    tokens.  The caller is responsible for exchanging the code.

    Raises :class:`OAuthError` on timeout, proxy error, or provider error.
    """
    proxy_url = os.environ.get(
        "PROMAIA_OAUTH_PROXY_URL", DEFAULT_PROXY_URL
    ).rstrip("/")

    # 1. Create session
    session = await _create_session(proxy_url, provider, scopes, client_id=client_id)

    # 2. Display auth info
    if display_callback:
        display_callback(session)

    # 3. Poll for result (tokens or code)
    result = await _poll_for_result(
        proxy_url, provider, session["session_id"],
        interval=poll_interval, timeout=timeout,
    )

    if result is None:
        raise OAuthError("OAuth flow timed out — no response within 5 minutes")

    return result


async def exchange_code_direct(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange an authorization code for tokens directly with Google.

    Used in passthrough (user-owned OAuth) mode — the proxy is not involved.

    Returns ``{ access_token, refresh_token, expires_in, scope, token_type }``.
    Raises :class:`OAuthError` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
    except httpx.ConnectError:
        raise OAuthError("Could not connect to Google — check your internet")
    except httpx.TimeoutException:
        raise OAuthError("Google token exchange timed out")

    if resp.status_code == 200:
        return resp.json()

    raise OAuthError(f"Token exchange failed: HTTP {resp.status_code}: {resp.text[:200]}")


async def _create_session(
    proxy_url: str, provider: str, scopes: str | None,
    *, client_id: str | None = None,
) -> dict:
    """POST /auth/{provider}/sessions → session info.

    Returns ``{ session_id, user_code, auth_url, expires_at }``.
    Raises :class:`OAuthError` if the proxy is unreachable or returns an error.
    """
    url = f"{proxy_url}/auth/{provider}/sessions"
    body = {}
    if scopes:
        body["scopes"] = scopes
    if client_id:
        body["mode"] = "passthrough"
        body["client_id"] = client_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body)
    except httpx.ConnectError:
        raise OAuthError(
            "Could not connect to OAuth proxy — check your internet"
        )
    except httpx.TimeoutException:
        raise OAuthError("OAuth proxy request timed out")

    if resp.status_code == 201:
        return resp.json()

    raise OAuthError(
        f"OAuth proxy returned HTTP {resp.status_code}: {resp.text[:200]}"
    )


async def _poll_for_result(
    proxy_url: str,
    provider: str,
    session_id: str,
    interval: float = 3.0,
    timeout: float = 300.0,
) -> dict | None:
    """Poll ``GET /auth/{provider}/sessions/{session_id}`` until complete.

    Returns the completed response dict, or ``None`` on timeout/expiry.

    For default mode, the dict contains ``tokens``.
    For passthrough mode, it contains ``code`` and ``redirect_uri``.

    Raises :class:`OAuthError` on explicit error from the proxy.
    """
    url = f"{proxy_url}/auth/{provider}/sessions/{session_id}"
    deadline = asyncio.get_event_loop().time() + timeout

    async with httpx.AsyncClient(timeout=10.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data["status"] == "completed":
                        # Passthrough returns code; default returns tokens
                        if data.get("mode") == "passthrough":
                            return {
                                "mode": "passthrough",
                                "code": data["code"],
                                "redirect_uri": data["redirect_uri"],
                            }
                        return data["tokens"]
                    if data["status"] == "error":
                        raise OAuthError(
                            data.get("error", "Provider denied access")
                        )
                    if data["status"] == "expired":
                        return None
            except OAuthError:
                raise
            except Exception:
                pass  # transient network error, retry

            await asyncio.sleep(interval)

    return None
