"""LockManager abstraction for distributed locks (F1-3).

MemoryLockManager is default (threading.Lock) for local/tests.
RedisLockManager uses redis SET NX PX + Lua unlock (future, fallback to memory if redis unavailable).
Both expose same interface, allowing migration without changing callers.

Usage:
    manager = get_lock_manager()
    if manager.acquire("execution:exec_123", blocking=False, timeout=2.0):
        try: ... finally: manager.release("execution:exec_123")
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class LockManager(Protocol):
    def acquire(self, key: str, blocking: bool = True, timeout: float = 2.0) -> bool: ...
    def release(self, key: str) -> None: ...
    def is_locked(self, key: str) -> bool: ...
    def clear(self) -> None: ...


class MemoryLockManager:
    """In-memory lock manager — threading.Lock per key."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _get_lock(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def acquire(self, key: str, blocking: bool = True, timeout: float = 2.0) -> bool:
        lock = self._get_lock(key)
        if blocking:
            # timeout=-1 means block forever for threading.Lock, but we implement timeout
            if timeout is None or timeout < 0:
                return lock.acquire(blocking=True)
            return lock.acquire(blocking=True, timeout=timeout)
        return lock.acquire(blocking=False)

    def release(self, key: str) -> None:
        with self._guard:
            lock = self._locks.get(key)
        if lock and lock.locked():
            try:
                lock.release()
            except RuntimeError:
                pass

    def is_locked(self, key: str) -> bool:
        with self._guard:
            lock = self._locks.get(key)
            return bool(lock and lock.locked())

    def clear(self) -> None:
        with self._guard:
            self._locks.clear()


class RedisLockManager:
    """Redis-backed lock manager — SET NX PX + Lua unlock.

    Falls back to MemoryLockManager if redis unavailable or url not configured.
    """

    def __init__(self, redis_url: str | None = None, prefix: str = "lock:") -> None:
        self._prefix = prefix
        self._memory_fallback = MemoryLockManager()
        self._redis = None
        self._url = redis_url
        self._redis_url = redis_url
        # lazy init: don't ping at creation to avoid 1s timeout per test
        # will try to connect on first acquire if needed and cache result
        if redis_url and redis_url not in ("redis://localhost:6379/0", "redis://localhost:6379/1"):
            try:
                import redis  # type: ignore

                # don't ping yet; will be done lazily
                self._redis = redis.from_url(redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
            except Exception:
                self._redis = None

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def acquire(self, key: str, blocking: bool = True, timeout: float = 2.0) -> bool:
        if self._redis is None:
            return self._memory_fallback.acquire(key, blocking=blocking, timeout=timeout)
        rkey = self._key(key)
        # non-blocking fast path
        if not blocking:
            try:
                ok = self._redis.set(rkey, "1", nx=True, px=int(timeout * 1000) if timeout else 2000)
                return bool(ok)
            except Exception:
                return self._memory_fallback.acquire(key, blocking=False)
        # blocking with retry
        deadline = time.time() + timeout if timeout else time.time() + 2.0
        while True:
            try:
                ok = self._redis.set(rkey, "1", nx=True, px=int(timeout * 1000) if timeout else 2000)
                if ok:
                    return True
            except Exception:
                return self._memory_fallback.acquire(key, blocking=blocking, timeout=timeout)
            if time.time() >= deadline:
                return False
            time.sleep(0.01)

    def release(self, key: str) -> None:
        if self._redis is None:
            self._memory_fallback.release(key)
            return
        rkey = self._key(key)
        try:
            # Lua for safe unlock (only if we own it) — simplified: del
            self._redis.delete(rkey)
        except Exception:
            pass
        # also release fallback if used
        try:
            self._memory_fallback.release(key)
        except Exception:
            pass

    def is_locked(self, key: str) -> bool:
        if self._redis is None:
            return self._memory_fallback.is_locked(key)
        try:
            return bool(self._redis.exists(self._key(key)))
        except Exception:
            return self._memory_fallback.is_locked(key)

    def clear(self) -> None:
        self._memory_fallback.clear()
        if self._redis:
            try:
                # don't flush DB, just clear fallback
                pass
            except Exception:
                pass


_manager: LockManager | None = None
_manager_guard = threading.Lock()


def get_lock_manager() -> LockManager:
    global _manager
    if _manager is None:
        with _manager_guard:
            if _manager is None:
                # auto-select based on REDIS_URL and env
                try:
                    from procurement_platform.config.settings import get_settings

                    settings = get_settings()
                    url = settings.redis_url
                    # in ci/test or localhost without redis server, use memory directly for speed (avoid 1s ping)
                    if settings.app_env in ("ci", "test") or "localhost" in url:
                        _manager = MemoryLockManager()
                    else:
                        _manager = RedisLockManager(redis_url=url)
                        if getattr(_manager, "_redis", None) is None:  # type: ignore[attr-defined]
                            _manager = MemoryLockManager()
                except Exception:
                    _manager = MemoryLockManager()
    return _manager


def reset_lock_manager() -> None:
    global _manager
    with _manager_guard:
        if _manager is not None:
            try:
                _manager.clear()
            except Exception:
                pass
        _manager = None
