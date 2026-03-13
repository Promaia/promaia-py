"""CLI commands for the unified agent activity feed."""

from rich.console import Console

from promaia.agents import load_agents


async def handle_feed_start(args):
    """List available agents and point user to per-agent feed."""
    console = Console()

    console.print("[cyan]🐙 Maia Feed[/cyan]\n")
    console.print("Per-agent feeds:")
    console.print("  maia agent feed <agent-name>\n")

    agents = load_agents()
    if agents:
        console.print("Available agents:")
        for agent in agents:
            status = "✅" if agent.enabled else "⏸️"
            console.print(f"  {status} {agent.name} ({agent.workspace})")
        console.print()

    console.print("Use [bold]maia agent feed <name>[/bold] to watch a specific agent.")


def add_feed_commands(subparsers):
    """Add feed management commands to CLI."""
    feed_parser = subparsers.add_parser(
        'feed',
        help='Live unified view of agent activity (use "maia agent feed <name>" for per-agent feed)'
    )

    feed_parser.set_defaults(func=handle_feed_start)
