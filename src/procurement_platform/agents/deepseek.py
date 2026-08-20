"""DeepSeek adapter — Fase 4 (fallback si Gemini no disponible).

DeepSeek expone API compatible OpenAI en https://api.deepseek.com/chat/completions
"""
from __future__ import annotations

import json
import time

import httpx

from procurement_platform.agents.adapter import LLMError, LLMRequest, LLMResponse, LLMUsage, estimate_cost, truncate_context
from procurement_platform.config.settings import get_settings


class DeepSeekAdapter:
    provider = "deepseek"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.deepseek_api_key
        self.model = model or settings.deepseek_model
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.timeout_ms = settings.llm_timeout_ms

    def supports_structured_output(self) -> bool:
        # DeepSeek chat soporta JSON mode via response_format
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            raise LLMError("DEEPSEEK_API_KEY no configurada")
        start = time.time()
        user_prompt = truncate_context(request.user_prompt, request.max_context_chars)
        system_prompt = request.system_prompt

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_schema:
            # DeepSeek soporta response_format json_object para forzar JSON
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMError(f"DeepSeek timeout: {e}") from e
            except Exception as e:
                raise LLMError(f"DeepSeek error: {e}") from e

            if resp.status_code != 200:
                raise LLMError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")

            data = resp.json()
            try:
                choices = data.get("choices", [])
                if not choices:
                    raise LLMError(f"DeepSeek sin choices: {data}")
                msg = choices[0].get("message", {})
                raw_text = msg.get("content", "")
                if not raw_text:
                    raw_text = json.dumps(data)
            except Exception as e:
                raise LLMError(f"DeepSeek parse error: {e}, data={data}") from e

            usage_data = data.get("usage", {})
            prompt_tokens = usage_data.get("prompt_tokens", 0)
            completion_tokens = usage_data.get("completion_tokens", 0)
            total_tokens = usage_data.get("total_tokens", prompt_tokens + completion_tokens)
            usage = LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens)
            usage.estimated_cost_usd = estimate_cost(self.provider, self.model, usage)

            content: dict | str = raw_text
            if request.response_schema:
                try:
                    content = json.loads(raw_text)
                except json.JSONDecodeError as e:
                    raise LLMError(f"DeepSeek no devolvió JSON válido: {raw_text[:500]}") from e

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
                finish_reason=choices[0].get("finish_reason", "stop"),
            )
