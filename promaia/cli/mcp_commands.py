"""
CLI commands for MCP (Model Context Protocol) server management.

Provides `maia mcp test` to verify connectivity and tool discovery
for configured MCP servers.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def add_mcp_commands(subparsers):
    """Register the `maia mcp` command group."""
    mcp_parser = subparsers.add_parser(
        "mcp", help="Manage MCP (Model Context Protocol) servers"
    )
    mcp_subparsers = mcp_parser.add_subparsers(
        dest="mcp_command", help="MCP commands"
    )

    # maia mcp test [server_name]
    test_parser = mcp_subparsers.add_parser(
        "test",
        help="Test connectivity to MCP servers",
    )
    test_parser.add_argument(
        "server_name",
        nargs="?",
        default=None,
        help="Name of a specific server to test (omit to test all enabled servers)",
    )
    test_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Connection timeout in seconds (default: 30)",
    )
    test_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show full tool schemas and server capabilities",
    )
    test_parser.set_defaults(func=handle_mcp_test)

    # maia mcp list
    list_parser = mcp_subparsers.add_parser(
        "list",
        help="List configured MCP servers",
    )
    list_parser.add_argument(
        "--all", "-a",
        action="store_true",
        dest="show_all",
        help="Include disabled servers",
    )
    list_parser.set_defaults(func=handle_mcp_list)

    return mcp_parser


# ── helpers ──────────────────────────────────────────────────────────

def _find_mcp_config_path() -> Optional[Path]:
    """Locate mcp_servers.json using the shared search logic."""
    from promaia.agents.mcp_loader import _find_mcp_servers_json
    return _find_mcp_servers_json()


def _load_servers(config_path: Path) -> dict:
    """Load server definitions from mcp_servers.json."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f).get("servers", {})


def _validate_config(config) -> list:
    """Lightweight validation of an McpServerConfig."""
    import shutil
    import os

    errors = []
    if not config.name:
        errors.append("Server name is required")
    if config.transport not in ("stdio", "streamable_http"):
        errors.append(f"Unknown transport '{config.transport}'")
    if config.transport == "streamable_http":
        if not config.url:
            errors.append("URL is required for streamable_http transport")
    else:
        if not config.command:
            errors.append("Server command is required for stdio transport")
        elif not shutil.which(config.command[0]):
            errors.append(f"Command '{config.command[0]}' not found in PATH")
        if config.working_dir and not os.path.isdir(config.working_dir):
            errors.append(f"Working directory '{config.working_dir}' does not exist")
    return errors


# ── maia mcp list ────────────────────────────────────────────────────

def handle_mcp_list(args):
    """List configured MCP servers and their status."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    config_path = _find_mcp_config_path()

    if not config_path:
        console.print("[red]No mcp_servers.json found.[/red]")
        console.print(
            "Run [bold]maia setup[/bold] or create maia-data/mcp_servers.json manually."
        )
        return

    servers = _load_servers(config_path)
    if not servers:
        console.print("[yellow]No MCP servers configured.[/yellow]")
        console.print(f"Config file: {config_path}")
        return

    show_all = getattr(args, "show_all", False)

    table = Table(title=f"MCP Servers ({config_path})")
    table.add_column("Name", style="bold")
    table.add_column("Transport")
    table.add_column("Endpoint")
    table.add_column("Enabled")

    for name, cfg in sorted(servers.items()):
        enabled = cfg.get("enabled", True)
        if not show_all and not enabled:
            continue

        transport = cfg.get("transport", "stdio")
        if transport == "streamable_http":
            endpoint = cfg.get("url", "—")
        else:
            cmd = cfg.get("command", [])
            endpoint = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

        enabled_str = "[green]yes[/green]" if enabled else "[dim]no[/dim]"
        table.add_row(name, transport, endpoint, enabled_str)

    console.print(table)


# ── maia mcp test ────────────────────────────────────────────────────

def handle_mcp_test(args):
    """Test MCP server connectivity: handshake + list tools."""
    asyncio.run(_run_mcp_test(args))


async def _run_mcp_test(args):
    from rich.console import Console
    from rich.table import Table

    console = Console()
    config_path = _find_mcp_config_path()

    if not config_path:
        console.print("[red]No mcp_servers.json found.[/red]")
        console.print(
            "Run [bold]maia setup[/bold] or create maia-data/mcp_servers.json manually."
        )
        return

    servers = _load_servers(config_path)
    if not servers:
        console.print("[yellow]No MCP servers configured.[/yellow]")
        return

    target_name: Optional[str] = getattr(args, "server_name", None)
    timeout: int = getattr(args, "timeout", 30)
    verbose: bool = getattr(args, "verbose", False)

    if target_name:
        if target_name not in servers:
            console.print(
                f"[red]Server '{target_name}' not found in config.[/red]"
            )
            console.print(
                "Available servers: "
                + ", ".join(sorted(servers.keys()))
            )
            return
        to_test = {target_name: servers[target_name]}
    else:
        # Test all enabled servers
        to_test = {
            n: c for n, c in servers.items() if c.get("enabled", True)
        }
        if not to_test:
            console.print("[yellow]No enabled MCP servers to test.[/yellow]")
            console.print("Use [bold]maia mcp list --all[/bold] to see disabled servers.")
            return

    console.print(f"\nTesting {len(to_test)} MCP server(s)...\n")

    for name, cfg in to_test.items():
        await _test_single_server(console, name, cfg, timeout, verbose)
        console.print()  # blank line between servers


async def _test_single_server(console, name: str, cfg: dict, timeout: int, verbose: bool):
    """Test a single MCP server: connect, handshake, list tools, disconnect."""
    from rich.table import Table
    from promaia.mcp.protocol import McpProtocolClient
    from promaia.config.mcp_servers import McpServerConfig

    transport = cfg.get("transport", "stdio")
    description = cfg.get("description", "")

    console.print(f"[bold]● {name}[/bold]", end="")
    if description:
        console.print(f"  [dim]{description}[/dim]")
    else:
        console.print()

    if transport == "streamable_http":
        url = cfg.get("url", "")
        console.print(f"  Transport: streamable_http → {url}")
    else:
        cmd = cfg.get("command", [])
        extra_args = cfg.get("args", [])
        display_cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if extra_args:
            display_cmd += " " + " ".join(extra_args)
        console.print(f"  Transport: stdio → {display_cmd}")

    # Build a McpServerConfig so we can use get_resolved_env()
    server_config = McpServerConfig(
        name=name,
        description=description,
        command=cfg.get("command", []),
        args=cfg.get("args", []),
        env=cfg.get("env", {}),
        working_dir=cfg.get("working_dir"),
        timeout=timeout,
        enabled=cfg.get("enabled", True),
        transport=transport,
        url=cfg.get("url"),
    )

    # Validate config
    errors = _validate_config(server_config)
    if errors:
        for err in errors:
            console.print(f"  [red]Config error:[/red] {err}")
        console.print(f"  [red]FAIL[/red] — config validation failed")
        return

    # Attempt connection
    protocol = McpProtocolClient()
    connect_kwargs = {
        "transport": transport,
        "timeout": timeout,
    }
    if transport == "streamable_http":
        connect_kwargs["url"] = server_config.url
        if server_config.env:
            connect_kwargs["headers"] = server_config.get_resolved_env()
    else:
        connect_kwargs["command"] = server_config.command
        connect_kwargs["args"] = server_config.args
        connect_kwargs["working_dir"] = server_config.working_dir
        connect_kwargs["env"] = server_config.get_resolved_env()

    t0 = time.monotonic()
    try:
        success = await protocol.connect(**connect_kwargs)
    except Exception as exc:
        elapsed = time.monotonic() - t0
        console.print(f"  [red]FAIL[/red] — connection error ({elapsed:.1f}s): {exc}")
        return

    elapsed = time.monotonic() - t0

    if not success:
        console.print(f"  [red]FAIL[/red] — could not connect ({elapsed:.1f}s)")
        await protocol.disconnect()
        return

    # Connection succeeded — show server info
    info = protocol.get_server_info() or {}
    server_name_reported = info.get("name", "unknown")
    server_version = info.get("version", "—")
    console.print(
        f"  [green]Connected[/green] in {elapsed:.1f}s — "
        f"server: {server_name_reported} v{server_version}"
    )

    # Show capabilities summary
    caps = protocol.get_capabilities() or {}
    if verbose and caps:
        cap_keys = [k for k, v in caps.items() if v]
        if cap_keys:
            console.print(f"  Capabilities: {', '.join(cap_keys)}")

    # List tools
    try:
        tools = await protocol.list_tools()
    except Exception as exc:
        console.print(f"  [yellow]Warning:[/yellow] could not list tools: {exc}")
        tools = None

    if tools:
        console.print(f"  Tools: [bold]{len(tools)}[/bold] available")

        table = Table(show_header=True, padding=(0, 1))
        table.add_column("Tool", style="cyan")
        table.add_column("Description")
        if verbose:
            table.add_column("Parameters")

        for tool in tools:
            tool_name = tool.get("name", "?")
            tool_desc = tool.get("description", "")
            # Truncate long descriptions unless verbose
            if not verbose and len(tool_desc) > 80:
                tool_desc = tool_desc[:77] + "..."

            if verbose:
                schema = tool.get("inputSchema", {})
                props = schema.get("properties", {})
                required = set(schema.get("required", []))
                if props:
                    param_strs = []
                    for pname in props:
                        marker = "*" if pname in required else ""
                        param_strs.append(f"{pname}{marker}")
                    params = ", ".join(param_strs)
                else:
                    params = "—"
                table.add_row(tool_name, tool_desc, params)
            else:
                table.add_row(tool_name, tool_desc)

        console.print(table)
    elif tools is not None:
        console.print("  Tools: [dim]none advertised[/dim]")

    # Clean up
    await protocol.disconnect()
    console.print(f"  [green]OK[/green] — server is reachable and responding")
