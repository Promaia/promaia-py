"""Main TUI application - unified interface for feed, chat, and commands."""

import asyncio
from enum import Enum
from typing import Optional, List
import logging

from prompt_toolkit import Application
from prompt_toolkit.layout import Layout, HSplit, Window, FormattedTextControl
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.text import Text

from promaia.tui.modes.base import BaseMode
from promaia.tui.modes.feed_mode import FeedMode
from promaia.tui.modes.chat_mode import ChatMode
from promaia.tui.command_executor import CommandExecutor
from promaia.tui.breakout import breakout_component

logger = logging.getLogger(__name__)


class ViewMode(Enum):
    """TUI view modes."""
    FEED = "feed"
    CHAT = "chat"


class PromaiaApp:
    """
    Unified TUI application for Promaia.

    Makes everything feel like a conversation:
    - FEED mode (default): Watch live agent activity
    - CHAT mode: Talk with agents
    - Inline commands: Run any maia command with /<command>
    """

    def __init__(self):
        """Initialize the TUI application."""
        self.console = Console()

        # Create modes (only 2!)
        self.feed_mode = FeedMode(self)
        self.chat_mode = ChatMode(self)

        self.modes = {
            ViewMode.FEED: self.feed_mode,
            ViewMode.CHAT: self.chat_mode,
        }
        self.current_mode = ViewMode.FEED

        # Command executor for inline commands
        self.command_executor = CommandExecutor()

        # Create display area
        self.display_control = FormattedTextControl(text=self._get_welcome_text())

        # Create input area
        self.input_field = TextArea(
            height=1,
            prompt=self.modes[self.current_mode].get_prompt(),
            multiline=False,
            wrap_lines=False,
        )

        # Set up key bindings
        kb = self._create_key_bindings()

        # Create layout
        layout = Layout(
            HSplit([
                Window(content=self.display_control, height=None),
                Window(height=1, char='─'),
                self.input_field,
            ])
        )

        # Create application
        self.app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
        )

    def _get_welcome_text(self) -> str:
        """Get welcome/help text."""
        return """╔════════════════════════════════════════════════════════════════════════╗
║                     🐙 Maia - Unified Interface                        ║
║                     "Make Everything a Chat"                           ║
╚════════════════════════════════════════════════════════════════════════╝

Welcome! This is your home base - watch the feed, chat with agents,
and run commands. Everything feels like a conversation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MODES:
  🐙 FEED MODE (default)  - Watch live agent activity
  💬 CHAT MODE            - Talk with your agents

SWITCH MODES:
  Type:  /feed  or  /chat
  Press: Ctrl+F (feed) or Ctrl+T (chat)

RUN COMMANDS (from any mode):
  /<command>    - Run any maia command inline
  Examples:     /agent list
                /database sync
                /daemon status

RESERVED COMMANDS (work in any mode):
  /feed         - Switch to feed mode
  /chat         - Switch to chat mode
  /help         - Show this help
  /clear        - Clear the display
  /break <mode> - Open mode in new terminal (e.g., /break feed)
  /quit         - Exit (or press Ctrl+C)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Starting in FEED mode...
Press Enter to begin!
"""

    def _create_key_bindings(self) -> KeyBindings:
        """Create key bindings for mode switching and control."""
        kb = KeyBindings()

        # Ctrl+C to exit
        @kb.add('c-c')
        def _(event):
            event.app.exit()

        # Ctrl+F for feed mode
        @kb.add('c-f')
        def _(event):
            asyncio.create_task(self.switch_mode(ViewMode.FEED))

        # Ctrl+T for chat mode
        @kb.add('c-t')
        def _(event):
            asyncio.create_task(self.switch_mode(ViewMode.CHAT))

        return kb

    async def switch_mode(self, new_mode: ViewMode):
        """
        Switch to a different view mode.

        Args:
            new_mode: The mode to switch to
        """
        if new_mode == self.current_mode:
            return

        # Deactivate old mode
        await self.modes[self.current_mode].deactivate()

        # Switch mode
        old_mode = self.current_mode
        self.current_mode = new_mode

        # Activate new mode
        await self.modes[new_mode].activate()

        # Update prompt
        self.input_field.prompt = self.modes[new_mode].get_prompt()

        # Refresh display
        self.refresh_display()

        logger.info(f"Switched from {old_mode.value} to {new_mode.value} mode")

    def refresh_display(self):
        """Refresh the display with current mode's content."""
        content = self.modes[self.current_mode].get_display_content()

        # Render Rich content to ANSI
        rendered = self._render_rich_to_ansi(content)

        # Update display
        self.display_control.text = ANSI(rendered)

    def _render_rich_to_ansi(self, content: List[Text]) -> str:
        """
        Render list of Rich Text objects to ANSI string.

        Args:
            content: List of Rich Text objects

        Returns:
            ANSI string
        """
        from io import StringIO

        output = StringIO()
        console = Console(file=output, force_terminal=True)

        for text_obj in content:
            console.print(text_obj, end='')

        return output.getvalue()

    async def handle_input(self, text: str):
        """
        Handle user input based on current mode.

        Args:
            text: User input text
        """
        if not text.strip():
            return

        # Check for slash commands
        if text.startswith('/'):
            await self._handle_slash_command(text)
        else:
            # Pass to current mode
            error = await self.modes[self.current_mode].handle_input(text)
            if error:
                # Show error in display
                self._add_error_message(error)

        # Refresh display after handling input
        self.refresh_display()

    async def _handle_slash_command(self, cmd: str):
        """
        Handle slash commands with three-tier precedence:
        1. Reserved TUI commands (always handled first)
        2. Mode-specific commands (mode.handle_command)
        3. Fallback to CLI execution (maia <command>)

        Args:
            cmd: The slash command (including /)
        """
        cmd_name = cmd.split()[0].lower()

        # ═══ TIER 1: Reserved TUI Commands (ALWAYS handled) ═══
        if cmd_name == '/feed':
            await self.switch_mode(ViewMode.FEED)
            return
        elif cmd_name == '/chat':
            await self.switch_mode(ViewMode.CHAT)
            return
        elif cmd_name == '/help':
            self._show_help()
            return
        elif cmd_name == '/clear':
            self.modes[self.current_mode].clear_display()
            self.refresh_display()
            return
        elif cmd_name == '/break':
            await self._handle_breakout(cmd)
            return
        elif cmd_name in ['/quit', '/exit', '/q']:
            self.app.exit()
            return

        # ═══ TIER 2: Mode-Specific Commands ═══
        current_mode = self.modes[self.current_mode]
        if await current_mode.handle_command(cmd):
            # Mode handled it, we're done!
            self.refresh_display()
            return

        # ═══ TIER 3: CLI Fallback Execution ═══
        # Execute as maia command inline
        command = cmd[1:]  # Remove leading /
        output = await self.command_executor.execute(command)
        self._add_command_output(output)
        self.refresh_display()

    def _show_help(self):
        """Show help overlay."""
        self.display_control.text = self._get_welcome_text()

    async def _handle_breakout(self, cmd: str):
        """
        Handle /break <component> command.

        Args:
            cmd: The break command (e.g., "/break feed")
        """
        parts = cmd.split()
        if len(parts) < 2:
            self._add_error_message(
                "Usage: /break <component>\n"
                "Examples: /break feed, /break chat"
            )
            self.refresh_display()
            return

        component = parts[1]
        success = breakout_component(component)

        if success:
            msg = Text()
            msg.append(f"✅ Opened {component} in new terminal\n", style="green")
            self._add_system_message(msg)
        else:
            self._add_error_message(
                f"❌ Failed to open {component} in new terminal\n"
                "Check logs for details."
            )

        self.refresh_display()

    def _add_command_output(self, output: Text):
        """
        Add command output to current mode's display.

        Args:
            output: Rich Text object with command output
        """
        current_mode = self.modes[self.current_mode]
        content = current_mode.get_display_content()
        content.append(output)

    def _add_system_message(self, msg: Text):
        """
        Add system message to current mode's display.

        Args:
            msg: Rich Text object with system message
        """
        current_mode = self.modes[self.current_mode]
        content = current_mode.get_display_content()
        content.append(msg)

    def _add_error_message(self, error: str):
        """
        Add error message to current mode's display.

        Args:
            error: Error message string
        """
        msg = Text()
        msg.append("❌ ", style="bold red")
        msg.append(error + "\n", style="red")
        self._add_system_message(msg)

    async def run(self):
        """Run the TUI application."""
        # Activate initial mode (feed)
        await self.modes[self.current_mode].activate()
        self.refresh_display()

        # Run the app
        await self.app.run_async()

        # Clean up on exit
        await self.modes[self.current_mode].deactivate()


async def run_tui():
    """
    Main entry point for the TUI.

    This is called from the CLI when user runs `maia` or `maia tui`.
    """
    app = PromaiaApp()

    # Set up input handler
    def accept(buff):
        text = app.input_field.text
        app.input_field.text = ""  # Clear input
        asyncio.create_task(app.handle_input(text))

    app.input_field.accept_handler = accept

    # Run the app
    try:
        await app.run()
    except KeyboardInterrupt:
        # Graceful exit on Ctrl+C
        logger.info("TUI interrupted by user")
    except Exception as e:
        logger.error(f"TUI error: {e}", exc_info=True)
        raise
