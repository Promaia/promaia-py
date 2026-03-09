#!/usr/bin/env python3
"""Test FeedMode lifecycle (activate/deactivate)."""

import asyncio
from promaia.tui import PromaiaApp

async def test_mode_switching():
    """Test switching between modes to verify proper cleanup."""
    print("Testing FeedMode lifecycle...")

    # Create app
    app = PromaiaApp()
    print("✅ PromaiaApp created")

    # Activate feed mode (default)
    await app.modes[app.current_mode].activate()
    print("✅ Feed mode activated")

    # Wait a moment
    await asyncio.sleep(0.5)

    # Deactivate feed mode (this was causing the error)
    try:
        await app.modes[app.current_mode].deactivate()
        print("✅ Feed mode deactivated successfully!")
    except Exception as e:
        print(f"❌ Feed mode deactivation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Activate chat mode
    from promaia.tui.app import ViewMode
    app.current_mode = ViewMode.CHAT
    await app.modes[app.current_mode].activate()
    print("✅ Chat mode activated")

    # Deactivate chat mode
    await app.modes[app.current_mode].deactivate()
    print("✅ Chat mode deactivated")

    print("\n🎉 All lifecycle tests passed!")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_mode_switching())
    exit(0 if success else 1)
