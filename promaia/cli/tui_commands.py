"""TUI CLI commands - launch the unified terminal interface."""

import asyncio


async def handle_tui_start(args):
    """
    Launch the unified TUI.

    This is the main entry point for the TUI, called when user runs:
    - `maia` (no args - TUI is default)
    - `maia tui` (explicit)
    """
    # Import inside function to avoid circular imports and early initialization
    from promaia.tui import run_tui
    await run_tui()


def add_tui_commands(subparsers):
    """
    Add TUI commands to CLI parser.

    Args:
        subparsers: The subparsers object from argparse
    """
    tui_parser = subparsers.add_parser(
        'tui',
        help='Launch unified terminal interface (default command)'
    )
    tui_parser.add_argument(
        '--mode',
        choices=['feed', 'chat'],
        default='feed',
        help='Initial mode to start in (default: feed)'
    )
    tui_parser.set_defaults(func=handle_tui_start)
