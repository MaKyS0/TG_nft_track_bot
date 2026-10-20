import asyncio
import time


class AsyncRateLimiter:
    """Spaces calls at least ``1 / rate_per_second`` apart (shared across tasks)."""

    def __init__(self, rate_per_second: float) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._interval = 1.0 / rate_per_second
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + self._interval
            if start > now:
                await asyncio.sleep(start - now)
