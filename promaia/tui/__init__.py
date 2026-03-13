"""
Unified Terminal User Interface for Promaia.

Makes everything feel like a conversation - watch the feed, chat with agents,
run commands inline - all from a single home base.

Usage:
    from promaia.tui import run_tui
    await run_tui()
"""

from promaia.tui.app import PromaiaApp, run_tui

__all__ = ['PromaiaApp', 'run_tui']
