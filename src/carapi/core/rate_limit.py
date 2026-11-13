import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after: float  # seconds until a slot frees up


class SlidingWindowLimiter:
    """In-memory sliding-window log: at most `limit` hits per `window` seconds per identity."""

    def __init__(
        self,
        limit: int,
        window: float,
        clock: Callable[[], float] = time.monotonic,
        sweep_every: int = 1000,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0
        self._sweep_every = sweep_every

    def hit(self, identity: str) -> RateLimitResult:
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            self._calls += 1
            if self._calls % self._sweep_every == 0:
                self._sweep(cutoff)
            q = self._hits.setdefault(identity, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return RateLimitResult(False, self.limit, 0, q[0] + self.window - now)
            q.append(now)
            return RateLimitResult(True, self.limit, self.limit - len(q), q[0] + self.window - now)

    def _sweep(self, cutoff: float) -> None:
        for ident in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[ident]

    def __len__(self) -> int:
        return len(self._hits)


def ceil_seconds(value: float) -> int:
    return max(1, math.ceil(value))
