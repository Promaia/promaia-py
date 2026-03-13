"""Terminal breakout - spawn components in new terminal windows."""

import subprocess
import shutil
import platform
import logging

logger = logging.getLogger(__name__)


def spawn_in_terminal(command: str) -> bool:
    """
    Spawn command in new terminal window (platform-aware).

    Args:
        command: Command to run (e.g., "maia feed")

    Returns:
        True if spawned successfully, False otherwise
    """
    system = platform.system()

    try:
        if system == 'Darwin':  # macOS
            return _spawn_macos(command)
        elif system == 'Linux':
            return _spawn_linux(command)
        elif system == 'Windows':
            return _spawn_windows(command)
        else:
            logger.error(f"Unsupported platform: {system}")
            return False
    except Exception as e:
        logger.error(f"Failed to spawn terminal: {e}")
        return False


def _spawn_macos(command: str) -> bool:
    """Spawn in macOS terminal."""
    # Try iTerm first (more popular among developers)
    if shutil.which('iTerm'):
        subprocess.Popen([
            'open', '-a', 'iTerm',
            '--args', '/bin/bash', '-c', command
        ])
        return True

    # Fall back to Terminal.app
    script = f'tell application "Terminal" to do script "{command}"'
    subprocess.Popen(['osascript', '-e', script])
    return True


def _spawn_linux(command: str) -> bool:
    """Spawn in Linux terminal."""
    # Try common terminal emulators in order of preference
    terminals = [
        ('gnome-terminal', ['gnome-terminal', '--', 'bash', '-c', command]),
        ('konsole', ['konsole', '-e', 'bash', '-c', command]),
        ('xterm', ['xterm', '-e', 'bash', '-c', command]),
        ('alacritty', ['alacritty', '-e', 'bash', '-c', command]),
        ('kitty', ['kitty', 'bash', '-c', command]),
        ('terminator', ['terminator', '-e', f'bash -c "{command}"']),
    ]

    for term_name, term_cmd in terminals:
        if shutil.which(term_name):
            subprocess.Popen(term_cmd)
            return True

    logger.error("No supported terminal emulator found")
    return False


def _spawn_windows(command: str) -> bool:
    """Spawn in Windows terminal."""
    # Try Windows Terminal first (modern)
    if shutil.which('wt'):
        subprocess.Popen(['wt', 'cmd', '/c', command])
        return True

    # Fall back to cmd.exe
    subprocess.Popen(['start', 'cmd', '/c', command], shell=True)
    return True


def breakout_feed():
    """Break out feed into new terminal."""
    return spawn_in_terminal('maia feed')


def breakout_chat():
    """Break out chat into new terminal."""
    return spawn_in_terminal('maia agent chat')


def breakout_component(component: str) -> bool:
    """
    Break out a specific component into new terminal.

    Args:
        component: Component name (feed, chat, etc.)

    Returns:
        True if spawned successfully
    """
    commands = {
        'feed': 'maia feed',
        'chat': 'maia agent chat',
        'daemon': 'maia daemon status',
    }

    command = commands.get(component.lower())
    if not command:
        logger.error(f"Unknown component: {component}")
        return False

    return spawn_in_terminal(command)
