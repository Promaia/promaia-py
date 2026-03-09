"""Feed mode - watch live agent activity."""

import asyncio
from typing import List, Optional
from rich.text import Text

from promaia.tui.modes.base import BaseMode
from promaia.agents.feed_aggregator import FeedAggregator
from promaia.agents.feed_formatters import format_as_group_chat


class FeedMode(BaseMode):
    """
    Feed mode - the default "home screen" for watching live agent activity.

    Integrates with FeedAggregator to show real-time events in a group-chat format.
    This mode is read-only - users watch the feed and use slash commands to interact.
    """

    def __init__(self, app):
        super().__init__(app)
        self.aggregator: Optional[FeedAggregator] = None
        self.feed_events: List[Text] = []
        self.feed_task: Optional[asyncio.Task] = None
        self.filters = {}
        self.max_events = 100  # Keep last 100 events

    async def activate(self):
        """Start the feed aggregator and begin watching events."""
        self.is_active = True

        # Start feed aggregator
        self.aggregator = FeedAggregator()

        # Start background task to consume events
        self.feed_task = asyncio.create_task(self._run_feed())

        # Add welcome message
        welcome = Text()
        welcome.append("🐙 Feed Mode Active\n", style="bold cyan")
        welcome.append("Watching for agent activity...\n", style="dim")
        welcome.append("Use ", style="dim")
        welcome.append("/chat", style="bold")
        welcome.append(" to talk with agents or ", style="dim")
        welcome.append("/<command>", style="bold")
        welcome.append(" to run maia commands\n", style="dim")
        self.feed_events.append(welcome)

    async def deactivate(self):
        """Stop the feed aggregator and clean up."""
        self.is_active = False

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

    async def _run_feed(self):
        """
        Background task that consumes events from the aggregator and formats them.
        """
        try:
            # Ensure aggregator is marked as active
            self.aggregator.active = True

            # Start watchers manually (same as FeedAggregator.start_feed does)
            self.aggregator.watchers = [
                asyncio.create_task(self.aggregator._watch_log_file()),
                asyncio.create_task(self.aggregator._watch_database()),
                asyncio.create_task(self.aggregator._watch_loggers()),
            ]

            # Consume events
            while self.is_active:
                try:
                    event = await asyncio.wait_for(
                        self.aggregator.event_queue.get(),
                        timeout=0.5
                    )

                    # Format event using existing formatter
                    formatted = format_as_group_chat(event)

                    # Add to display
                    self.feed_events.append(formatted)

                    # Keep only last N events
                    if len(self.feed_events) > self.max_events:
                        self.feed_events = self.feed_events[-self.max_events:]

                    # Refresh display
                    self.app.refresh_display()

                except asyncio.TimeoutError:
                    # No events, continue
                    continue
                except Exception as e:
                    # Log error but don't crash
                    error_text = Text()
                    error_text.append(f"⚠️  Feed error: {e}\n", style="bold red")
                    self.feed_events.append(error_text)

        except asyncio.CancelledError:
            # Task cancelled, clean exit
            pass
        except Exception as e:
            # Unexpected error
            error_text = Text()
            error_text.append(f"❌ Feed crashed: {e}\n", style="bold red")
            self.feed_events.append(error_text)
        finally:
            # Ensure watchers are cleaned up
            if self.aggregator and hasattr(self.aggregator, 'watchers'):
                self.aggregator.active = False
                for watcher in self.aggregator.watchers:
                    if not watcher.done():
                        watcher.cancel()

    async def handle_input(self, text: str) -> Optional[str]:
        """
        Handle user input in feed mode.

        Feed mode is read-only - users should use slash commands.
        We could support filter updates here (e.g., "--agent grace").
        """
        if text.startswith('--'):
            # Parse filter flags
            return self._parse_filters(text)

        # Otherwise, feed is read-only
        return (
            "Feed is read-only. Use /chat to talk with agents or "
            "/<command> to run maia commands."
        )

    def _parse_filters(self, text: str) -> Optional[str]:
        """Parse filter flags like --agent, --level, --goal."""
        # TODO: Implement filter parsing
        # For now, just show a message
        return "Filters coming soon! Try /chat or /<command> instead."

    def get_display_content(self) -> List[Text]:
        """Get the feed events to display."""
        return self.feed_events

    def get_prompt(self) -> str:
        """Get the feed mode prompt."""
        return "🐙 [feed] "

    def clear_display(self):
        """Clear the feed display."""
        # Keep only the welcome message
        if self.feed_events:
            self.feed_events = [self.feed_events[0]]
        else:
            self.feed_events = []
