"""
MessageRenderer - Renders chat messages with Rich text highlighting.

Converts plain text AI responses into styled Rich Text objects with:
- Code block syntax highlighting via Rich.Syntax
- Markdown-style formatting (bold, italic, inline code)
- Styled user/assistant/system labels
"""
import re
from typing import List, Optional

from rich.text import Text
from rich.syntax import Syntax
from rich.console import Console

from promaia.tui.rendering.themes import CHAT_THEME


class MessageRenderer:
    """Renders chat messages as Rich Text objects with syntax highlighting."""

    def __init__(self, theme: Optional[dict] = None):
        self.theme = theme or CHAT_THEME
        self._console = Console(force_terminal=True, width=200)

    def render_user_message(self, text: str) -> Text:
        """Render a user message."""
        result = Text()
        result.append("You: ", style=self.theme['user_label'])
        result.append(text + "\n", style=self.theme['user_text'])
        return result

    def render_assistant_message(self, text: str, tokens: Optional[dict] = None) -> Text:
        """
        Render an assistant message with code highlighting.

        Parses code blocks (```lang ... ```) and renders them with
        Rich Syntax highlighting. Regular text gets markdown-style formatting.
        """
        result = Text()
        result.append("Assistant: ", style=self.theme['assistant_label'])

        # Split text into code blocks and prose
        parts = self._split_code_blocks(text)

        for part_type, content in parts:
            if part_type == 'code':
                lang, code = content
                # Render code block with syntax highlighting
                result.append("\n")
                syntax_text = self._render_syntax(code, lang)
                result.append(syntax_text)
                result.append("\n")
            else:
                # Render prose with inline formatting
                styled = self._apply_inline_formatting(content)
                result.append(styled)

        # Add token usage if available
        if tokens:
            result.append("\n")
            token_line = self._render_token_info(tokens)
            result.append(token_line)

        result.append("\n")
        return result

    def render_system_message(self, text: str, style: str = "") -> Text:
        """Render a system/status message."""
        result = Text()
        effective_style = style or self.theme['system_text']
        result.append(text + "\n", style=effective_style)
        return result

    def render_command_result(self, text: str, style: str = "") -> Text:
        """Render a command result with appropriate styling."""
        result = Text()
        effective_style = style or self.theme['info']
        result.append(text + "\n", style=effective_style)
        return result

    def render_error(self, text: str) -> Text:
        """Render an error message."""
        result = Text()
        result.append(text + "\n", style=self.theme['error'])
        return result

    def render_welcome(self, model_name: str, temperature: float, temp_label: str, apis: List[str]) -> Text:
        """Render the chat mode welcome message."""
        result = Text()
        result.append("Chat Mode\n", style="bold cyan")
        result.append(f"Model: {model_name}", style="white")
        result.append(f"  |  Temp: {temperature} ({temp_label})", style="dim")
        result.append(f"  |  APIs: {', '.join(apis)}\n", style="dim")
        result.append("\n", style="dim")
        result.append("Commands: ", style="dim")
        result.append("/model", style="bold")
        result.append(" (switch)  ", style="dim")
        result.append("/temp", style="bold")
        result.append(" (creativity)  ", style="dim")
        result.append("/save", style="bold")
        result.append(" (save chat)  ", style="dim")
        result.append("/clear", style="bold")
        result.append(" (reset)\n", style="dim")
        result.append("Switch: ", style="dim")
        result.append("/feed", style="bold")
        result.append(" for activity  |  ", style="dim")
        result.append("/help", style="bold")
        result.append(" for all commands\n", style="dim")
        result.append("─" * 60 + "\n", style="dim")
        return result

    # ── Internal Helpers ──

    def _split_code_blocks(self, text: str) -> List[tuple]:
        """
        Split text into alternating (type, content) tuples.

        Returns list of:
          ('prose', text_string)
          ('code', (language, code_string))
        """
        parts = []
        # Pattern matches ```lang\n...\n``` code blocks
        pattern = re.compile(r'```(\w+)?\n(.*?)```', re.DOTALL)

        last_end = 0
        for match in pattern.finditer(text):
            # Add prose before this code block
            if match.start() > last_end:
                prose = text[last_end:match.start()]
                if prose.strip():
                    parts.append(('prose', prose))

            lang = match.group(1) or 'text'
            code = match.group(2).rstrip('\n')
            parts.append(('code', (lang, code)))
            last_end = match.end()

        # Add remaining prose after last code block
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining.strip():
                parts.append(('prose', remaining))

        # If no code blocks found, entire text is prose
        if not parts:
            parts.append(('prose', text))

        return parts

    def _render_syntax(self, code: str, language: str) -> Text:
        """Render a code block with syntax highlighting using Rich.Syntax."""
        try:
            syntax = Syntax(
                code,
                language,
                theme=self.theme['code_theme'],
                line_numbers=False,
                word_wrap=True,
            )
            # Capture Syntax output as Text
            from io import StringIO
            buf = StringIO()
            console = Console(file=buf, force_terminal=True, width=120)
            console.print(syntax, end='')
            rendered = buf.getvalue()

            result = Text()
            result.append(f"  [{language}]\n", style=self.theme['code_border'])
            result.append_text(Text.from_ansi(rendered))
            return result
        except Exception:
            # Fallback: plain text code block
            result = Text()
            result.append(f"  [{language}]\n", style=self.theme['code_border'])
            result.append(code + "\n", style=self.theme['inline_code'])
            return result

    def _apply_inline_formatting(self, text: str) -> Text:
        """
        Apply markdown-style inline formatting to text.

        Handles: **bold**, *italic*, `inline code`
        """
        result = Text()

        # Process the text character by character using regex
        # Order matters: code first (to avoid conflicts), then bold, then italic
        pattern = re.compile(
            r'`([^`]+)`'           # inline code
            r'|\*\*([^*]+)\*\*'    # bold
            r'|\*([^*]+)\*'        # italic
        )

        last_end = 0
        for match in pattern.finditer(text):
            # Add plain text before this match
            if match.start() > last_end:
                result.append(text[last_end:match.start()])

            if match.group(1):
                # Inline code
                result.append(match.group(1), style=self.theme['inline_code'])
            elif match.group(2):
                # Bold
                result.append(match.group(2), style="bold")
            elif match.group(3):
                # Italic
                result.append(match.group(3), style="italic")

            last_end = match.end()

        # Add remaining text
        if last_end < len(text):
            result.append(text[last_end:])

        return result

    def _render_token_info(self, tokens: dict) -> Text:
        """Render token usage information."""
        result = Text()
        parts = []
        if tokens.get('total_tokens'):
            parts.append(f"{tokens['total_tokens']} tokens")
        if tokens.get('cost') and tokens['cost'] > 0:
            parts.append(f"${tokens['cost']:.4f}")
        if tokens.get('model'):
            parts.append(tokens['model'])

        if parts:
            result.append(f"  [{' | '.join(parts)}]", style=self.theme['tokens'])

        return result
