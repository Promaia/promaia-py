"""Chat mode - interactive AI chat in the TUI (placeholder)."""

from promaia.tui.modes.base import BaseMode


class ChatMode(BaseMode):
    """Chat mode placeholder. Not yet implemented."""

    async def activate(self):
        self.is_active = True

    async def deactivate(self):
        self.is_active = False

    async def handle_input(self, text: str):
        return "Chat mode is not yet implemented."

    def get_display_content(self):
        return []
