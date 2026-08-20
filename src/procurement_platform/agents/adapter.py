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

    async def generate(self, request: LLMRequest) -> LLMResponse:
        ...

    def supports_structured_output(self) -> bool:
        ...


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


def estimate_cost(provider: str, model: str, usage: LLMUsage) -> float:
    # tarifas versionadas simplificadas (USD por 1K tokens)
    rates = {
        ("gemini", "gemini-2.0-flash"): (0.075 / 1000, 0.30 / 1000),
        ("deepseek", "deepseek-chat"): (0.14 / 1000, 0.28 / 1000),
        ("deepseek", "deepseek-reasoner"): (0.55 / 1000, 2.19 / 1000),
        ("fake", "fake"): (0.0, 0.0),
    }
    key = (provider, model)
    if key not in rates:
        # fallback genérico
        return 0.0
    prompt_rate, completion_rate = rates[key]
    return usage.prompt_tokens * prompt_rate + usage.completion_tokens * completion_rate
