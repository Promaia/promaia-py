"""Shared interactive flow for configuring credentials.

This module provides ``configure_credential()`` which handles the full
interactive UX for any integration — showing help, prompting for a key,
validating, and storing.  It is used by ``maia setup`` and
``maia auth configure``.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.prompt import Prompt

from promaia.auth.base import AuthMode, Integration

logger = logging.getLogger(__name__)

# Re-use the console instance from setup_commands if available,
# otherwise create our own.
console = Console()


async def _check_openrouter_anthropic_conflict(c: Console) -> None:
    """Temporary: if ANTHROPIC_API_KEY is set, OpenRouter won't be used.

    Offer to clear the Anthropic key so the OpenRouter shim activates.
    This is a temporary workaround until proper multi-provider routing is built.
    """
    from promaia.utils.env_writer import read_env_value, update_env_value

    anthropic_key = read_env_value("ANTHROPIC_API_KEY")
    if not anthropic_key:
        return

    c.print()
    c.print("  [bold yellow]Note:[/bold yellow] An Anthropic API key is already configured.")
    c.print("  OpenRouter won't be used while a direct Anthropic key is present.")
    c.print("  [dim](Direct Anthropic keys take priority over OpenRouter.)[/dim]")
    c.print()

    clear = Prompt.ask(
        "  Clear Anthropic API key so OpenRouter is used instead? [Y/n]",
        default="y",
    ).strip().lower()

    if clear in ("y", "yes", ""):
        update_env_value("ANTHROPIC_API_KEY", "")
        c.print("  [green]OK[/green] Anthropic API key cleared. OpenRouter will be used.")
    else:
        c.print("  [dim]Keeping Anthropic key. OpenRouter will not be active.[/dim]")
        c.print("  [dim]To switch later, run: maia auth configure anthropic[/dim]")


async def configure_credential(
    integration: Integration,
    con: Console | None = None,
    workspace: str | None = None,
    account: str | None = None,
) -> bool:
    """Interactive flow to configure a single integration.

    Args:
        integration: The integration to configure.
        con: Rich console for output.
        workspace: If set, store credential for this workspace instead of globally.
        account: Google account email — stores credential per-account and validates
                 that the authenticated email matches after OAuth completes.

    Returns ``True`` if a credential was stored (or already valid),
    ``False`` if the user cancelled.
    """
    c = con or console

    # Determine auth mode
    modes = integration.auth_modes
    if len(modes) > 1:
        mode = await _select_auth_mode(integration, c)
        if mode is None:
            return False
    elif len(modes) == 1:
        mode = modes[0]
    else:
        c.print(f"[red]{integration.display_name}: no auth modes configured[/red]")
        return False

    if mode == AuthMode.OAUTH:
        success = await _configure_oauth(integration, c, workspace=workspace, account=account)
    elif mode == AuthMode.USER_OAUTH:
        success = await _configure_user_oauth(integration, c, workspace=workspace, account=account)
    else:
        success = await _configure_api_key(integration, c, workspace=workspace)

    # Temporary: warn about Anthropic key conflict when setting up OpenRouter
    if success and integration.name == "openrouter":
        await _check_openrouter_anthropic_conflict(c)

    return success


# ── OAuth flow ────────────────────────────────────────────────────────


def _render_qr(url: str, c: Console) -> None:
    """Render a QR code in the terminal. Silently skipped on failure."""
    try:
        import io
        import sys
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf)
        text = buf.getvalue()
        indented = "\n".join("    " + line for line in text.splitlines() if line.strip())
        # Write UTF-8 directly to stdout's binary buffer.  Rich's legacy
        # Windows renderer routes through cp1252 which can't encode the
        # Unicode half-block characters that print_ascii uses.
        sys.stdout.buffer.write(("\n" + indented + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        pass


async def _configure_oauth(
    integration: Integration,
    c: Console,
    *,
    workspace: str | None = None,
    account: str | None = None,
) -> bool:
    """Proxy-based OAuth flow: display URL + code, poll for tokens."""
    from promaia.auth.callback_server import run_oauth_flow, OAuthError

    title = f"{integration.display_name} — OAuth Authorization"
    if account:
        title += f" ({account})"
    c.print(f"\n[bold]{title}[/bold]\n")

    if account:
        c.print(f"  [dim]Please sign in with:[/dim] [bold]{account}[/bold]\n")

    def _show_auth_info(info: dict) -> None:
        c.print(f"  Visit: [link={info['auth_url']}]{info['auth_url']}[/link]\n")
        _render_qr(info["auth_url"], c)
        c.print(f"  Code:  [bold cyan]{info['user_code']}[/bold cyan]\n")
        c.print("  [dim]Waiting for authorization... Ctrl+C to cancel[/dim]\n")

    try:
        tokens = await run_oauth_flow(
            provider=integration.oauth_provider,
            scopes=getattr(integration, "oauth_scopes", None),
            display_callback=_show_auth_info,
        )
    except OAuthError as e:
        c.print(f"  [red]OAuth failed:[/red] {e}")
        return False

    access_token = tokens.get("access_token", "")
    logger.debug("OAuth token keys: %s", list(tokens.keys()))
    if access_token:
        logger.debug("access_token: %s...(%d chars)", access_token[:12], len(access_token))
    else:
        logger.warning("No 'access_token' key in OAuth response: %s", list(tokens.keys()))

    # Validate the authenticated account matches the expected one
    if account and access_token:
        from promaia.auth.integrations.google import GoogleIntegration

        actual_email = await GoogleIntegration.get_authenticated_email(access_token)
        if actual_email and actual_email.lower() != account.lower():
            c.print(f"  [red]Account mismatch:[/red] signed in as [bold]{actual_email}[/bold] but expected [bold]{account}[/bold]")
            c.print(f"  [dim]Please try again and sign in with {account}[/dim]")
            return False

        # Use actual email (preserves original casing from Google)
        if actual_email:
            account = actual_email

    # Let integration store tokens — keyed by account if provided
    store_kwargs = dict(tokens)
    if account:
        store_kwargs["account"] = account
    elif workspace:
        store_kwargs["workspace"] = workspace
    integration.store_credential(access_token, **store_kwargs)
    c.print(f"  [green]OK[/green] {integration.display_name} connected")

    # Validate the stored credential
    if access_token:
        valid, msg = await integration.validate_credential(access_token)
        if valid:
            c.print(f"  [green]OK[/green] {msg}")
        else:
            c.print(f"  [yellow]Warning:[/yellow] {msg}")

    return True


# ── User-owned OAuth flow ─────────────────────────────────────────────


async def _configure_user_oauth(
    integration: Integration,
    c: Console,
    *,
    workspace: str | None = None,
    account: str | None = None,
) -> bool:
    """User-owned OAuth: prompt for credentials, passthrough via proxy."""
    from promaia.auth.callback_server import (
        OAuthError,
        exchange_code_direct,
        run_oauth_flow,
    )

    c.print(f"\n[bold]{integration.display_name} — Your Own OAuth Credentials[/bold]\n")
    c.print("  [dim]To use your own Google OAuth credentials:[/dim]")
    c.print("  [dim]1. Go to https://console.cloud.google.com/apis/credentials[/dim]")
    c.print("  [dim]2. Create an OAuth 2.0 Client ID (Desktop app type)[/dim]")
    c.print("  [dim]3. Add this redirect URI to your app:[/dim]")
    c.print("     [cyan]https://oauth.promaia.workers.dev/auth/google/callback[/cyan]")
    c.print("  [dim]4. Copy the Client ID and Client Secret below[/dim]\n")

    client_id = Prompt.ask("  Client ID").strip()
    if not client_id:
        c.print("  [red]Client ID cannot be empty.[/red]")
        return False

    from prompt_toolkit import PromptSession
    session = PromptSession()
    client_secret = (await session.prompt_async("  Client Secret: ", is_password=True)).strip()
    if not client_secret:
        c.print("  [red]Client Secret cannot be empty.[/red]")
        return False

    def _show_auth_info(info: dict) -> None:
        c.print(f"\n  Visit: [link={info['auth_url']}]{info['auth_url']}[/link]\n")
        _render_qr(info["auth_url"], c)
        c.print(f"  Code:  [bold cyan]{info['user_code']}[/bold cyan]\n")
        c.print("  [dim]Waiting for authorization... Ctrl+C to cancel[/dim]\n")

    try:
        result = await run_oauth_flow(
            provider=integration.oauth_provider,
            scopes=getattr(integration, "oauth_scopes", None),
            display_callback=_show_auth_info,
            client_id=client_id,
        )
    except OAuthError as e:
        c.print(f"  [red]OAuth failed:[/red] {e}")
        return False

    # Passthrough: result contains code + redirect_uri, not tokens
    if result.get("mode") != "passthrough":
        c.print("  [red]Unexpected response from proxy (expected passthrough mode)[/red]")
        return False

    c.print("  [dim]Exchanging authorization code...[/dim]")
    try:
        tokens = await exchange_code_direct(
            code=result["code"],
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=result["redirect_uri"],
        )
    except OAuthError as e:
        c.print(f"  [red]Token exchange failed:[/red] {e}")
        return False

    access_token = tokens.get("access_token", "")
    if not access_token:
        c.print("  [red]No access token in response[/red]")
        return False

    # Validate the authenticated account matches the expected one
    if account and access_token:
        from promaia.auth.integrations.google import GoogleIntegration

        actual_email = await GoogleIntegration.get_authenticated_email(access_token)
        if actual_email and actual_email.lower() != account.lower():
            c.print(f"  [red]Account mismatch:[/red] signed in as [bold]{actual_email}[/bold] but expected [bold]{account}[/bold]")
            c.print(f"  [dim]Please try again and sign in with {account}[/dim]")
            return False
        if actual_email:
            account = actual_email

    # Store tokens with user credentials for direct refresh
    store_kwargs = dict(tokens)
    store_kwargs["mode"] = "user_oauth"
    store_kwargs["client_id"] = client_id
    store_kwargs["client_secret"] = client_secret
    if account:
        store_kwargs["account"] = account
    elif workspace:
        store_kwargs["workspace"] = workspace
    integration.store_credential(access_token, **store_kwargs)
    c.print(f"  [green]OK[/green] {integration.display_name} connected (user-owned OAuth)")

    # Validate
    valid, msg = await integration.validate_credential(access_token)
    if valid:
        c.print(f"  [green]OK[/green] {msg}")
    else:
        c.print(f"  [yellow]Warning:[/yellow] {msg}")

    return True


# ── API key flow ──────────────────────────────────────────────────────


async def _configure_api_key(
    integration: Integration, c: Console, *, workspace: str | None = None,
) -> bool:
    """Prompt for API key, validate, and store."""
    c.print(f"\n[bold]{integration.display_name} Setup[/bold]\n")

    for line in integration.help_lines:
        c.print(f"  [dim]{line}[/dim]")

    if integration.key_url:
        c.print(
            f"\n  [dim]Key URL: [link={integration.key_url}]"
            f"{integration.key_url}[/link][/dim]\n"
        )

    # Check for existing credential
    existing = integration.get_default_credential()
    if existing:
        masked = (
            existing[:8] + "..." + existing[-4:]
            if len(existing) > 12
            else "***"
        )
        c.print(f"  [dim]Current key: {masked}[/dim]")

        replace = Prompt.ask(
            "  Replace existing key? [y/N]", default="n"
        ).strip().lower()
        if replace not in ("y", "yes"):
            c.print("  [dim]Validating existing key...[/dim]")
            valid, msg = await integration.validate_credential(existing)
            if valid:
                c.print(f"  [green]OK[/green] {msg}")
                return True
            else:
                c.print(f"  [red]FAIL[/red] {msg}")
                c.print(
                    "  [yellow]Existing key is invalid. "
                    "Please enter a new one.[/yellow]\n"
                )

    # Prompt for new key (up to 3 attempts)
    max_attempts = 3
    for attempt in range(max_attempts):
        api_key = Prompt.ask(
            f"  Enter your {integration.display_name} API key"
        ).strip()

        if not api_key:
            c.print("  [red]API key cannot be empty.[/red]")
            continue

        if api_key.lower() == "q":
            return False

        c.print("  [dim]Validating...[/dim]")
        valid, msg = await integration.validate_credential(api_key)

        if valid:
            c.print(f"  [green]OK[/green] {msg}")
            integration.store_credential(api_key, workspace=workspace)
            c.print(f"  [green]OK[/green] Credential saved")
            return True
        else:
            remaining = max_attempts - attempt - 1
            c.print(f"  [red]FAIL[/red] {msg}")
            if remaining > 0:
                c.print(
                    f"  [yellow]{remaining} attempt(s) remaining[/yellow]\n"
                )
            else:
                c.print("  [red]Max validation attempts reached.[/red]")
                save_anyway = Prompt.ask(
                    "  Save key anyway (skip validation)? [y/N]",
                    default="n",
                ).strip().lower()
                if save_anyway in ("y", "yes"):
                    integration.store_credential(api_key, workspace=workspace)
                    c.print(f"  [green]OK[/green] Credential saved (unvalidated)")
                    return True
                return False

    return False


# ── Auth mode selector ────────────────────────────────────────────────


_MODE_LABELS: dict[AuthMode, tuple[str, bool]] = {
    AuthMode.OAUTH: ("Authorize via Promaia", True),
    AuthMode.USER_OAUTH: ("Use your own Google Cloud project", False),
    AuthMode.API_KEY: ("Enter API key manually", False),
}


async def _select_auth_mode(integration: Integration, c: Console) -> AuthMode | None:
    """Arrow-key selector for auth mode.

    Returns the chosen :class:`AuthMode` or ``None`` if cancelled.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout

    options = []
    for mode in integration.auth_modes:
        label, recommended = _MODE_LABELS.get(mode, (mode.value, False))
        options.append((mode, label, recommended))
    current_focus = 0
    confirmed = False

    def get_entry_display(index: int) -> str:
        _, label, recommended = options[index]
        indicator = "\u2192" if index == current_focus else " "
        tag = "  (recommended)" if recommended else ""
        return f" {indicator}  {label}{tag}"

    def get_status_display():
        return " \u2191\u2193 Navigate   ENTER Select   ESC Cancel"

    def create_layout():
        title_window = Window(
            FormattedTextControl(
                text=f" {integration.display_name} \u2014 Choose Connection Method:"
            ),
            height=1,
        )
        entry_windows = [
            Window(
                FormattedTextControl(text=lambda i=i: get_entry_display(i)),
                height=1,
            )
            for i in range(len(options))
        ]
        status_window = Window(
            FormattedTextControl(text=get_status_display),
            height=1,
            style="fg:gray",
        )
        return Layout(HSplit([
            title_window,
            Window(height=1),
            *entry_windows,
            Window(height=1),
            status_window,
        ]))

    layout = create_layout()
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        if current_focus > 0:
            current_focus -= 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        if current_focus < len(options) - 1:
            current_focus += 1
            event.app.layout = create_layout()

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal confirmed
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()

    if confirmed:
        mode, label, _ = options[current_focus]
        c.print(f"  [magenta]{label}[/magenta]\n")
        return mode
    return None
