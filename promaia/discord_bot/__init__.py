"""
Discord integration package for Promaia.
"""

# Lazy imports to avoid requiring discord.py at module load time
def __getattr__(name):
    if name == 'PromaiaBot':
        from .bot import PromaiaBot
        return PromaiaBot
    elif name == 'run_bot':
        from .bot import run_bot
        return run_bot
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = ['PromaiaBot', 'run_bot']
