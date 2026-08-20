"""Rate limiter in-memory — Fase 7.

Sliding window por key (tenant, tool, endpoint).
Para MVP es in-memory; en prod se migraría a Redis.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

_DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    # key_pattern: (max_requests, window_seconds)
    "api:create_execution": (60, 60),  # 60/min por tenant
    "api:approval_decision": (30, 60),
    "tool:search_suppliers": (20, 60),
    "tool:submit_purchase_order": (10, 60),
    "global:ingest": (30, 60),
}


class RateLimitExceeded(RuntimeError):
    def __init__(self, key: str, limit: int, window: int, retry_after: float):
        super().__init__(f"rate_limited:{key}:{limit}/{window}s retry_after={retry_after:.1f}s")
        self.key = key
        self.limit = limit
        self.window = window
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[int, int]] | None = None):
        self.limits = limits or _DEFAULT_LIMITS.copy()
        # key -> deque[timestamp]
        self._windows: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _get_limit(self, key: str) -> tuple[int, int] | None:
        # exact match or prefix match
        if key in self.limits:
            return self.limits[key]
        # try prefix before colon
        prefix = key.split(":")[0] + ":*"
        if prefix in self.limits:
            return self.limits[prefix]
        # fallback global
        return self.limits.get("global:default")

    def check(self, key: str) -> tuple[bool, float]:
        """Verifica sin incrementar. Retorna (allowed, retry_after)."""
        now = time.time()
        limit_win = self._get_limit(key)
        if not limit_win:
            return True, 0
        limit, window = limit_win
        with self._lock:
            dq = self._windows.setdefault(key, deque())
            # purge old
            while dq and dq[0] <= now - window:
                dq.popleft()
            if len(dq) >= limit:
                oldest = dq[0]
                retry_after = (oldest + window) - now
                return False, max(0, retry_after)
            return True, 0

    def hit(self, key: str) -> None:
        """Registra un hit. Lanza RateLimitExceeded si excede."""
        now = time.time()
        limit_win = self._get_limit(key)
        if not limit_win:
            with self._lock:
                dq = self._windows.setdefault(key, deque())
                dq.append(now)
            return
        limit, window = limit_win
        with self._lock:
            dq = self._windows.setdefault(key, deque())
            while dq and dq[0] <= now - window:
                dq.popleft()
            if len(dq) >= limit:
                oldest = dq[0]
                retry_after = (oldest + window) - now
                raise RateLimitExceeded(key, limit, window, retry_after)
            dq.append(now)

    def check_and_hit(self, key: str) -> None:
        self.hit(key)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._windows.clear()
            else:
                self._windows.pop(key, None)

    def get_state(self, key: str) -> dict:
        now = time.time()
        limit_win = self._get_limit(key) or (0, 0)
        limit, window = limit_win
        with self._lock:
            dq = self._windows.get(key, deque())
            # copy purge
            cnt = sum(1 for ts in dq if ts > now - window)
            return {"key": key, "count": cnt, "limit": limit, "window": window}


# Global limiter singleton
_global_limiter: RateLimiter | None = None
_global_lock = Lock()


def get_rate_limiter() -> RateLimiter:
    global _global_limiter
    with _global_lock:
        if _global_limiter is None:
            _global_limiter = RateLimiter()
        return _global_limiter


def reset_rate_limiter() -> None:
    global _global_limiter
    with _global_lock:
        if _global_limiter:
            _global_limiter.reset()
        else:
            _global_limiter = RateLimiter()
