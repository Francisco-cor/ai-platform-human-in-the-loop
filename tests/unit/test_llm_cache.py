"""Fase 6 — LLM cache con tenant isolation y TTL."""

import pytest

from procurement_platform.agents.adapter import LLMRequest, LLMResponse, LLMUsage
from procurement_platform.agents.cache import get_llm_cache, reset_llm_cache, _cache_key
from procurement_platform.agents.factory import LLMFactory
from procurement_platform.observability.metrics import get_metrics, reset_metrics
from procurement_platform.config.settings import get_settings, reset_settings_cache


def test_cache_key_tenant_isolation():
    reset_llm_cache()
    req1 = LLMRequest(
        system_prompt="sys",
        user_prompt="hello cache test",
        response_schema={"type": "object"},
        prompt_version="procurement-v1",
        tenant_id="tenant_demo",
    )
    req2 = LLMRequest(
        system_prompt="sys",
        user_prompt="hello cache test",
        response_schema={"type": "object"},
        prompt_version="procurement-v1",
        tenant_id="tenant_other",
    )
    k1 = _cache_key(req1)
    k2 = _cache_key(req2)
    assert k1 != k2
    assert "tenant_demo" in k1
    assert "tenant_other" in k2
    # same tenant same prompt -> same key
    req1b = LLMRequest(
        system_prompt="sys",
        user_prompt="hello cache test",
        response_schema={"type": "object"},
        prompt_version="procurement-v1",
        tenant_id="tenant_demo",
    )
    assert _cache_key(req1) == _cache_key(req1b)
    # different prompt_version -> different key (invalidation)
    req_v2 = LLMRequest(
        system_prompt="sys",
        user_prompt="hello cache test",
        response_schema={"type": "object"},
        prompt_version="procurement-v2",
        tenant_id="tenant_demo",
    )
    assert _cache_key(req1) != _cache_key(req_v2)


def test_cache_hit_and_metrics():
    reset_llm_cache()
    reset_metrics()
    cache = get_llm_cache(ttl=3600)
    req = LLMRequest(
        system_prompt="sys v1",
        user_prompt="user hello cache metrics",
        response_schema={"type": "object"},
        prompt_version="procurement-v1",
        tenant_id="tenant_demo",
    )
    # miss initially
    assert cache.get(req) is None
    m = get_metrics()
    assert m._cache_misses.get("tenant_demo", 0) == 1
    # set
    resp = LLMResponse(
        request_id=req.request_id,
        provider="fake",
        model="fake",
        content={"hello": "world"},
        raw_content='{"hello":"world"}',
        usage=LLMUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
    )
    cache.set(req, resp)
    # hit
    cached = cache.get(req)
    assert cached is not None
    assert cached.content == {"hello": "world"}
    assert m._cache_hits.get("tenant_demo", 0) == 1
    assert m.get_cache_hit_rate("tenant_demo") == 0.5  # 1 hit / (1 hit+1 miss)


@pytest.mark.asyncio
async def test_cache_via_factory():
    # factory should use cache: second identical call hits
    reset_llm_cache()
    reset_metrics()
    reset_settings_cache()
    # ensure clean settings
    import os

    os.environ.pop("PROCUREMENT_TENANT_LLM_CONFIG", None)
    os.environ["PROCUREMENT_LLM_PROVIDER"] = "fake"
    reset_settings_cache()
    req = LLMRequest(
        system_prompt="sys factory",
        user_prompt="hello factory cache test identical",
        response_schema={
            "type": "object",
            "required": ["supplier_id", "lines"],
            "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array", "items": {}}},
        },
        prompt_version="procurement-v1",
        tenant_id="tenant_demo",
        execution_id="exec_cache_test1",
    )
    # adjust schema to be one fake handles (needs supplier_id+lines+evidence)
    req.response_schema = {
        "type": "object",
        "required": ["supplier_id", "lines", "evidence"],
        "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array", "items": {}}, "evidence": {"type": "string"}},
    }
    resp1 = await LLMFactory.generate_with_fallback(req)
    assert not resp1.was_cached
    # second identical request (different execution_id but same prompt content after sanitization)
    req2 = LLMRequest(
        system_prompt="sys factory",
        user_prompt="hello factory cache test identical",
        response_schema=req.response_schema,
        prompt_version="procurement-v1",
        tenant_id="tenant_demo",
        execution_id="exec_cache_test2",
    )
    resp2 = await LLMFactory.generate_with_fallback(req2)
    assert resp2.was_cached
    m = get_metrics()
    assert m.get_cache_hit_rate("tenant_demo") > 0.3


def test_cache_tenant_isolation_enforced_via_factory():
    reset_llm_cache()
    reset_metrics()
    reset_settings_cache()
    import os

    os.environ["PROCUREMENT_LLM_PROVIDER"] = "fake"
    reset_settings_cache()
    # first tenant_demo
    req_demo = LLMRequest(
        system_prompt="sys tenant",
        user_prompt="tenant isolation test same prompt",
        response_schema={"type": "object", "properties": {"supplier_id": {"type": "string"}}, "required": ["supplier_id"]},
        prompt_version="procurement-v1",
        tenant_id="tenant_demo",
        execution_id="exec_demo1",
    )
    req_demo.response_schema = {
        "type": "object",
        "required": ["supplier_id", "lines", "evidence"],
        "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array", "items": {}}, "evidence": {"type": "string"}},
    }
    # need to set same as factory expects
    import asyncio

    async def _run():
        r1 = await LLMFactory.generate_with_fallback(req_demo)
        assert not r1.was_cached
        # same prompt but tenant_other should be miss (isolation)
        req_other = LLMRequest(
            system_prompt="sys tenant",
            user_prompt="tenant isolation test same prompt",
            response_schema=req_demo.response_schema,
            prompt_version="procurement-v1",
            tenant_id="tenant_other",
            execution_id="exec_other1",
        )
        r2 = await LLMFactory.generate_with_fallback(req_other)
        assert not r2.was_cached
        # second call for tenant_other should hit within same tenant
        req_other2 = LLMRequest(
            system_prompt="sys tenant",
            user_prompt="tenant isolation test same prompt",
            response_schema=req_demo.response_schema,
            prompt_version="procurement-v1",
            tenant_id="tenant_other",
            execution_id="exec_other2",
        )
        r3 = await LLMFactory.generate_with_fallback(req_other2)
        assert r3.was_cached

    asyncio.run(_run())


def test_cache_metrics_exposed(client):
    # ensure /metrics exposes llm_cache metrics
    reset_metrics()
    from procurement_platform.agents.adapter import LLMRequest

    # trigger a cache hit/miss via factory
    import asyncio
    from procurement_platform.agents.factory import LLMFactory

    reset_llm_cache()

    async def _do():
        req = LLMRequest(
            system_prompt="sys metrics",
            user_prompt="metrics test prompt",
            response_schema={"type": "object", "required": ["supplier_id", "lines", "evidence"], "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array"}, "evidence": {"type": "string"}}},
            prompt_version="procurement-v1",
            tenant_id="tenant_demo",
            execution_id="exec_metrics1",
        )
        await LLMFactory.generate_with_fallback(req)
        await LLMFactory.generate_with_fallback(req)  # second should hit

    asyncio.run(_do())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    txt = resp.text
    assert "llm_cache_hits_total" in txt
    assert "llm_cache_hit_rate" in txt
