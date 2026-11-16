"""Per-caller rate limits (sliding window, in process memory).

Keyed on the Cognito sub, so it runs after the token is verified. Each API
task keeps its own counts: with N tasks a caller can reach N times the
limit, which is fine for abuse damping on /devices and DELETE /me.
"""

import time
from collections import deque
from typing import Callable, Deque, Dict

from cachetools import TTLCache
from fastapi import Depends, HTTPException, status

from app.dependencies import get_current_user


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: TTLCache = TTLCache(maxsize=100_000, ttl=window_seconds)

    def hit(self, key: str) -> float:
        """Record one call. Returns 0 when allowed, else seconds to wait."""
        now = self.clock()
        q: Deque[float] = self._hits.get(key) or deque()
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            self._hits[key] = q
            return max(q[0] + self.window - now, 1.0)
        q.append(now)
        self._hits[key] = q
        return 0.0

    def reset(self) -> None:
        self._hits.clear()


def per_sub_limit(limiter: SlidingWindowLimiter):
    async def check(user_info: Dict[str, str] = Depends(get_current_user)) -> None:
        wait = limiter.hit(user_info["sub"])
        if wait:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Try again shortly.",
                headers={"Retry-After": str(int(wait + 0.999))},
            )

    return check


# POST /devices and DELETE /devices/{id} share one budget per account.
devices_limiter = SlidingWindowLimiter(limit=30, window_seconds=600)
# DELETE /me: enough for a few retries after a partial failure.
delete_me_limiter = SlidingWindowLimiter(limit=5, window_seconds=600)
