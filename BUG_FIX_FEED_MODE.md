# Bug Fix: FeedMode Deactivation Error

## Issue

When switching from feed mode to chat mode (or exiting the TUI), the application crashed with:

```
Exception 'FeedAggregator' object has no attribute 'stop'
```

## Root Cause

The `FeedMode.deactivate()` method was calling `await self.aggregator.stop()`, but `FeedAggregator` doesn't have a `stop()` method.

Similarly, `FeedMode._run_feed()` was calling `await self.aggregator.start()`, which also doesn't exist.

## Fix

Updated `promaia/tui/modes/feed_mode.py`:

### 1. Fixed `_run_feed()` to manually start watchers

**Before:**
```python
await self.aggregator.start()  # Method doesn't exist
```

**After:**
```python
# Ensure aggregator is marked as active
self.aggregator.active = True

# Start watchers manually (same as FeedAggregator.start_feed does)
self.aggregator.watchers = [
    asyncio.create_task(self.aggregator._watch_log_file()),
    asyncio.create_task(self.aggregator._watch_database()),
    asyncio.create_task(self.aggregator._watch_loggers()),
]
```

### 2. Fixed `deactivate()` to properly stop aggregator

**Before:**
```python
if self.aggregator:
    await self.aggregator.stop()  # Method doesn't exist
    self.aggregator = None
```

**After:**
```python
# Signal aggregator to stop
if self.aggregator:
    self.aggregator.active = False

# Cancel feed task
if self.feed_task and not self.feed_task.done():
    self.feed_task.cancel()
    try:
        await self.feed_task
    except asyncio.CancelledError:
        pass

# Cancel watcher tasks
if self.aggregator and hasattr(self.aggregator, 'watchers'):
    for watcher in self.aggregator.watchers:
        if not watcher.done():
            watcher.cancel()
    # Wait for watchers to complete
    if self.aggregator.watchers:
        await asyncio.gather(*self.aggregator.watchers, return_exceptions=True)

self.aggregator = None
```

### 3. Added finally block to `_run_feed()` for cleanup

```python
finally:
    # Ensure watchers are cleaned up
    if self.aggregator and hasattr(self.aggregator, 'watchers'):
        self.aggregator.active = False
        for watcher in self.aggregator.watchers:
            if not watcher.done():
                watcher.cancel()
```

## Verification

Lifecycle test now passes:

```bash
python test_feed_mode_lifecycle.py
```

Output:
```
✅ PromaiaApp created
✅ Feed mode activated
✅ Feed mode deactivated successfully!
✅ Chat mode activated
✅ Chat mode deactivated
🎉 All lifecycle tests passed!
```

## Status

✅ Fixed - Mode switching now works properly!

Try it:
```bash
maia           # Launch TUI
/chat          # Switch to chat mode (no longer crashes!)
/feed          # Switch back to feed mode
/quit          # Exit gracefully
```
