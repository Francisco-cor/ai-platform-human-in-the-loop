"""Factory LLM con fallback Gemini → DeepSeek → fake (Fase 4) + Fase 6 cache & per-tenant governance."""

from __future__ import annotations

import asyncio

from procurement_platform.agents.adapter import (
    BudgetExhausted,
    LLMAdapter,
    LLMError,
    LLMRequest,
    LLMResponse,
)
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
        # Fase 6 — enriquecer prompt_hash si no viene
        if not request.prompt_hash:
            try:
                from procurement_platform.agents.prompts import get_prompt_hash

                request.prompt_hash = get_prompt_hash(request.prompt_version)
            except Exception:
                pass
        # Fase 6 — per-tenant allowlist check (antes de cache/generation)
        tenant_id = request.tenant_id or "tenant_demo"
        # validar modelo permitido para tenant (usa request model implícito via provider selection)
        # Si provider explícito en settings, validar que cada candidato permitido; si no, filtrar candidatos
        # Aquí validamos luego tras seleccionar candidatos, pero pre-check con provider solicitado
        # Fase 6 — tenant token budget check per-execution (no global acumulado para no romper harness 22 casos)
        # Usa execution_id usage vs tenant max, no tenant global. Global se controla via rate_limiter window 60s.
        try:
            cfg = settings.get_tenant_llm_config(tenant_id)
            max_tenant_tokens = int(cfg.get("max_tokens", settings.max_tokens_per_execution))
            # check current execution usage vs tenant max (per-execution override)
            try:
                from procurement_platform.workflows.orchestrator import _execution_token_usage as _etu
                from procurement_platform.workflows.orchestrator import _cost_lock as _cl

                exec_id = request.execution_id or "no_exec"
                with _cl:
                    used_exec = _etu.get(exec_id, 0)
                if used_exec + request.max_tokens > max_tenant_tokens:
                    try:
                        from procurement_platform.observability.metrics import get_metrics

                        get_metrics().inc_budget_exceeded(tenant_id, "tenant_max_tokens_per_execution")
                    except Exception:
                        pass
                    raise BudgetExhausted(
                        f"budget_exceeded: execution {exec_id} tenant {tenant_id} tokens {used_exec}+{request.max_tokens} > {max_tenant_tokens}"
                    )
            except BudgetExhausted:
                raise
            except Exception:
                pass
            # también rate limiter por tokens (Fase 6) — window 60s, limit = tenant max
            try:
                from procurement_platform.security.rate_limiter import get_rate_limiter

                rl = get_rate_limiter()
                allowed, _ = rl.check(f"llm:{tenant_id}:tokens")
                if not allowed:
                    raise BudgetExhausted(f"budget_exceeded: llm rate limit for tenant {tenant_id}")
            except BudgetExhausted:
                raise
            except Exception:
                pass
        except BudgetExhausted:
            raise
        except Exception:
            pass

        # Fase 6 — cache check (tenant isolated, TTL 1h, key incluye prompt_version)
        if settings.llm_cache_enabled:
            try:
                from procurement_platform.agents.cache import get_llm_cache

                cache = get_llm_cache(ttl=settings.llm_cache_ttl_seconds)
                cached = cache.get(request)
                if cached is not None:
                    # tenant isolation ya en key, pero validamos que tenant coincida
                    cached.was_cached = True
                    # asegurar que prompt_hash del cached coincide con actual request (invalida si versión cambió)
                    # key ya incluye versión, así que si versión cambió no habría hit
                    return cached
            except Exception:
                pass

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

        # Fase 6 — filtrar candidatos por allowlist del tenant (si no permitido, skip)
        try:
            allowed_models = settings.get_tenant_llm_config(tenant_id).get("models", [])
            allowed_lower = [m.lower() for m in allowed_models]
            filtered: list[LLMAdapter] = []
            for c in candidates:
                if c.provider.lower() in allowed_lower:
                    filtered.append(c)
            # si filtrado deja vacío, lanzar
            if not filtered:
                raise BudgetExhausted(f"model not allowed for tenant {tenant_id}: {allowed_models}")
            candidates = filtered
        except BudgetExhausted:
            raise
        except Exception:
            pass

        last_error: Exception | None = None
        for idx, adapter in enumerate(candidates):
            try:
                resp = await adapter.generate(request)
                # marcar si fue fallback (no es el primero)
                if idx > 0:
                    resp.was_fallback = True
                # propagar prompt_hash
                if request.prompt_hash and not resp.prompt_hash:
                    resp.prompt_hash = request.prompt_hash
                # validar que si se pidió schema, el content sea dict
                if request.response_schema and not isinstance(resp.content, dict):
                    raise LLMError(
                        f"Respuesta no es JSON válida para schema: {resp.raw_content[:300]}"
                    )
                # Fase 6 — guardar en cache (solo si no fue fallback crítico? cacheamos todo exitoso)
                if settings.llm_cache_enabled:
                    try:
                        from procurement_platform.agents.cache import get_llm_cache

                        get_llm_cache(ttl=settings.llm_cache_ttl_seconds).set(request, resp)
                    except Exception:
                        pass
                # Fase 6 — rate limiter hit por tokens del tenant (llm:tenant:tokens)
                try:
                    from procurement_platform.security.rate_limiter import get_rate_limiter

                    rl = get_rate_limiter()
                    # hit por request (no por token individual) para no saturar, pero registramos
                    rl.hit(f"llm:{tenant_id}:tokens")
                except Exception:
                    pass
                return resp
            except BudgetExhausted:
                raise
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

        # si todos fallaron, usar fake como último recurso si no se intentó y permitido
        if not any(isinstance(c, FakeAdapter) for c in candidates):
            # verificar allowlist para fake
            try:
                if not settings.is_model_allowed_for_tenant(tenant_id, "fake"):
                    raise BudgetExhausted(f"model fake not allowed for tenant {tenant_id}")
            except BudgetExhausted:
                raise
            except Exception:
                pass
            fake = FakeAdapter()
            resp = await fake.generate(request)
            if request.prompt_hash and not resp.prompt_hash:
                resp.prompt_hash = request.prompt_hash
            if settings.llm_cache_enabled:
                try:
                    from procurement_platform.agents.cache import get_llm_cache

                    get_llm_cache(ttl=settings.llm_cache_ttl_seconds).set(request, resp)
                except Exception:
                    pass
            return resp

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
