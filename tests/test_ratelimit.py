import time

import pytest

from app.ratelimit import AsyncRateLimiter


async def test_calls_are_spaced():
    limiter = AsyncRateLimiter(50)  # 20 ms apart
    start = time.monotonic()
    for _ in range(4):
        await limiter.acquire()
    assert time.monotonic() - start >= 0.055  # 3 gaps of ~20 ms


def test_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        AsyncRateLimiter(0)
