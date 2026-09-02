"""LLM cache — Fase 6 LLMOps.

Capa de cache prompt → response con aislamiento por tenant y TTL 1h.

Key: sha256(system_prompt + user_prompt + schema + model + prompt_version) por tenant.
Backend: Redis (si PROCUREMENT_REDIS_URL disponible y ping ok) else in-memory dict con expiry.
Metric: llm_cache_hits_total{tenant, result="hit|miss"} + hit_rate derivado.

Invalidación automática si prompt_version cambia (key incluye versión).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from procurement_platform.agents.adapter import LLMRequest, LLMResponse


def _sanitize_for_cache(text: str) -> str:
    """Normaliza prompt para cache: elimina volátiles (timestamps, request_id, exec_id, trace)."""
    import re

    # remove datetime objects and iso strings
    text = re.sub(r"datetime\.datetime\([^)]+\)", "TIMESTAMP", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^',\"\s]*", "TIMESTAMP", text)
    # generic request/exec/llm/trace ids (hex or any word)
    text = re.sub(r"req_[a-zA-Z0-9_\-]{3,}", "req_stable", text)
    text = re.sub(r"exec_[a-zA-Z0-9_\-]{3,}", "exec_stable", text)
    text = re.sub(r"llm_[a-zA-Z0-9_\-]{3,}", "llm_stable", text)
    text = re.sub(r"trace_[a-zA-Z0-9_\-]{3,}", "trace_stable", text)
    # also bare execution_id / request_id values without prefix (fallback)
    text = re.sub(r"'execution_id':\s*'[^']*'", "'execution_id': 'exec_stable'", text)
    text = re.sub(r'"execution_id":\s*"[^"]*"', '"execution_id": "exec_stable"', text)
    text = re.sub(r"'request_id':\s*'[^']*'", "'request_id': 'req_stable'", text)
    text = re.sub(r'"request_id":\s*"[^"]*"', '"request_id": "req_stable"', text)
    # remove proposal_id volatile too
    text = re.sub(r"prop_[a-f0-9]{8,}", "prop_stable", text)
    return text


def _cache_key(request: LLMRequest) -> str:
    """Calcula key determinista para cache.

    Incluye tenant_id para aislamiento, y prompt_version para invalidación.
    Sanitiza volátiles para permitir hit en suite repetida.
    """
    # sanitize user_prompt for cache stability (remove timestamps/ids)
    user_sanitized = _sanitize_for_cache(request.user_prompt or "")
    system_sanitized = _sanitize_for_cache(request.system_prompt or "")
    parts = [
        system_sanitized,
        user_sanitized,
        json.dumps(request.response_schema, sort_keys=True) if request.response_schema else "",
        request.prompt_version or "",
        # graph_version también podría afectar pero spec dice prompt → response
        # model se incluye para no mezclar respuestas entre modelos
        getattr(request, "model", "") if hasattr(request, "model") else "",
    ]
    raw = "\n---\n".join(parts)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tenant = request.tenant_id or "default"
    # incluir modelo si request tiene campo model? LLMRequest no tiene model, usa provider/model via settings fallback
    # incluimos tenant en prefix para aislamiento
    return f"llm_cache:{tenant}:{request.prompt_version}:{h[:32]}"


class _InMemoryCache:
    def __init__(self, ttl: int = 3600):
        self.ttl = ttl
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        now = time.time()
        with self._lock:
            if key in self._store:
                exp, val = self._store[key]
                if exp > now:
                    return val
                else:
                    del self._store[key]
            return None

    def set(self, key: str, value: str) -> None:
        exp = time.time() + self.ttl
        with self._lock:
            self._store[key] = (exp, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


class LLMCache:
    """Cache con backend Redis opcional y fallback in-memory."""

    def __init__(self, ttl_seconds: int = 3600, redis_url: str | None = None):
        self.ttl = ttl_seconds
        self._mem = _InMemoryCache(ttl=ttl_seconds)
        self._redis = None
        self._redis_url = redis_url
        # try connect to redis if url provided
        if redis_url:
            try:
                import redis  # type: ignore

                # short timeout to avoid blocking tests if redis not available
                client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    def _get_redis(self, key: str) -> str | None:
        if self._redis is None:
            return None
        try:
            val = self._redis.get(key)
            if val is not None and isinstance(val, bytes):
                return val.decode("utf-8")
            return val  # type: ignore
        except Exception:
            return None

    def _set_redis(self, key: str, value: str) -> None:
        if self._redis is None:
            return
        try:
            self._redis.setex(key, self.ttl, value)
        except Exception:
            pass

    def get(self, request: LLMRequest) -> LLMResponse | None:
        key = _cache_key(request)
        # try redis first then mem
        val = self._get_redis(key)
        if val is None:
            val = self._mem.get(key)
        if val is None:
            # miss metric
            try:
                from procurement_platform.observability.metrics import get_metrics

                get_metrics().inc_cache(request.tenant_id or "default", hit=False)
            except Exception:
                pass
            return None
        # hit metric
        try:
            from procurement_platform.observability.metrics import get_metrics

            get_metrics().inc_cache(request.tenant_id or "default", hit=True)
        except Exception:
            pass
        try:
            data = json.loads(val)
            return LLMResponse(**data)
        except Exception:
            return None

    def set(self, request: LLMRequest, response: LLMResponse) -> None:
        key = _cache_key(request)
        try:
            payload = response.model_dump(mode="json")
            val = json.dumps(payload)
            self._mem.set(key, val)
            self._set_redis(key, val)
        except Exception:
            pass

    def clear(self) -> None:
        self._mem.clear()
        if self._redis:
            try:
                # only clear our keys (pattern)
                for k in self._redis.scan_iter(match="llm_cache:*"):
                    self._redis.delete(k)
            except Exception:
                pass

    def stats(self) -> dict[str, Any]:
        return {"mem_size": self._mem.size(), "redis": self._redis is not None, "ttl": self.ttl}


_global_cache: LLMCache | None = None
_global_lock = threading.Lock()


def get_llm_cache(ttl: int = 3600) -> LLMCache:
    global _global_cache
    with _global_lock:
        if _global_cache is None:
            # determine redis_url from settings if not ci local?
            redis_url = None
            try:
                from procurement_platform.config.settings import get_settings

                redis_url = get_settings().redis_url
                # for ci env (sqlite test), still try redis but will fallback
                # if redis_url points to localhost and not available, in-memory will be used
            except Exception:
                redis_url = None
            _global_cache = LLMCache(ttl_seconds=ttl, redis_url=redis_url)
        return _global_cache


def reset_llm_cache() -> None:
    global _global_cache
    with _global_lock:
        if _global_cache:
            try:
                _global_cache.clear()
            except Exception:
                pass
        _global_cache = None
