"""Chat mode - full interactive AI chat in the TUI."""

import asyncio
import logging
from typing import List, Optional
from rich.text import Text

from promaia.tui.modes.base import BaseMode

logger = logging.getLogger(__name__)


class ChatMode(BaseMode):
    """
    Chat mode - full AI conversation interface in the TUI.

    Provides the same features as `maia chat`: multi-API support,
    model switching, temperature control, artifacts, query tools,
    code highlighting, and all slash commands.

    Session state persists across mode switches (feed <-> chat).
    """

    def __init__(self, app):
        super().__init__(app)

        # Lazily initialized on first activation
        self._session = None
        self._command_handler = None
        self._renderer = None
        self._initialized = False

        # Display state
        self.chat_history: List[Text] = []
        self._is_processing = False

    def _ensure_initialized(self):
        """Initialize session, commands, and renderer on first use."""
        if self._initialized:
            return

        from promaia.chat.session import ChatSession
        from promaia.chat.commands import ChatCommandHandler
        from promaia.tui.rendering import MessageRenderer

        self._session = ChatSession()
        self._command_handler = ChatCommandHandler(self._session)
        self._renderer = MessageRenderer()
        self._initialized = True

    async def activate(self):
        """Start chat mode. Creates session on first activation, preserves on re-entry."""
        self.is_active = True
        self._ensure_initialized()

        # Only show welcome on first activation (not on mode switch back)
        if not self.chat_history:
            welcome = self._renderer.render_welcome(
                model_name=self._session.get_model_name(),
                temperature=self._session.current_temperature,
                temp_label=self._session.get_temperature_label(),
                apis=self._session.get_available_apis(),
            )
            self.chat_history.append(welcome)

    async def deactivate(self):
        """Leave chat mode. Session persists for re-entry."""
        self.is_active = False
        # Session is NOT destroyed - it persists across mode switches

    async def handle_input(self, text: str) -> Optional[str]:
        """
        Send a message to the AI and display the response.

        Returns None on success, error string on failure.
        """
        if self._is_processing:
            return "Still processing previous message..."

        self._is_processing = True
        try:
            # Render and display user message
            user_msg = self._renderer.render_user_message(text)
            self.chat_history.append(user_msg)

            # Show thinking indicator
            thinking = self._renderer.render_system_message(
                "Thinking...", style="dim italic"
            )
            self.chat_history.append(thinking)

            # Refresh display to show "Thinking..."
            self.app.refresh_display()

            # Send to AI
            result = await self._session.send_message(text)

            # Remove thinking indicator
            if self.chat_history and self.chat_history[-1] is thinking:
                self.chat_history.pop()

            # Render and display assistant response
            if result.get('error'):
                error_msg = self._renderer.render_error(result['text'])
                self.chat_history.append(error_msg)
            else:
                assistant_msg = self._renderer.render_assistant_message(
                    result['text'],
                    tokens=result.get('tokens'),
                )
                self.chat_history.append(assistant_msg)

            # Keep history manageable
            if len(self.chat_history) > 200:
                # Keep welcome + last 198 entries
                self.chat_history = [self.chat_history[0]] + self.chat_history[-198:]

            return None  # Success

        except Exception as e:
            logger.error(f"Chat input error: {e}", exc_info=True)
            error_msg = self._renderer.render_error(f"Error: {str(e)}")
            self.chat_history.append(error_msg)
            return None  # Don't propagate as app-level error
        finally:
            self._is_processing = False

    async def handle_command(self, command: str) -> bool:
        """
        Handle chat-specific slash commands.

        Returns True if handled, False to fall through to CLI executor.
        """
        self._ensure_initialized()

        result = self._command_handler.handle(command)
        if result.handled:
            # Map style names to theme-friendly display
            style = result.style or "dim"
            msg = self._renderer.render_command_result(result.output, style=style)
            self.chat_history.append(msg)
            return True

        return False  # Not a chat command

    def get_display_content(self) -> List[Text]:
        """Get the chat history for display."""
        return self.chat_history

    def get_prompt(self) -> str:
        """Get the chat mode prompt showing current model."""
        if self._session:
            model = self._session.get_model_name()
            return f"💬 [{model}] "
        return "💬 [chat] "

    def clear_display(self):
        """Clear chat history but keep session state."""
        self._ensure_initialized()
        # Re-render welcome message
        welcome = self._renderer.render_welcome(
            model_name=self._session.get_model_name(),
            temperature=self._session.current_temperature,
            temp_label=self._session.get_temperature_label(),
            apis=self._session.get_available_apis(),
        )
        self.chat_history = [welcome]
        # Clear message history in session too
        self._session.clear_messages()
