"""
Upstox API rate-limit guard for the live engine.

Upstox enforces three rolling windows per user: 50 req/sec, 500 req/min,
2000 req per 30 min. This module makes a breach impossible by gating every
HTTP call through a triple-window token bucket with headroom:

    45 / second      450 / minute      1900 / 30 minutes

plus defensive 429 handling (honours Retry-After). If demand exceeds quota,
calls WAIT instead of failing — the engine's cycle stretches and its
bar-walk reconciliation replays any missed bars idempotently, so slowing
down is always safe; breaching never happens.

Single-threaded by design (the engine's main loop is sequential).

Usage (allstrategy.py):
    from upstox_ratelimit import http_get
    res = http_get(url, headers=_headers(), params=..., timeout=10)
"""
import time
from collections import deque

import requests

# Upstox caps with safety headroom
WINDOWS = (
    (1.0, 45),        # 50/sec   -> 45
    (60.0, 450),      # 500/min  -> 450
    (1800.0, 1900),   # 2000/30m -> 1900
)
MAX_429_WAITS = 8     # patient quota waits before giving up on one call


class RateLimiter:
    def __init__(self, windows=WINDOWS):
        self.windows = [(span, cap, deque()) for span, cap in windows]

    def acquire(self):
        """Block until one request is permitted under every window."""
        while True:
            now = time.time()
            wait = 0.0
            for span, cap, q in self.windows:
                while q and q[0] <= now - span:
                    q.popleft()
                if len(q) >= cap:
                    wait = max(wait, q[0] + span - now)
            if wait <= 0:
                break
            if wait > 5:
                print(f"[RATE] window full — pacing {wait:.0f}s "
                      "(quota guard, not an error)")
            time.sleep(min(wait, 30.0) + 0.01)
        stamp = time.time()
        for _, _, q in self.windows:
            q.append(stamp)

    @property
    def usage(self):
        now = time.time()
        out = {}
        for span, cap, q in self.windows:
            n = sum(1 for t in q if t > now - span)
            out[f"{int(span)}s"] = f"{n}/{cap}"
        return out


_limiter = RateLimiter()


def http_get(url, **kwargs):
    """Drop-in replacement for requests.get with quota gating + 429 care.
    Returns the Response; raises requests exceptions like requests.get."""
    for attempt in range(MAX_429_WAITS):
        _limiter.acquire()
        res = requests.get(url, **kwargs)
        if res.status_code != 429:
            return res
        hdr = res.headers.get("Retry-After")
        wait = int(hdr) + 1 if hdr and str(hdr).isdigit() else 30 * (attempt + 1)
        wait = min(wait, 300)
        print(f"[RATE] 429 from Upstox — backing off {wait}s "
              f"({attempt + 1}/{MAX_429_WAITS}) | usage {_limiter.usage}")
        time.sleep(wait)
    return res        # caller's HTTP-status handling deals with it


def usage():
    """Current quota consumption, for diagnostics/logging."""
    return _limiter.usage
