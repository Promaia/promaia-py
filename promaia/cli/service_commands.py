"""
CLI commands for managing Promaia Docker services.

Provides list, enable, disable, and restart commands to manage services
via maia-data/services.json. The supervisor process inside each
container watches this file and starts/stops the actual service.
"""

import json
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

# Canonical service names (order matches docker-compose.yml)
SERVICES = ["web", "scheduler", "calendar", "mail", "discord", "slack"]

# Defaults when services.json doesn't exist or a service isn't listed
DEFAULTS = {
    "web":       {"enabled": True},
    "scheduler": {"enabled": True},
    "calendar":  {"enabled": True},
    "mail":      {"enabled": True},
    "discord":   {"enabled": False},
    "slack":     {"enabled": False},
}


def _get_config_path() -> Path:
    """Resolve the path to services.json."""
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / "services.json"


def _read_config() -> dict:
    """Read services.json, returning defaults if missing or invalid."""
    config_path = _get_config_path()
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def _write_config(cfg: dict) -> None:
    """Write services.json with pretty formatting."""
    config_path = _get_config_path()
    config_path.write_text(json.dumps(cfg, indent=2) + "\n")


def _is_enabled(cfg: dict, service: str) -> bool:
    """Check if a service is enabled in the config."""
    return cfg.get(service, DEFAULTS.get(service, {})).get("enabled", True)


# ── Handlers ────────────────────────────────────────────────────────────


def handle_services_list(args):
    """Show all services and their enabled/disabled status."""
    cfg = _read_config()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Service", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)

    for svc in SERVICES:
        enabled = _is_enabled(cfg, svc)
        if enabled:
            status = "[green]enabled[/green]"
        else:
            status = "[dim]disabled[/dim]"
        table.add_row(svc, status)

    console.print(table)


def handle_services_enable(args):
    """Enable a service."""
    service = args.service
    cfg = _read_config()

    # Ensure the service entry exists
    if service not in cfg:
        cfg[service] = dict(DEFAULTS.get(service, {"enabled": True}))

    if cfg[service].get("enabled", True):
        console.print(f"[yellow]{service}[/yellow] is already enabled")
        return

    cfg[service]["enabled"] = True
    _write_config(cfg)
    console.print(f"[green]Enabled[/green] {service}")


def handle_services_disable(args):
    """Disable a service."""
    service = args.service
    cfg = _read_config()

    # Ensure the service entry exists
    if service not in cfg:
        cfg[service] = dict(DEFAULTS.get(service, {"enabled": True}))

    if not cfg[service].get("enabled", True):
        console.print(f"[yellow]{service}[/yellow] is already disabled")
        return

    cfg[service]["enabled"] = False
    _write_config(cfg)
    console.print(f"[dim]Disabled[/dim] {service}")


def handle_services_restart(args):
    """Request a restart for one or all services."""
    cfg = _read_config()
    target = args.service

    if target == "all":
        targets = [svc for svc in SERVICES if _is_enabled(cfg, svc)]
        if not targets:
            console.print("[yellow]No enabled services to restart[/yellow]")
            return
    else:
        targets = [target]

    ts = time.time()
    for svc in targets:
        if svc not in cfg:
            cfg[svc] = dict(DEFAULTS.get(svc, {"enabled": True}))
        cfg[svc]["restart_requested"] = ts

    _write_config(cfg)

    names = ", ".join(targets)
    console.print(f"[cyan]Restart requested[/cyan] for {names} (takes up to 5s)")


# ── Registration ────────────────────────────────────────────────────────


def add_service_commands(subparsers):
    """Add service management commands to CLI."""
    svc_parser = subparsers.add_parser(
        "services",
        help="Manage background services (web, scheduler, calendar, mail, discord)",
    )
    svc_subparsers = svc_parser.add_subparsers(
        dest="services_command",
        help="Service commands",
    )

    # maia services list
    list_parser = svc_subparsers.add_parser("list", help="Show all services and status")
    list_parser.set_defaults(func=handle_services_list)

    # maia services enable <service>
    enable_parser = svc_subparsers.add_parser("enable", help="Enable a service")
    enable_parser.add_argument("service", choices=SERVICES, help="Service to enable")
    enable_parser.set_defaults(func=handle_services_enable)

    # maia services disable <service>
    disable_parser = svc_subparsers.add_parser("disable", help="Disable a service")
    disable_parser.add_argument("service", choices=SERVICES, help="Service to disable")
    disable_parser.set_defaults(func=handle_services_disable)

    # maia services restart <service|all>
    restart_parser = svc_subparsers.add_parser(
        "restart", help="Restart a service (or all enabled services)"
    )
    restart_parser.add_argument(
        "service", choices=SERVICES + ["all"], help="Service to restart, or 'all'"
    )
    restart_parser.set_defaults(func=handle_services_restart)

    # Bare `maia services` shows list
    svc_parser.set_defaults(func=handle_services_list)
