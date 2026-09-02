"""Feature flag service (Unleash-style local) — Fase 9.

- Fuente: yaml local flags.yaml → futuro Unleash
- Flags: rag_reranker, llm_cache, async_workers, ui_v2
- Orchestrator lee flags por tenant
- Eval harness testa con flag on/off
- make flags-list -> cat infra/feature_flags.yaml

Formato flags.yaml:
  flags:
    rag_reranker: {enabled: false, tenants: ["tenant_demo"]}
    llm_cache: {enabled: true}
    async_workers: {enabled: false}
    ui_v2: {enabled: false}
"""

from __future__ import annotations

import pathlib
import threading
from typing import Any

import yaml

_DEFAULT_FLAGS = {
    "rag_reranker": {"enabled": False, "tenants": []},
    "llm_cache": {"enabled": True, "tenants": []},
    "async_workers": {"enabled": False, "tenants": []},
    "ui_v2": {"enabled": False, "tenants": []},
    "webhooks": {"enabled": True, "tenants": []},
    "notifications": {"enabled": True, "tenants": []},
}

_FLAGS_PATH_CANDIDATES = [
    pathlib.Path("infra/feature_flags.yaml"),
    pathlib.Path("feature_flags.yaml"),
    pathlib.Path("config/feature_flags.yaml"),
]


class FlagProvider:
    def __init__(self, path: pathlib.Path | None = None):
        self.path = path or self._find_path()
        self._flags: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.load()

    def _find_path(self) -> pathlib.Path | None:
        for p in _FLAGS_PATH_CANDIDATES:
            if p.exists():
                return p
        # Also check cwd parents
        cur = pathlib.Path.cwd()
        for _ in range(3):
            cand = cur / "infra" / "feature_flags.yaml"
            if cand.exists():
                return cand
            cur = cur.parent
        return None

    def load(self) -> None:
        flags = dict(_DEFAULT_FLAGS)
        if self.path and self.path.exists():
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                # Support both {flags: {...}} and direct {...}
                file_flags = data.get("flags") if isinstance(data, dict) and "flags" in data else data
                if isinstance(file_flags, dict):
                    for k, v in file_flags.items():
                        if isinstance(v, bool):
                            flags[k] = {"enabled": v, "tenants": []}
                        elif isinstance(v, dict):
                            flags[k] = {"enabled": bool(v.get("enabled", False)), "tenants": v.get("tenants", [])}
                        else:
                            flags[k] = {"enabled": bool(v), "tenants": []}
            except Exception:
                pass
        with self._lock:
            self._flags = flags

    def is_enabled(self, flag: str, tenant_id: str | None = None) -> bool:
        with self._lock:
            cfg = self._flags.get(flag)
            if not cfg:
                return False
            if not cfg.get("enabled"):
                # check per-tenant override
                tenants = cfg.get("tenants") or []
                if tenant_id and tenant_id in tenants:
                    return True
                return False
            # globally enabled — check if tenants restricted?
            tenants = cfg.get("tenants") or []
            if tenants and tenant_id and tenant_id not in tenants:
                # if tenants list specified and not in list, disabled for this tenant
                return False
            return True

    def get_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._flags)

    def set_flag(self, flag: str, enabled: bool, tenants: list[str] | None = None) -> None:
        with self._lock:
            self._flags[flag] = {"enabled": enabled, "tenants": tenants or []}
        # Optionally persist to file if path exists
        if self.path:
            try:
                data = {"flags": self._flags}
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            except Exception:
                pass

    def reload(self) -> None:
        self.load()


# Global
_global_flags: FlagProvider | None = None
_global_lock = threading.Lock()


def get_flag_provider(path: pathlib.Path | None = None) -> FlagProvider:
    global _global_flags
    with _global_lock:
        if _global_flags is None or path:
            _global_flags = FlagProvider(path=path)
        return _global_flags


def reset_flag_provider() -> None:
    global _global_flags
    with _global_lock:
        _global_flags = None


def is_flag_enabled(flag: str, tenant_id: str | None = None) -> bool:
    return get_flag_provider().is_enabled(flag, tenant_id)
