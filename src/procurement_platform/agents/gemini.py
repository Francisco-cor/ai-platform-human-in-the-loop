"""Gemini adapter — Fase 4."""

from __future__ import annotations

import json
import time

import httpx

from procurement_platform.agents.adapter import (
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    estimate_cost,
    truncate_context,
)
from procurement_platform.config.settings import get_settings


class GeminiAdapter:
    provider = "gemini"

    def __init__(
        self, api_key: str | None = None, model: str | None = None, base_url: str | None = None
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.base_url = (base_url or settings.gemini_base_url).rstrip("/")
        self.timeout_ms = settings.llm_timeout_ms

    def supports_structured_output(self) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY no configurada")
        start = time.time()
        # Construir prompt combinado
        user_prompt = truncate_context(request.user_prompt, request.max_context_chars)
        system_prompt = request.system_prompt

        # Gemini API espera contents con role
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        # Structured output si hay schema
        if request.response_schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = request.response_schema

        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
            try:
                resp = await client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
            except httpx.TimeoutException as e:
                raise LLMError(f"Gemini timeout: {e}") from e
            except Exception as e:
                raise LLMError(f"Gemini error: {e}") from e

            if resp.status_code != 200:
                # 429, 500 etc. son reintentables a nivel superior
                raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:500]}")

            data = resp.json()
            # Extraer texto
            try:
                candidates = data.get("candidates", [])
                if not candidates:
                    raise LLMError(f"Gemini sin candidates: {data}")
                parts = candidates[0].get("content", {}).get("parts", [])
                raw_text = "".join(p.get("text", "") for p in parts)
                if not raw_text:
                    raw_text = json.dumps(data)
            except Exception as e:
                raise LLMError(f"Gemini parse error: {e}, data={data}") from e

            # Usage
            usage_meta = data.get("usageMetadata", {})
            prompt_tokens = usage_meta.get("promptTokenCount", 0)
            completion_tokens = usage_meta.get("candidatesTokenCount", 0)
            total_tokens = usage_meta.get("totalTokenCount", prompt_tokens + completion_tokens)
            usage = LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            usage.estimated_cost_usd = estimate_cost(self.provider, self.model, usage)

            # Parsear JSON si se esperaba
            content: dict | str = raw_text
            if request.response_schema:
                try:
                    content = json.loads(raw_text)
                except json.JSONDecodeError as e:
                    raise LLMError(f"Gemini no devolvió JSON válido: {raw_text[:500]}") from e

            latency_ms = int((time.time() - start) * 1000)
            return LLMResponse(
                request_id=request.request_id,
                provider=self.provider,
                model=self.model,
                content=content,
                raw_content=raw_text,
                usage=usage,
                latency_ms=latency_ms,
                prompt_version=request.prompt_version,
                graph_version=request.graph_version,
                finish_reason=candidates[0].get("finishReason", "stop"),
            )
