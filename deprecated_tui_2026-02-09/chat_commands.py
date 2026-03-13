"""
ChatCommandHandler - Handles all slash commands for chat sessions.

Extracts command handling from interface.py's main loop into a reusable class
that works with ChatSession for both CLI and TUI chat modes.
"""
import logging
from typing import Tuple, Optional

from promaia.chat.session import ChatSession

logger = logging.getLogger(__name__)


class CommandResult:
    """Result of a command execution."""

    __slots__ = ('handled', 'output', 'style')

    def __init__(self, handled: bool, output: str = "", style: str = ""):
        self.handled = handled
        self.output = output
        self.style = style  # Rich style hint: "green", "red", "yellow", "cyan", "dim"


class ChatCommandHandler:
    """
    Handles slash commands for a ChatSession.

    Each handler returns a CommandResult indicating whether the command
    was handled and any output text to display.
    """

    def __init__(self, session: ChatSession):
        self.session = session
        self._debug_mode = False

        # Map command names to handler methods
        self._handlers = {
            '/clear': self._handle_clear,
            '/c': self._handle_clear,
            '/mute': self._handle_mute,
            '/unmute': self._handle_unmute,
            '/debug': self._handle_debug,
            '/push': self._handle_push,
            '/s': self._handle_sync,
            '/model': self._handle_model,
            '/temp': self._handle_temp,
            '/save': self._handle_save,
            '/queries': self._handle_queries,
            '/remove-query': self._handle_remove_query,
            '/artifact': self._handle_artifact,
            '/artifacts': self._handle_artifact,
            '/a': self._handle_artifact,
        }

    def handle(self, command: str) -> CommandResult:
        """
        Handle a slash command.

        Args:
            command: Full command string including slash (e.g. "/model opus")

        Returns:
            CommandResult with handled status and output
        """
        parts = command.strip().split(None, 1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handler = self._handlers.get(cmd_name)
        if handler:
            return handler(args)

        return CommandResult(False)

    def get_known_commands(self) -> list:
        """Get list of command names this handler knows about."""
        return list(self._handlers.keys())

    # ── Command Handlers ──

    def _handle_clear(self, args: str) -> CommandResult:
        """Clear all context and messages."""
        self.session.clear_context()
        self.session.clear_messages()
        return CommandResult(True, "Context and messages cleared.", "green")

    def _handle_mute(self, args: str) -> CommandResult:
        """Mute context (hide from AI but preserve)."""
        if self.session.mute_context():
            sources = self.session.context_state.get('muted_sources', [])
            return CommandResult(
                True,
                f"Context muted (hidden from AI, but preserved)\n"
                f"  Muted {len(sources)} sources",
                "yellow",
            )
        return CommandResult(True, "Context is already muted", "yellow")

    def _handle_unmute(self, args: str) -> CommandResult:
        """Restore muted context."""
        if self.session.unmute_context():
            sources = self.session.context_state.get('sources', [])
            total = self.session.context_state.get('total_pages_loaded', 0)
            return CommandResult(
                True,
                f"Context unmuted (restored)\n"
                f"  Restored {len(sources)} sources with {total} pages",
                "green",
            )
        return CommandResult(True, "Context is not muted", "yellow")

    def _handle_debug(self, args: str) -> CommandResult:
        """Toggle debug mode."""
        import os
        self._debug_mode = not self._debug_mode
        os.environ["MAIA_DEBUG"] = "1" if self._debug_mode else "0"
        status = "enabled" if self._debug_mode else "disabled"
        return CommandResult(True, f"Debug mode {status}.", "yellow")

    def _handle_push(self, args: str) -> CommandResult:
        """Push conversation to Notion."""
        try:
            import asyncio
            from promaia.chat.interface import push_chat_to_notion
            result = asyncio.run(push_chat_to_notion(self.session.messages))
            return CommandResult(True, str(result), "green")
        except ImportError:
            return CommandResult(True, "Push to Notion not available.", "red")
        except Exception as e:
            return CommandResult(True, f"Error pushing to Notion: {e}", "red")

    def _handle_sync(self, args: str) -> CommandResult:
        """Sync context databases."""
        return CommandResult(
            True,
            "Database sync is available in full CLI mode.\n"
            "Run: maia chat -s <source> to load context.",
            "yellow",
        )

    def _handle_model(self, args: str) -> CommandResult:
        """Switch AI model."""
        if not args.strip():
            # Show current model and available options
            current = self.session.get_model_name()
            apis = self.session.get_available_apis()
            lines = [f"Current model: {current}"]
            lines.append(f"Available APIs: {', '.join(apis)}")
            lines.append("Usage: /model <name>")
            lines.append("Names: opus, sonnet, gpt, gemini, flash, pro, llama")
            return CommandResult(True, "\n".join(lines), "cyan")

        result = self.session.switch_model(args.strip())
        if result:
            return CommandResult(True, f"Switched to {result}", "green")
        return CommandResult(
            True,
            f"Could not switch to '{args.strip()}'. "
            f"Try: opus, sonnet, gpt, gemini, flash, pro, llama",
            "red",
        )

    def _handle_temp(self, args: str) -> CommandResult:
        """Set or show temperature."""
        if not args.strip():
            label = self.session.get_temperature_label()
            return CommandResult(
                True,
                f"Current temperature: {self.session.current_temperature} ({label})\n"
                "Usage: /temp <0.0-2.0>",
                "cyan",
            )

        try:
            new_temp = float(args.strip())
            if self.session.set_temperature(new_temp):
                label = self.session.get_temperature_label()
                return CommandResult(True, f"Temperature set to {new_temp} ({label})", "cyan")
            return CommandResult(True, "Temperature must be between 0.0 and 2.0", "red")
        except ValueError:
            return CommandResult(True, "Invalid temperature value. Use: /temp 0.9", "red")

    def _handle_save(self, args: str) -> CommandResult:
        """Save conversation to history."""
        custom_name = args.strip().strip('"\'') if args.strip() else None
        result = self.session.save_conversation(custom_name)
        if result:
            return CommandResult(True, f"Conversation saved as: {result}", "green")
        return CommandResult(True, "No conversation to save.", "yellow")

    def _handle_queries(self, args: str) -> CommandResult:
        """List AI-generated queries."""
        ai_queries = self.session.context_state.get('ai_queries', [])
        if not ai_queries:
            return CommandResult(True, "No AI-generated queries in this session.", "dim")

        lines = ["AI-Generated Queries:"]
        for i, query_info in enumerate(ai_queries, 1):
            query_type = query_info.get('type', 'unknown')
            query_text = query_info.get('query', '')
            reasoning = query_info.get('reasoning', '')
            lines.append(f"  {i}. [{query_type}] \"{query_text}\"")
            if reasoning:
                short = reasoning[:150] + "..." if len(reasoning) > 150 else reasoning
                lines.append(f"     Reasoning: {short}")
        lines.append("\nUse '/remove-query N' to remove a query")
        return CommandResult(True, "\n".join(lines), "cyan")

    def _handle_remove_query(self, args: str) -> CommandResult:
        """Remove an AI-generated query by index."""
        ai_queries = self.session.context_state.get('ai_queries', [])
        if not args.strip():
            return CommandResult(True, "Usage: /remove-query <number>", "yellow")

        try:
            idx = int(args.strip()) - 1
            if 0 <= idx < len(ai_queries):
                removed = ai_queries.pop(idx)
                return CommandResult(
                    True,
                    f"Removed query: [{removed.get('type')}] \"{removed.get('query')}\"",
                    "green",
                )
            return CommandResult(True, f"Invalid query number. Valid: 1-{len(ai_queries)}", "red")
        except ValueError:
            return CommandResult(True, "Invalid number. Use: /remove-query 1", "red")

    def _handle_artifact(self, args: str) -> CommandResult:
        """List or show artifacts."""
        artifact_manager = self.session.context_state.get('artifact_manager')
        if not artifact_manager:
            return CommandResult(True, "No artifact manager available.", "yellow")

        artifacts = artifact_manager.list_artifacts()
        if not artifacts:
            return CommandResult(True, "No artifacts in this session.", "dim")

        lines = ["Artifacts:"]
        for art_id, info in artifacts.items():
            art_type = info.get('type', 'text')
            version = info.get('version', 1)
            preview = info.get('preview', '')[:80]
            lines.append(f"  #{art_id} [{art_type}] v{version}: {preview}")
        return CommandResult(True, "\n".join(lines), "cyan")
