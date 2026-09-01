"""Adapter LLM — interfaz común para Gemini y DeepSeek (Fase 4).

Garantías:
- Salidas estructuradas con JSON Schema + validación Pydantic.
- Fallback determinista si Gemini no está disponible → DeepSeek → fake.
- Metadata registrada (provider, model, prompt_version, tokens, coste).
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from pydantic import BaseModel, Field


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # coste estimado USD (tarifas versionadas)
    estimated_cost_usd: float = 0.0


class LLMRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"llm_{uuid.uuid4().hex[:8]}")
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any] | None = None  # JSON Schema para salida estructurada
    temperature: float = 0.2
    max_tokens: int = 2048
    # control de contexto
    max_context_chars: int = 12000
    # metadata
    prompt_version: str = "procurement-v1"
    graph_version: str = "procurement-graph-v1"
    tenant_id: str | None = None
    execution_id: str | None = None
    trace_id: str | None = None


class LLMResponse(BaseModel):
    request_id: str
    provider: str  # gemini | deepseek | fake
    model: str
    content: dict[str, Any] | str  # parsed JSON si response_schema, o raw string
    raw_content: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    latency_ms: int = 0
    prompt_version: str = "procurement-v1"
    graph_version: str = "procurement-graph-v1"
    # para auditoría
    finish_reason: str = "stop"
    was_fallback: bool = False
    error: str | None = None


class LLMAdapter(Protocol):
    provider: str
    model: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    def supports_structured_output(self) -> bool: ...


class LLMError(RuntimeError):
    pass


class LLMValidationError(ValueError):
    pass


# Helpers
def truncate_context(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    # truncar manteniendo inicio y fin (útil para logs)
    half = max_chars // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


class BudgetExhausted(RuntimeError):
    """F5-4: presupuesto tokens/coste excedido para tenant/execution."""

    pass


_cost_rates_cache: dict | None = None
_cost_rates_version: str | None = None


def _load_cost_rates() -> dict:
    global _cost_rates_cache, _cost_rates_version
    if _cost_rates_cache is not None:
        return _cost_rates_cache
    # try yaml
    try:
        import pathlib
        import yaml  # type: ignore

        p = pathlib.Path("config/cost_rates.yaml")
        if not p.exists():
            p = pathlib.Path("src/procurement_platform/config/cost_rates.yaml")
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            _cost_rates_cache = data
            _cost_rates_version = str(data.get("version", "v1"))
            return data
    except Exception:
        pass
    # fallback hard-coded
    _cost_rates_cache = {
        "version": "v1",
        "rates": {
            "gemini": {"gemini-2.0-flash": {"prompt_per_1k": 0.075, "completion_per_1k": 0.30}},
            "deepseek": {
                "deepseek-chat": {"prompt_per_1k": 0.14, "completion_per_1k": 0.28},
                "deepseek-reasoner": {"prompt_per_1k": 0.55, "completion_per_1k": 2.19},
            },
            "fake": {"fake": {"prompt_per_1k": 0.0, "completion_per_1k": 0.0}},
        },
    }
    return _cost_rates_cache


def get_cost_rates_version() -> str:
    data = _load_cost_rates()
    return str(data.get("version", "v1"))


def estimate_cost(provider: str, model: str, usage: LLMUsage) -> float:
    data = _load_cost_rates()
    rates = data.get("rates", {})
    # try exact
    prov = rates.get(provider, {})
    mdl = prov.get(model) if isinstance(prov, dict) else None
    if mdl and "prompt_per_1k" in mdl:
        pr = float(mdl["prompt_per_1k"]) / 1000
        cr = float(mdl["completion_per_1k"]) / 1000
        return usage.prompt_tokens * pr + usage.completion_tokens * cr
    # fallback default
    default = rates.get("default", {"prompt_per_1k": 0.10, "completion_per_1k": 0.30})
    pr = float(default.get("prompt_per_1k", 0.10)) / 1000
    cr = float(default.get("completion_per_1k", 0.30)) / 1000
    # also try hardcoded legacy
    legacy = {
        ("gemini", "gemini-2.0-flash"): (0.075 / 1000, 0.30 / 1000),
        ("deepseek", "deepseek-chat"): (0.14 / 1000, 0.28 / 1000),
        ("deepseek", "deepseek-reasoner"): (0.55 / 1000, 2.19 / 1000),
        ("fake", "fake"): (0.0, 0.0),
    }
    if (provider, model) in legacy:
        pr, cr = legacy[(provider, model)]
        return usage.prompt_tokens * pr + usage.completion_tokens * cr
    return usage.prompt_tokens * pr + usage.completion_tokens * cr


def reset_cost_rates_cache() -> None:
    global _cost_rates_cache, _cost_rates_version
    _cost_rates_cache = None
    _cost_rates_version = None
