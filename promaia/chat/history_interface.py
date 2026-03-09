"""
Interactive interface for selecting and managing chat history threads.
Provides keyboard navigation and loading capabilities.
"""
from typing import List, Optional, Tuple
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

from promaia.storage.chat_history import ChatHistoryManager, ChatThread
from promaia.utils.display import print_text, print_separator

class HistorySelector:
    """Interactive selector for chat history threads."""
    
    def __init__(self):
        self.history_manager = ChatHistoryManager()
        self.selected_index = 0
        self.threads = []
        self.result = None
        
    def _create_formatted_text(self) -> FormattedText:
        """Create formatted text for display."""
        lines = []
        
        # Header
        lines.append(("class:header", "Recent Chat History"))
        lines.append(("", "\n"))
        lines.append(("class:instructions", "Use ↑/↓ to navigate, Enter to load, Q to quit"))
        lines.append(("", "\n\n"))
        
        if not self.threads:
            lines.append(("class:error", "No chat history found."))
            lines.append(("", "\n"))
            lines.append(("class:instructions", "Use '/save' in a chat to save conversations."))
            lines.append(("", "\n"))
            lines.append(("class:instructions", "Press Q to quit."))
            return FormattedText(lines)
        
        # Thread list
        for i, thread in enumerate(self.threads):
            if i == self.selected_index:
                lines.append(("class:selected", f"  {i + 1}. {str(thread)}"))
            else:
                lines.append(("class:unselected", f"  {i + 1}. {str(thread)}"))
            lines.append(("", "\n"))
        
        lines.append(("", "\n"))
        lines.append(("class:instructions", "Commands: [Enter] Load | [↑/↓] Navigate | [Q] Quit"))
        
        return FormattedText(lines)
    
    def _create_key_bindings(self) -> KeyBindings:
        """Create key bindings for navigation."""
        kb = KeyBindings()
        
        @kb.add('up')
        def move_up(event):
            if self.threads and self.selected_index > 0:
                self.selected_index -= 1
                event.app.invalidate()
        
        @kb.add('down')
        def move_down(event):
            if self.threads and self.selected_index < len(self.threads) - 1:
                self.selected_index += 1
                event.app.invalidate()
        
        @kb.add('enter')
        def load_thread(event):
            if self.threads:
                self.result = ('load', self.threads[self.selected_index])
                event.app.exit()
        
        @kb.add('q')
        def quit_app(event):
            self.result = ('quit', None)
            event.app.exit()
        
        @kb.add('c-c')  # Ctrl+C
        def force_quit(event):
            self.result = ('quit', None)
            event.app.exit()
        
        return kb
    
    def select_thread(self) -> Tuple[str, Optional[ChatThread]]:
        """
        Show the selection interface and return the user's choice.
        
        Returns:
            Tuple of (action, thread) where action is 'load' or 'quit'
        """
        import sys
        
        self.threads = self.history_manager.get_threads()
        
        if not self.threads:
            print_text("No chat history found. Use '/save' in a chat to save conversations.", style="yellow")
            return ('quit', None)
        
        # Check if we're in a proper terminal
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print_text("Interactive selection not available (not running in a terminal).", style="yellow")
            print_text("Available chat threads:", style="white")
            for i, thread in enumerate(self.threads, 1):
                print_text(f"  {i}. {thread}", style="white")
            print_text("\nUse 'maia chat' to start a new conversation.", style="dim")
            return ('quit', None)
        
        try:
            # Create the application
            root_container = HSplit([
                Window(
                    content=FormattedTextControl(
                        lambda: self._create_formatted_text()
                    ),
                    height=len(self.threads) + 8,  # Adjust height based on content
                ),
            ])
            
            layout = Layout(root_container)
            
            # Define styles
            style = Style.from_dict({
                'header': 'bold cyan',
                'instructions': 'italic',
                'selected': 'reverse bold',
                'unselected': '',
                'error': 'red',
            })
            
            # Create and run the application
            app = Application(
                layout=layout,
                key_bindings=self._create_key_bindings(),
                style=style,
                full_screen=False,
            )
            
            app.run()
            
            return self.result or ('quit', None)
            
        except (EOFError, KeyboardInterrupt):
            print_text("\nHistory selection cancelled.", style="yellow")
            return ('quit', None)
        except Exception as e:
            print_text(f"Error with interactive interface: {e}", style="red")
            print_text("Available chat threads:", style="white")
            for i, thread in enumerate(self.threads, 1):
                print_text(f"  {i}. {thread}", style="white")
            print_text("\nUse 'maia chat' to start a new conversation.", style="dim")
            return ('quit', None) 