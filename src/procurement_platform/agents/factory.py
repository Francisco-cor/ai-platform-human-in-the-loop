"""Factory LLM con fallback Gemini → DeepSeek → fake (Fase 4)."""

from __future__ import annotations

import asyncio

from procurement_platform.agents.adapter import LLMAdapter, LLMError, LLMRequest, LLMResponse
from procurement_platform.agents.deepseek import DeepSeekAdapter
from procurement_platform.agents.fake import FakeAdapter
from procurement_platform.agents.gemini import GeminiAdapter
from procurement_platform.config.settings import get_settings


class LLMFactory:
    @staticmethod
    def create(provider: str | None = None) -> LLMAdapter:
        settings = get_settings()
        prov = (provider or settings.llm_provider).lower()
        if prov == "gemini":
            return GeminiAdapter()
        if prov == "deepseek":
            return DeepSeekAdapter()
        if prov == "fake":
            return FakeAdapter()
        # auto: intentar en orden
        if prov == "auto":
            # preferir gemini si hay key, sino deepseek, sino fake
            if settings.gemini_api_key:
                return GeminiAdapter()
            if settings.deepseek_api_key:
                return DeepSeekAdapter()
            return FakeAdapter()
        raise ValueError(f"provider desconocido: {prov}")

    @staticmethod
    async def generate_with_fallback(request: LLMRequest, max_retries: int = 1) -> LLMResponse:
        settings = get_settings()
        # lista de providers a intentar
        candidates: list[LLMAdapter] = []
        if settings.llm_provider == "fake":
            candidates = [FakeAdapter()]
        elif settings.llm_provider == "gemini":
            candidates = [GeminiAdapter()]
            if settings.llm_fallback_enabled and settings.deepseek_api_key:
                candidates.append(DeepSeekAdapter())
            candidates.append(FakeAdapter())
        elif settings.llm_provider == "deepseek":
            candidates = [DeepSeekAdapter()]
            if settings.llm_fallback_enabled and settings.gemini_api_key:
                candidates.append(GeminiAdapter())
            candidates.append(FakeAdapter())
        else:  # auto
            if settings.gemini_api_key:
                candidates.append(GeminiAdapter())
            if settings.deepseek_api_key:
                candidates.append(DeepSeekAdapter())
            # si ninguno tiene key, fake es el único
            candidates.append(FakeAdapter())

        last_error: Exception | None = None
        for idx, adapter in enumerate(candidates):
            try:
                resp = await adapter.generate(request)
                # marcar si fue fallback (no es el primero)
                if idx > 0:
                    resp.was_fallback = True
                # validar que si se pidió schema, el content sea dict
                if request.response_schema and not isinstance(resp.content, dict):
                    raise LLMError(
                        f"Respuesta no es JSON válida para schema: {resp.raw_content[:300]}"
                    )
                return resp
            except LLMError as e:
                last_error = e
                # si es fake, no reintentar
                if adapter.provider == "fake":
                    raise
                # si fallback deshabilitado, propagar
                if not settings.llm_fallback_enabled and idx == 0:
                    raise
                # intentar siguiente candidato
                continue

        # si todos fallaron, usar fake como último recurso si no se intentó
        if not any(isinstance(c, FakeAdapter) for c in candidates):
            fake = FakeAdapter()
            return await fake.generate(request)

        raise LLMError(f"Todos los providers fallaron. Último error: {last_error}")


# Helper síncrono para tests/orchestrator que no es async
def run_llm_sync(request: LLMRequest) -> LLMResponse:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # crear nuevo loop en thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, LLMFactory.generate_with_fallback(request))
                return future.result()
        else:
            return loop.run_until_complete(LLMFactory.generate_with_fallback(request))
    except RuntimeError:
        return asyncio.run(LLMFactory.generate_with_fallback(request))
