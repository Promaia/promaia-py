#!/usr/bin/env python3
"""Test TUI imports and basic initialization."""

print("Testing TUI imports...")

# Test basic imports
try:
    from promaia.tui import PromaiaApp, run_tui
    print("✅ TUI main module imports")
except Exception as e:
    print(f"❌ TUI main module import failed: {e}")
    exit(1)

# Test mode imports
try:
    from promaia.tui.modes import BaseMode, FeedMode, ChatMode
    print("✅ TUI modes import")
except Exception as e:
    print(f"❌ TUI modes import failed: {e}")
    exit(1)

# Test command executor
try:
    from promaia.tui.command_executor import CommandExecutor
    print("✅ Command executor imports")
except Exception as e:
    print(f"❌ Command executor import failed: {e}")
    exit(1)

# Test breakout
try:
    from promaia.tui.breakout import breakout_component
    print("✅ Breakout module imports")
except Exception as e:
    print(f"❌ Breakout module import failed: {e}")
    exit(1)

# Test CLI integration
try:
    from promaia.cli.tui_commands import add_tui_commands, handle_tui_start
    print("✅ CLI TUI commands import")
except Exception as e:
    print(f"❌ CLI TUI commands import failed: {e}")
    exit(1)

# Test PromaiaApp instantiation
try:
    app = PromaiaApp()
    print("✅ PromaiaApp instantiates successfully")
    print(f"   - Default mode: {app.current_mode.value}")
    print(f"   - Modes available: {list(app.modes.keys())}")
except Exception as e:
    print(f"❌ PromaiaApp instantiation failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Test mode creation
try:
    feed_mode = app.modes[app.current_mode]
    print(f"✅ Feed mode created: {feed_mode.__class__.__name__}")
    print(f"   - Prompt: {feed_mode.get_prompt()}")
except Exception as e:
    print(f"❌ Mode creation failed: {e}")
    exit(1)

print("\n🎉 All TUI components loaded successfully!")
print("\nTo launch the TUI, run:")
print("  maia tui")
print("  or just: maia (TUI is the default)")
