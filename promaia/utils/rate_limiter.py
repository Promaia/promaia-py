"""
Adaptive rate limiter for API calls.
Intelligently manages request rates to avoid hitting API limits.
"""
import time
import asyncio
from collections import deque
from typing import Optional


class AdaptiveRateLimiter:
    """
    Adaptive rate limiter that only delays when necessary.
    Tracks request times in a sliding window and only delays if approaching limits.
    """

    def __init__(self, calls_per_second: float = 3.0, burst_size: Optional[int] = None):
        """
        Initialize the rate limiter.

        Args:
            calls_per_second: Maximum number of calls per second (default: 3.0 for Notion)
            burst_size: Maximum burst size. If None, uses calls_per_second value
        """
        self.calls_per_second = calls_per_second
        self.burst_size = burst_size or int(calls_per_second)
        self.call_times = deque(maxlen=max(self.burst_size, int(calls_per_second * 2)))
        self.window_size = 1.0  # 1 second window

    async def acquire(self):
        """
        Acquire permission to make an API call.
        Only delays if we're approaching the rate limit.
        """
        now = time.time()

        # Remove old entries outside the window
        while self.call_times and (now - self.call_times[0]) > self.window_size:
            self.call_times.popleft()

        # Check if we need to wait
        if len(self.call_times) >= self.calls_per_second:
            # Calculate how long to wait
            oldest_call = self.call_times[0]
            time_since_oldest = now - oldest_call

            if time_since_oldest < self.window_size:
                # We need to wait
                wait_time = self.window_size - time_since_oldest
                await asyncio.sleep(wait_time)
                now = time.time()

                # Clean up again after sleeping
                while self.call_times and (now - self.call_times[0]) > self.window_size:
                    self.call_times.popleft()

        # Record this call
        self.call_times.append(now)

    def get_stats(self) -> dict:
        """
        Get statistics about the rate limiter.

        Returns:
            Dictionary with rate limiter statistics
        """
        now = time.time()
        recent_calls = sum(1 for t in self.call_times if (now - t) <= 1.0)

        return {
            'calls_per_second_limit': self.calls_per_second,
            'recent_calls_last_second': recent_calls,
            'total_calls_tracked': len(self.call_times),
            'utilization_pct': round((recent_calls / self.calls_per_second) * 100, 1)
        }

    def reset(self):
        """Reset the rate limiter state."""
        self.call_times.clear()


class NotionRateLimiter(AdaptiveRateLimiter):
    """
    Rate limiter specifically tuned for Notion API.
    Notion has a limit of 3 requests per second per integration.
    """

    def __init__(self):
        """Initialize with Notion-specific settings."""
        # Use 3 requests per second as per Notion's documented limit
        # Set burst size slightly lower for safety
        super().__init__(calls_per_second=3.0, burst_size=3)


# Global instance for Notion API
_notion_rate_limiter = None


def get_notion_rate_limiter() -> NotionRateLimiter:
    """
    Get the global Notion rate limiter instance.

    Returns:
        NotionRateLimiter instance
    """
    global _notion_rate_limiter
    if _notion_rate_limiter is None:
        _notion_rate_limiter = NotionRateLimiter()
    return _notion_rate_limiter


def reset_notion_rate_limiter():
    """Reset the global Notion rate limiter."""
    global _notion_rate_limiter
    if _notion_rate_limiter:
        _notion_rate_limiter.reset()
