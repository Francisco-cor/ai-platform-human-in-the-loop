"""
Tool plugin registry — Fase 11.

Registry via entry_points:
  pyproject.toml [project.entry-points."procurement.tools"]
  calculate_shortage = procurement_platform.tools.builtin.calculate_shortage:handler

Also manual registration:
  from procurement_platform.platform.tools.registry import register_tool
  register_tool(name, schema, handler)

Definitions.py now reads registry to build TOOL_SCHEMAS and allowlist.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any, Callable, Dict


_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_tool(name: str, schema: Dict[str, Any], handler: Callable) -> None:
    """Register a tool manually (builtin or domain)."""
    _TOOL_REGISTRY[name] = {"schema": schema, "handler": handler}


def get_tool_registry() -> Dict[str, Dict[str, Any]]:
    # Lazy load entry_points on first call
    if not _TOOL_REGISTRY:
        _load_entry_points()
    return dict(_TOOL_REGISTRY)


def _load_entry_points() -> None:
    try:
        eps = importlib.metadata.entry_points(group="procurement.tools")
    except TypeError:
        # Python 3.10 compat: no group arg
        eps = importlib.metadata.entry_points().get("procurement.tools", [])  # type: ignore
    for ep in eps:
        try:
            handler = ep.load()
            # handler should have .schema or we infer empty
            schema = getattr(handler, "schema", {"type": "object", "properties": {}})
            _TOOL_REGISTRY[ep.name] = {"schema": schema, "handler": handler}
        except Exception:
            continue


def list_tools() -> list[str]:
    return sorted(get_tool_registry().keys())


def get_tool_schema(name: str) -> Dict[str, Any] | None:
    reg = get_tool_registry()
    return reg.get(name, {}).get("schema")


def get_tool_handler(name: str) -> Callable | None:
    reg = get_tool_registry()
    return reg.get(name, {}).get("handler")


def clear_registry() -> None:
    _TOOL_REGISTRY.clear()
