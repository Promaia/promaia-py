"""``maia auth`` — manage integration credentials.

Subcommands::

    maia auth list                                   # Show all integrations + status
    maia auth configure <integration>                # Interactive setup
    maia auth configure google --account you@gmail   # Authenticate a specific Google account
    maia auth revoke <integration>                   # Remove stored credentials
    maia auth revoke google --account you@gmail      # Revoke a specific Google account
"""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.table import Table

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────


def _get_google_accounts_from_config() -> list[str]:
    """Return unique Gmail email addresses from the database config."""
    try:
        from promaia.config.databases import get_database_manager

        db_manager = get_database_manager()
        emails: list[str] = []
        for name in db_manager.list_databases():
            db = db_manager.get_database(name)
            if db and db.source_type == "gmail" and db.database_id:
                email = db.database_id.lower()
                if email not in emails:
                    emails.append(email)
        return emails
    except Exception:
        return []


async def _select_google_account(c: Console) -> str | None:
    """Prompt user to pick a Google account from configured databases.

    Returns the chosen email, or ``None`` if cancelled.
    """
    emails = _get_google_accounts_from_config()

    if not emails:
        c.print("[yellow]No Gmail accounts found in your databases.[/yellow]")
        c.print("[dim]Add one first with: maia database add[/dim]")
        c.print("[dim]Or specify directly: maia auth configure google --account you@gmail.com[/dim]\n")
        # Allow manual entry as fallback
        from rich.prompt import Prompt

        manual = Prompt.ask("  Enter Google account email (or q to cancel)").strip()
        if not manual or manual.lower() == "q":
            return None
        return manual

    if len(emails) == 1:
        c.print(f"  [dim]Account:[/dim] [bold]{emails[0]}[/bold]\n")
        return emails[0]

    # Multiple accounts — show selector
    c.print("[bold]Which Google account to authenticate?[/bold]\n")
    for i, email in enumerate(emails, 1):
        c.print(f"  {i}. {email}")
    c.print()

    from rich.prompt import Prompt

    choice = Prompt.ask(f"  Select (1-{len(emails)})").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(emails):
        return emails[int(choice) - 1]

    # Maybe they typed the email directly
    if "@" in choice and choice.lower() in emails:
        return choice.lower()

    c.print("[red]Invalid selection[/red]")
    return None


# ── Handlers ──────────────────────────────────────────────────────────


async def handle_auth_list(args):
    """Show credential status for every registered integration."""
    from promaia.auth.registry import list_integrations

    table = Table(show_header=True)
    table.add_column("Integration", style="magenta")
    table.add_column("Mode")
    table.add_column("Status")

    for integration in list_integrations():
        mode = integration.auth_modes[0].value.replace("_", " ").title()

        # Google: show per-account status
        if integration.name == "google":
            accounts = integration.list_authenticated_accounts()
            if accounts:
                for acct in accounts:
                    status = f"[green]Authenticated[/green]"
                    table.add_row(f"  {integration.display_name}", mode, f"{status} ({acct})")
            else:
                # Check legacy global
                cred = integration.get_default_credential()
                if cred:
                    status = "[yellow]Set (legacy global)[/yellow]"
                else:
                    status = "[dim]Not set[/dim]"
                table.add_row(integration.display_name, mode, status)
            continue

        cred = integration.get_default_credential()
        if cred:
            masked = (
                cred[:8] + "..." + cred[-4:]
                if len(cred) > 12
                else "***"
            )
            status = f"[green]Set[/green] ({masked})"
        else:
            status = "[dim]Not set[/dim]"

        table.add_row(integration.display_name, mode, status)

    console.print(table)


async def handle_auth_configure(args):
    """Interactive setup for a single integration."""
    from promaia.auth.registry import get_integration
    from promaia.auth.flow import configure_credential

    try:
        integration = get_integration(args.name)
    except KeyError:
        console.print(f"[red]Unknown integration: {args.name}[/red]")
        _print_available()
        return

    workspace = getattr(args, "workspace", None)
    account = getattr(args, "account", None)

    # Google-specific: require an account (email) to authenticate
    if args.name == "google" and not account:
        account = await _select_google_account(console)
        if account is None:
            return

    await configure_credential(integration, console, workspace=workspace, account=account)


async def handle_auth_revoke(args):
    """Remove stored credentials for a single integration."""
    from promaia.auth.registry import get_integration

    try:
        integration = get_integration(args.name)
    except KeyError:
        console.print(f"[red]Unknown integration: {args.name}[/red]")
        _print_available()
        return

    account = getattr(args, "account", None)

    # Google-specific: revoke per-account
    if args.name == "google":
        if not account:
            accounts = integration.list_authenticated_accounts()
            if not accounts:
                console.print("[dim]No Google accounts authenticated.[/dim]")
                return
            if len(accounts) == 1:
                account = accounts[0]
            else:
                console.print("[bold]Which Google account to revoke?[/bold]\n")
                for i, acct in enumerate(accounts, 1):
                    console.print(f"  {i}. {acct}")
                console.print()
                from rich.prompt import Prompt

                choice = Prompt.ask(f"  Select (1-{len(accounts)})").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(accounts):
                    account = accounts[int(choice) - 1]
                else:
                    console.print("[red]Invalid selection[/red]")
                    return

        integration.clear_account_credential(account)
        console.print(f"[green]OK[/green] Google credential removed for {account}")
        return

    integration.clear_credential()
    console.print(f"[green]OK[/green] {integration.display_name} credential removed")


def _print_available():
    """Print the names of all registered integrations."""
    from promaia.auth.registry import list_integrations

    names = [i.name for i in list_integrations()]
    console.print(f"[dim]Available: {', '.join(names)}[/dim]")


# ── Dispatch ──────────────────────────────────────────────────────────


def handle_auth(args):
    """Sync entry point for ``maia auth``."""
    auth_cmd = getattr(args, "auth_command", None)

    handlers = {
        "list": handle_auth_list,
        "configure": handle_auth_configure,
        "revoke": handle_auth_revoke,
    }

    handler = handlers.get(auth_cmd)
    if handler:
        asyncio.run(handler(args))
    else:
        console.print("[dim]Usage: maia auth [list|configure|revoke][/dim]")
        console.print("[dim]Run 'maia auth --help' for details.[/dim]")


# ── Argparse registration ────────────────────────────────────────────


def add_auth_commands(subparsers):
    """Register ``maia auth`` command with argparse."""
    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage integration credentials (list, configure, revoke)",
    )
    auth_subs = auth_parser.add_subparsers(dest="auth_command")

    # maia auth list
    auth_subs.add_parser("list", help="Show credential status for all integrations")

    # maia auth configure <name>
    configure_parser = auth_subs.add_parser(
        "configure",
        help="Configure an integration (e.g. anthropic, notion, discord)",
    )
    configure_parser.add_argument(
        "name",
        help="Integration name (anthropic, openai, google_ai, google, notion, discord)",
    )
    configure_parser.add_argument(
        "--workspace", "-w",
        default=None,
        help="Store credential for a specific workspace (default: global)",
    )
    configure_parser.add_argument(
        "--account", "-a",
        default=None,
        help="Google account email to authenticate (for Google integration)",
    )

    # maia auth revoke <name>
    revoke_parser = auth_subs.add_parser(
        "revoke",
        help="Remove stored credentials for an integration",
    )
    revoke_parser.add_argument(
        "name",
        help="Integration name (anthropic, openai, google_ai, google, notion, discord)",
    )
    revoke_parser.add_argument(
        "--account", "-a",
        default=None,
        help="Google account email to revoke (for Google integration)",
    )

    auth_parser.set_defaults(func=handle_auth)
