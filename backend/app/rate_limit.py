"""Stage H: a per-user sliding-window rate limit on the two uncapped, DB/compute
-heavy strategy-run routes (POST /strategy-runs, POST .../paper-run). Pure
in-process state -- no Redis, consistent with CLAUDE.md section 2's rejection of
extra infrastructure for a workload this small.

SINGLE-INSTANCE LIMITATION: state lives in this process' memory only. Running
more than one backend instance behind a load balancer gives each instance its
own independent counter, so the effective limit becomes max_requests * instance
count, not a shared cap. Fine for this app's current single-instance deployment
(CLAUDE.md section 2: Render free tier, one container); would need a shared
store (e.g. Redis) the moment there's more than one instance. Documented in
README.md and CLAUDE.md section 12 as well, so this isn't discovered the hard
way at scale-out time."""
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import HTTPException, status


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window: timedelta):
        self.max_requests = max_requests
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, now: datetime) -> None:
        """Raises 429 if `key` is already at the limit; otherwise records this
        request. `now` is always an explicit parameter (never read from the wall
        clock inside this class), matching every other `now`-taking function in
        this codebase, so tests can move time without sleeping."""
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "rate limit exceeded -- try again shortly",
                )
            hits.append(now)


# Limits are a placeholder, not a researched figure (same caveat CLAUDE.md
# section 11 already applies to the cost-assumption defaults) -- generous enough
# not to interfere with normal manual/dev use, tight enough to stop an accidental
# loop. Revisit before public launch. Separate instances per route: creating a
# run and running the paper engine have different cost profiles, so one user
# hammering one of them shouldn't eat into their budget for the other.
STRATEGY_RUN_LIMITER = SlidingWindowLimiter(max_requests=10, window=timedelta(seconds=60))
PAPER_RUN_LIMITER = SlidingWindowLimiter(max_requests=10, window=timedelta(seconds=60))
# Replay Trading (2026-11-23): session creation is DB/compute-light but still
# gets the same shape of cap as a strategy run. Order placement is a human
# clicking Buy/Sell while watching a chart replay at some playback speed, so it
# allows more requests per window than the above -- still enough to stop a
# scripted loop, not enough to interfere with fast manual clicking.
REPLAY_SESSION_LIMITER = SlidingWindowLimiter(max_requests=10, window=timedelta(seconds=60))
REPLAY_ORDER_LIMITER = SlidingWindowLimiter(max_requests=60, window=timedelta(seconds=60))
