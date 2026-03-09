"""
Copy-friendly Rich display utilities for markdown and text content.
Provides beautiful terminal output that copies cleanly without formatting artifacts.
"""
import os
import json
from typing import Optional, Union
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
from rich.syntax import Syntax
from rich import box
from rich.theme import Theme


def get_display_config() -> dict:
    """
    Load display configuration from promaia.config.json.
    
    Returns:
        Display configuration dictionary with defaults
    """
    try:
        # Look for config file in current directory
        config_path = "promaia.config.json"
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                return config.get("global", {}).get("display", {})
    except Exception as e:
        # If config can't be loaded, use defaults
        pass
    
    # Default display settings
    return {
        "plain_output": False,
        "copy_friendly_mode": True
    }


class CopyFriendlyConsole:
    """
    A Rich console wrapper optimized for copy-friendly output.
    Avoids panels, boxes, and other formatting that breaks during copy/paste.
    """
    
    def __init__(self, plain_mode: bool = None):
        """
        Initialize the copy-friendly console.
        
        Args:
            plain_mode: If True, uses plain text output. If None, reads from config.
        """
        # Get display configuration
        display_config = get_display_config()
        
        # Check override: environment variable > parameter > config
        if plain_mode is None:
            # Check environment variable first (for temporary overrides)
            env_plain = os.getenv("MAIA_PLAIN_OUTPUT")
            if env_plain is not None:
                plain_mode = env_plain.lower() == "true"
            else:
                # Use config setting
                plain_mode = display_config.get("plain_output", False)
        
        self.plain_mode = plain_mode
        
        if self.plain_mode:
            # Plain text console - no colors, no formatting
            # Disable automatic line wrapping for copy-friendly output
            self.console = Console(
                force_terminal=False,
                no_color=True,
                legacy_windows=False,
                width=9999,  # Very large width to prevent wrapping
                soft_wrap=False  # Prevent automatic line wrapping
            )
        else:
            # Rich console optimized for copy-ability with custom theme
            copy_friendly_theme = Theme({
                "markdown.h1": "bold blue",
                "markdown.h2": "bold cyan", 
                "markdown.h3": "bold green",
                "markdown.code": "cyan",
                "markdown.code_block": "dim cyan",
                "markdown.block_quote": "italic yellow",
                "markdown.list": "white",
                "markdown.item": "white",
                "markdown.emphasis": "italic",
                "markdown.strong": "bold"
            })
            
            # Disable automatic line wrapping for copy-friendly output
            self.console = Console(
                force_terminal=True,
                tab_size=2,  # Smaller tab size
                legacy_windows=False,
                theme=copy_friendly_theme,
                width=9999,  # Very large width to prevent wrapping
                soft_wrap=False  # Prevent automatic line wrapping
            )
    
    def print_markdown(self, content: str, title: Optional[str] = None) -> None:
        """
        Print markdown content in a copy-friendly way that eliminates box characters.
        
        Args:
            content: Markdown content to display
            title: Optional title to display above content
        """
        if self.plain_mode:
            # Plain text output
            if title:
                print(f"\n=== {title} ===")
            print(content)
            print()  # Add spacing
            return
        
        # Rich output but completely copy-friendly
        if title:
            # Use simple text styling instead of panels
            title_text = Text(f"\n{title}", style="bold blue")
            self.console.print(title_text)
            self.console.print("─" * min(len(title), 60), style="dim")  # Simple line separator
        
        # Parse and display markdown manually to avoid box rendering
        self._render_markdown_copy_friendly(content)
        self.console.print()  # Add spacing
    
    def _render_markdown_copy_friendly(self, content: str) -> None:
        """
        Render markdown content without any box characters or problematic formatting.
        Headings are rendered as plain text to preserve copyability.
        """
        lines = content.strip().split('\n')
        
        for line in lines:
            line = line.rstrip()
            
            # Empty lines
            if not line:
                self.console.print()
                continue
            
            # Headers - render as plain text to preserve markdown format when copied
            if line.startswith('### '):
                # Keep the ### prefix for proper markdown copying
                self.console.print(line)  # Plain text, no styling
            elif line.startswith('## '):
                # Keep the ## prefix for proper markdown copying
                self.console.print(line)  # Plain text, no styling
            elif line.startswith('# '):
                # Keep the # prefix for proper markdown copying
                self.console.print(line)  # Plain text, no styling
            
            # Code blocks
            elif line.startswith('```'):
                # Don't skip, just print code block markers as-is for copy-friendly output
                self.console.print(line)
            
            # Lists - use simple hyphens for copy-friendly bullet points
            elif line.startswith('- ') or line.startswith('* '):
                indent = len(line) - len(line.lstrip())
                content_text = line[indent + 2:]  # Remove original marker
                # Always use hyphen for copy-friendly lists
                formatted_text = self._process_inline_formatting(content_text)
                spaces = " " * indent
                self.console.print(f"{spaces}- {formatted_text}", style="white", markup=True)
            
            # Numbered lists  
            elif line.strip() and line.lstrip()[0].isdigit() and '. ' in line:
                formatted_line = self._process_inline_formatting(line)
                self.console.print(formatted_line, style="white", markup=True)
            
            # Blockquotes
            elif line.startswith('> '):
                quote_text = line[2:]
                formatted_quote = self._process_inline_formatting(quote_text)
                self.console.print(f"  {formatted_quote}", style="italic yellow", markup=True)
            
            # Regular paragraphs with inline formatting - styled in medium grey for readability
            else:
                formatted_line = self._process_inline_formatting(line)
                self.console.print(formatted_line, style="white", markup=True)
    
    def _process_inline_formatting(self, text: str) -> str:
        """
        Process inline markdown formatting (**bold**, *italic*, `code`) in text.
        
        Args:
            text: Text containing markdown formatting
            
        Returns:
            Text with Rich markup tags
        """
        import re
        
        # Process in order: code first (to avoid conflicts), then bold, then italic
        
        # Process `code` text first (to avoid conflicts with other formatting)
        formatted_text = re.sub(r'`([^`]+)`', r'[cyan]\1[/cyan]', text)
        
        # Process **bold** text (before single asterisks to avoid conflicts)
        formatted_text = re.sub(r'\*\*([^*]+)\*\*', r'[bold]\1[/bold]', formatted_text)
        
        # Process *italic* text (after bold to avoid conflicts)
        # Use negative lookbehind and lookahead to avoid matching asterisks that are part of bold formatting
        formatted_text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'[italic]\1[/italic]', formatted_text)
        
        return formatted_text
    
    def print_code(self, code: str, language: str = "text", title: Optional[str] = None) -> None:
        """
        Print code content in a copy-friendly way.
        
        Args:
            code: Code content to display
            language: Programming language for syntax highlighting
            title: Optional title to display above code
        """
        if self.plain_mode:
            if title:
                print(f"\n=== {title} ===")
            print(code)
            print()
            return
        
        if title:
            title_text = Text(f"\n{title}", style="bold cyan")
            self.console.print(title_text)
            self.console.print("─" * min(len(title), 60), style="dim")
        
        # Use syntax highlighting without boxes/panels
        syntax = Syntax(
            code,
            language,
            theme="github-dark",
            line_numbers=False,  # Avoid line numbers that break copy/paste
            word_wrap=True,
            background_color=None  # No background color blocks
        )
        
        self.console.print(syntax)
        self.console.print()
    
    def print_text(self, text: str, style: Optional[str] = None, title: Optional[str] = None) -> None:
        """
        Print plain text with optional styling.
        
        Args:
            text: Text content to display
            style: Rich style string (e.g., "bold red")
            title: Optional title to display above text
        """
        if self.plain_mode:
            if title:
                print(f"\n=== {title} ===")
            print(text)
            print()
            return
        
        if title:
            title_text = Text(f"\n{title}", style="bold")
            self.console.print(title_text)
            self.console.print("─" * min(len(title), 60), style="dim")
        
        if style:
            self.console.print(text, style=style)
        else:
            self.console.print(text)
    
    def print_separator(self, text: Optional[str] = None) -> None:
        """
        Print a simple separator line.
        
        Args:
            text: Optional text to include in separator
        """
        if self.plain_mode:
            if text:
                print(f"\n--- {text} ---")
            else:
                print("\n" + "-" * 40)
            return
        
        if text:
            self.console.print(f"\n[dim]--- {text} ---[/dim]")
        else:
            self.console.print("[dim]" + "─" * 40 + "[/dim]")
    
    def get_recordable_output(self, content: str, content_type: str = "markdown") -> str:
        """
        Get the raw text output that would be displayed, for copying purposes.
        
        Args:
            content: Content to render
            content_type: Type of content ("markdown", "code", "text")
            
        Returns:
            Raw text representation suitable for copying
        """
        if content_type == "markdown":
            # For markdown, we can strip some Rich formatting but keep structure
            # This is useful for getting copyable versions
            lines = content.split('\n')
            clean_lines = []
            
            for line in lines:
                # Remove Rich markup while preserving markdown structure
                clean_line = line
                # You could add more cleanup here if needed
                clean_lines.append(clean_line)
            
            return '\n'.join(clean_lines)
        
        return content


# Global instance for easy access
copy_friendly_console = CopyFriendlyConsole()

# Convenience functions
def print_markdown(content: str, title: Optional[str] = None) -> None:
    """Print markdown content in a copy-friendly way."""
    copy_friendly_console.print_markdown(content, title)

def print_code(code: str, language: str = "text", title: Optional[str] = None) -> None:
    """Print code content in a copy-friendly way."""
    copy_friendly_console.print_code(code, language, title)

def print_text(text: str, style: Optional[str] = None, title: Optional[str] = None) -> None:
    """Print text content with optional styling."""
    copy_friendly_console.print_text(text, style, title)

def print_separator(text: Optional[str] = None) -> None:
    """Print a simple separator line."""
    copy_friendly_console.print_separator(text)

def set_plain_mode(enabled: bool = True) -> None:
    """Enable or disable plain text mode globally."""
    global copy_friendly_console
    copy_friendly_console = CopyFriendlyConsole(plain_mode=enabled) 