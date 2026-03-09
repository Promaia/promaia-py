"""
CLI commands for managing the calendar monitor daemon.

Provides start, stop, status, enable, disable, and logs commands
for 24/7 operation via launchd (macOS) or systemd (Linux).
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()


def get_launchd_plist_path() -> Path:
    """Get path to launchd plist file."""
    return Path.home() / "Library" / "LaunchAgents" / "com.promaia.agent.plist"


def get_source_plist_path() -> Path:
    """Get path to source plist template."""
    # Try to find the plist in the package
    import promaia
    package_dir = Path(promaia.__file__).parent.parent
    return package_dir / "scripts" / "com.promaia.agent.plist"


def is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Check if running on Linux."""
    return sys.platform.startswith("linux")


def check_platform():
    """Ensure platform is supported."""
    if not is_macos() and not is_linux():
        console.print("[red]Error: Daemon management only supported on macOS and Linux[/red]")
        console.print(f"[yellow]Current platform: {sys.platform}[/yellow]")
        sys.exit(1)


def get_python_path() -> str:
    """Get path to current Python interpreter."""
    return sys.executable


def install_launchd_plist():
    """Install launchd plist to ~/Library/LaunchAgents/."""
    source_plist = get_source_plist_path()
    dest_plist = get_launchd_plist_path()

    if not source_plist.exists():
        console.print(f"[red]Error: Source plist not found: {source_plist}[/red]")
        console.print("[yellow]Please ensure promaia is installed correctly[/yellow]")
        return False

    # Create LaunchAgents directory if needed
    dest_plist.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Read template
        with open(source_plist, 'r') as f:
            plist_content = f.read()

        # Update paths for current user
        python_path = get_python_path()
        home_dir = str(Path.home())

        plist_content = plist_content.replace('/usr/local/bin/python3', python_path)
        plist_content = plist_content.replace('__HOME__', home_dir)

        # Write to LaunchAgents
        with open(dest_plist, 'w') as f:
            f.write(plist_content)

        # Set permissions
        os.chmod(dest_plist, 0o644)

        console.print(f"[green]✓ Installed plist to {dest_plist}[/green]")
        return True

    except Exception as e:
        console.print(f"[red]Error installing plist: {e}[/red]")
        return False


def handle_daemon_enable(args):
    """Enable daemon to auto-start on boot."""
    check_platform()

    if is_macos():
        console.print("[cyan]Enabling calendar monitor daemon (auto-start on boot)...[/cyan]")

        # Install plist
        if not install_launchd_plist():
            return

        plist_path = get_launchd_plist_path()

        try:
            # Load service
            result = subprocess.run(
                ['launchctl', 'load', str(plist_path)],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                console.print("[green]✓ Daemon enabled successfully[/green]")
                console.print(f"[dim]Service will start automatically on boot[/dim]")
                console.print(f"[dim]To start now: maia daemon start[/dim]")
            else:
                if "Already loaded" in result.stderr or "already loaded" in result.stderr:
                    console.print("[yellow]Service already enabled[/yellow]")
                else:
                    console.print(f"[red]Error enabling service:[/red]\n{result.stderr}")

        except FileNotFoundError:
            console.print("[red]Error: launchctl not found (not on macOS?)[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    elif is_linux():
        console.print("[yellow]systemd support not yet implemented[/yellow]")
        console.print("[dim]Coming soon! For now, run manually: maia agent calendar-monitor[/dim]")


def handle_daemon_disable(args):
    """Disable daemon auto-start."""
    check_platform()

    if is_macos():
        console.print("[cyan]Disabling calendar monitor daemon...[/cyan]")
        plist_path = get_launchd_plist_path()

        if not plist_path.exists():
            console.print("[yellow]Daemon not enabled (plist not found)[/yellow]")
            return

        try:
            # Unload service
            result = subprocess.run(
                ['launchctl', 'unload', str(plist_path)],
                capture_output=True,
                text=True
            )

            if result.returncode == 0 or "Could not find" in result.stderr:
                # Remove plist
                plist_path.unlink()
                console.print("[green]✓ Daemon disabled successfully[/green]")
                console.print("[dim]Service will not start automatically on boot[/dim]")
            else:
                console.print(f"[red]Error disabling service:[/red]\n{result.stderr}")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    elif is_linux():
        console.print("[yellow]systemd support not yet implemented[/yellow]")


def handle_daemon_start(args):
    """Start daemon as a background process."""
    check_platform()

    from promaia.agents.daemon import get_daemon_status

    status = get_daemon_status()

    if status["running"]:
        console.print(f"[yellow]Daemon already running (PID {status['pid']})[/yellow]")
        console.print(f"[dim]To restart: maia daemon restart[/dim]")
        return

    foreground = getattr(args, 'foreground', False)

    if foreground:
        # Run in foreground (useful for debugging)
        console.print("[cyan]Starting calendar monitor daemon (foreground)...[/cyan]")
        from promaia.agents.daemon import CalendarMonitorDaemon
        daemon = CalendarMonitorDaemon(
            check_interval_minutes=getattr(args, 'interval', 1),
            trigger_window_minutes=getattr(args, 'window', 5),
        )
        try:
            daemon.start()
        except KeyboardInterrupt:
            console.print("\n[yellow]Daemon stopped by user[/yellow]")
        return

    # Spawn daemon as a detached background process
    interval = getattr(args, 'interval', 1)
    window = getattr(args, 'window', 5)

    # Use the same Python interpreter to run the daemon module directly
    cmd = [
        sys.executable, "-m", "promaia.agents.daemon",
        "--interval", str(interval),
        "--window", str(window),
    ]

    # Redirect stdout/stderr to the daemon log file
    from promaia.utils.env_writer import get_logs_dir
    log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "daemon.log"

    with open(log_file, "a") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # Detach from terminal
            cwd=str(Path(__file__).parent.parent.parent),  # Project root
        )

    # Wait briefly and verify it started
    import time
    time.sleep(1.5)

    status = get_daemon_status()
    if status["running"]:
        console.print(f"[green]✓ Daemon started (PID {status['pid']})[/green]")
        console.print(f"[dim]Logs: {log_file}[/dim]")
    else:
        console.print(f"[red]Daemon failed to start (process exited)[/red]")
        console.print(f"[dim]Check logs: {log_file}[/dim]")


def handle_daemon_stop(args):
    """Stop running daemon."""
    check_platform()

    from promaia.agents.daemon import stop_daemon, get_daemon_status

    status = get_daemon_status()

    if not status["running"]:
        console.print("[yellow]Daemon not running[/yellow]")

        # Check for stale PID file
        if status.get("stale_pid"):
            console.print(f"[dim]Removing stale PID file (PID {status['stale_pid']} not running)[/dim]")
            status["pid_file"].unlink()
        return

    console.print(f"[cyan]Stopping daemon (PID {status['pid']})...[/cyan]")

    if stop_daemon():
        console.print("[green]✓ Daemon stopped successfully[/green]")
    else:
        console.print("[red]Failed to stop daemon[/red]")
        console.print(f"[dim]Try manually: kill {status['pid']}[/dim]")


def handle_daemon_restart(args):
    """Restart daemon."""
    console.print("[cyan]Restarting daemon...[/cyan]")
    handle_daemon_stop(args)

    # Wait a moment for clean shutdown
    import time
    time.sleep(1)

    handle_daemon_start(args)


def handle_daemon_status(args):
    """Show daemon status."""
    check_platform()

    from promaia.agents.daemon import get_daemon_status

    status = get_daemon_status()

    # Create status table
    table = Table(title="Calendar Monitor Daemon Status", show_header=False)
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    # Running status
    if status["running"]:
        table.add_row("Status", "[green]Running ✓[/green]")
        table.add_row("PID", str(status["pid"]))
    else:
        table.add_row("Status", "[red]Not running[/red]")
        if status.get("stale_pid"):
            table.add_row("Stale PID", f"{status['stale_pid']} [dim](process not found)[/dim]")

    # File paths
    table.add_row("PID File", str(status["pid_file"]))
    table.add_row("Log File", str(status["log_file"]))

    # launchd status (macOS)
    if is_macos():
        plist_path = get_launchd_plist_path()
        if plist_path.exists():
            table.add_row("Auto-start", "[green]Enabled[/green]")
            table.add_row("Plist", str(plist_path))
        else:
            table.add_row("Auto-start", "[yellow]Disabled[/yellow]")

    console.print(table)

    # Show recent logs if running
    if status["running"] and status["log_file"].exists():
        console.print("\n[cyan]Recent logs:[/cyan]")
        try:
            with open(status["log_file"], 'r') as f:
                lines = f.readlines()
                # Show last 10 lines
                for line in lines[-10:]:
                    console.print(f"  [dim]{line.rstrip()}[/dim]")
        except Exception as e:
            console.print(f"[red]Error reading logs: {e}[/red]")


def handle_daemon_logs(args):
    """Show daemon logs."""
    check_platform()

    from promaia.agents.daemon import get_daemon_status

    status = get_daemon_status()
    log_file = status["log_file"]

    if not log_file.exists():
        console.print(f"[yellow]Log file not found: {log_file}[/yellow]")
        console.print("[dim]The daemon may not have been started yet[/dim]")
        return

    # Follow mode
    if getattr(args, 'follow', False):
        console.print(f"[cyan]Following logs: {log_file}[/cyan]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        try:
            # Use tail -f
            subprocess.run(['tail', '-f', str(log_file)])
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped following logs[/dim]")
        except FileNotFoundError:
            console.print("[red]Error: 'tail' command not found[/red]")
            # Fallback: read file repeatedly
            import time
            last_size = 0
            try:
                while True:
                    size = log_file.stat().st_size
                    if size > last_size:
                        with open(log_file, 'r') as f:
                            f.seek(last_size)
                            console.print(f.read(), end='')
                        last_size = size
                    time.sleep(0.5)
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped following logs[/dim]")
    else:
        # Show last N lines
        lines = getattr(args, 'lines', 50)

        try:
            with open(log_file, 'r') as f:
                all_lines = f.readlines()
                recent_lines = all_lines[-lines:]

            console.print(f"[cyan]Last {len(recent_lines)} lines from: {log_file}[/cyan]\n")
            for line in recent_lines:
                console.print(line.rstrip())

            if len(all_lines) > lines:
                console.print(f"\n[dim]Showing {len(recent_lines)} of {len(all_lines)} total lines[/dim]")
                console.print(f"[dim]Use --follow to tail logs live[/dim]")

        except Exception as e:
            console.print(f"[red]Error reading logs: {e}[/red]")


def add_daemon_commands(subparsers):
    """Add daemon management commands to CLI."""
    daemon_parser = subparsers.add_parser(
        'daemon',
        help='Manage calendar monitor daemon (24/7 background operation)'
    )

    daemon_subparsers = daemon_parser.add_subparsers(
        dest='daemon_command',
        help='Daemon commands'
    )

    # Enable command
    enable_parser = daemon_subparsers.add_parser(
        'enable',
        help='Enable daemon to auto-start on boot (launchd/systemd)'
    )
    enable_parser.set_defaults(func=handle_daemon_enable)

    # Disable command
    disable_parser = daemon_subparsers.add_parser(
        'disable',
        help='Disable daemon auto-start'
    )
    disable_parser.set_defaults(func=handle_daemon_disable)

    # Start command
    start_parser = daemon_subparsers.add_parser(
        'start',
        help='Start daemon in background'
    )
    start_parser.add_argument(
        '--interval', '-i',
        type=int,
        default=1,
        help='Check interval in minutes (default: 1)'
    )
    start_parser.add_argument(
        '--window', '-w',
        type=int,
        default=5,
        help='Trigger window in minutes after start time (default: 5)'
    )
    start_parser.add_argument(
        '--foreground', '-f',
        action='store_true',
        help='Run in foreground instead of background (for debugging)'
    )
    start_parser.set_defaults(func=handle_daemon_start)

    # Stop command
    stop_parser = daemon_subparsers.add_parser(
        'stop',
        help='Stop running daemon'
    )
    stop_parser.set_defaults(func=handle_daemon_stop)

    # Restart command
    restart_parser = daemon_subparsers.add_parser(
        'restart',
        help='Restart daemon'
    )
    restart_parser.set_defaults(func=handle_daemon_restart)

    # Status command
    status_parser = daemon_subparsers.add_parser(
        'status',
        help='Show daemon status'
    )
    status_parser.set_defaults(func=handle_daemon_status)

    # Logs command
    logs_parser = daemon_subparsers.add_parser(
        'logs',
        help='Show daemon logs'
    )
    logs_parser.add_argument(
        '--follow', '-f',
        action='store_true',
        help='Follow logs in real-time (like tail -f)'
    )
    logs_parser.add_argument(
        '--lines', '-n',
        type=int,
        default=50,
        help='Number of recent lines to show (default: 50)'
    )
    logs_parser.set_defaults(func=handle_daemon_logs)

    # Set default handler for bare "maia daemon"
    daemon_parser.set_defaults(func=handle_daemon_status)
