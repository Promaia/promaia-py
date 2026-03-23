"""Theme constants for TUI rendering."""

# Style names used by MessageRenderer
CHAT_THEME = {
    # Message roles
    'user_label': 'bold white',
    'user_text': 'white',
    'assistant_label': 'bold cyan',
    'assistant_text': 'white',
    'system_label': 'bold yellow',
    'system_text': 'dim',

    # Code highlighting
    'code_theme': 'monokai',
    'code_border': 'dim cyan',
    'inline_code': 'cyan',

    # Status / feedback
    'success': 'bold green',
    'error': 'bold red',
    'warning': 'bold yellow',
    'info': 'bold cyan',
    'dim': 'dim',

    # Token/cost display
    'tokens': 'dim',
}
