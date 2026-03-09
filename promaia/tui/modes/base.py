"""Base class for all TUI modes."""

from abc import ABC, abstractmethod
from typing import Optional, List
from rich.text import Text


class BaseMode(ABC):
    """Base class for all TUI modes."""

    def __init__(self, app):
        """
        Initialize the mode.

        Args:
            app: The PromaiaApp instance
        """
        self.app = app
        self.is_active = False

    @abstractmethod
    async def activate(self):
        """
        Called when entering this mode.

        Use this to start background tasks, initialize state, etc.
        """
        pass

    @abstractmethod
    async def deactivate(self):
        """
        Called when leaving this mode.

        Use this to clean up resources, stop background tasks, etc.
        """
        pass

    @abstractmethod
    async def handle_input(self, text: str) -> Optional[str]:
        """
        Handle user input (non-slash-command text).

        Args:
            text: The input text from the user

        Returns:
            Error message if input couldn't be handled, None if successful
        """
        pass

    async def handle_command(self, command: str) -> bool:
        """
        Handle slash command. Returns True if handled, False otherwise.

        Override to implement mode-specific commands. Commands should
        include the leading slash (e.g., "/e", "/model").

        Args:
            command: The slash command (including /)

        Returns:
            True if this mode handled the command, False to fall through
        """
        return False  # Default: don't handle any commands

    @abstractmethod
    def get_display_content(self) -> List[Text]:
        """
        Get display content as a list of Rich Text objects.

        Returns:
            List of Rich Text objects to display
        """
        pass

    @abstractmethod
    def get_prompt(self) -> str:
        """
        Get input prompt for this mode.

        Returns:
            Prompt string (e.g., "🐙 [feed] ")
        """
        pass

    def clear_display(self):
        """Clear this mode's display content. Override if needed."""
        pass
