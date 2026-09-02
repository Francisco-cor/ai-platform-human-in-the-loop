"""Fase 9 — feature flags tests."""

import pathlib

from procurement_platform.infra.feature_flags import FlagProvider, get_flag_provider, reset_flag_provider, is_flag_enabled


def test_flag_provider_load_and_tenant(tmp_path):
    flags_path = tmp_path / "flags.yaml"
    flags_path.write_text(
        """
flags:
  rag_reranker:
    enabled: false
    tenants: ["tenant_demo"]
  llm_cache:
    enabled: true
  async_workers:
    enabled: false
""",
        encoding="utf-8",
    )
    provider = FlagProvider(path=flags_path)
    # rag_reranker disabled globally but enabled for tenant_demo via tenants list? Actually disabled false but tenants includes tenant_demo -> should be True for tenant_demo?
    # Our logic: if not enabled but tenants includes, return True
    assert provider.is_enabled("rag_reranker", "tenant_demo") is True
    assert provider.is_enabled("rag_reranker", "tenant_other") is False
    assert provider.is_enabled("llm_cache") is True
    assert provider.is_enabled("async_workers") is False
    # set flag
    provider.set_flag("ui_v2", True, tenants=["tenant_demo"])
    assert provider.is_enabled("ui_v2", "tenant_demo") is True
    assert provider.is_enabled("ui_v2", "tenant_other") is False


def test_flag_provider_via_api(client):
    # default flags.yaml has llm_cache true
    resp = client.get("/v1/flags")
    assert resp.status_code == 200
    data = resp.json()
    assert "flags" in data
    assert "llm_cache" in data["flags"]
    # get single flag
    resp2 = client.get("/v1/flags/llm_cache?tenant_id=tenant_demo")
    assert resp2.status_code == 200
    assert resp2.json()["flag"] == "llm_cache"
    assert "enabled" in resp2.json()


def test_flag_affects_orchestrator(monkeypatch, db_session):
    # Test that flag toggles behavior: llm_cache disabled should not hit cache
    from procurement_platform.infra.feature_flags import get_flag_provider, reset_flag_provider
    import tempfile
    import pathlib
    import os

    # Create temp flags with llm_cache disabled
    tmp = pathlib.Path(tempfile.mktemp(suffix=".yaml"))
    tmp.write_text("flags:\n  llm_cache:\n    enabled: false\n", encoding="utf-8")
    # Need to reset provider with this path
    reset_flag_provider()
    provider = get_flag_provider(path=tmp)
    assert provider.is_enabled("llm_cache") is False

    # Now test factory cache disabled via flag: run two identical LLM calls, should not hit cache
    from procurement_platform.agents.adapter import LLMRequest
    from procurement_platform.agents.factory import LLMFactory
    from procurement_platform.agents.cache import reset_llm_cache
    from procurement_platform.observability.metrics import reset_metrics, get_metrics
    import asyncio

    reset_llm_cache()
    reset_metrics()

    async def _run():
        req = LLMRequest(
            system_prompt="flag test",
            user_prompt="hello flag",
            response_schema={"type": "object", "properties": {"supplier_id": {"type": "string"}}, "required": ["supplier_id"]},
            prompt_version="procurement-v1",
            tenant_id="tenant_demo",
            execution_id="exec_flag1",
        )
        req.response_schema = {
            "type": "object",
            "required": ["supplier_id", "lines", "evidence"],
            "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array"}, "evidence": {"type": "string"}},
        }
        r1 = await LLMFactory.generate_with_fallback(req)
        assert not r1.was_cached
        # second identical should also be miss because flag disabled
        req2 = LLMRequest(
            system_prompt="flag test",
            user_prompt="hello flag",
            response_schema=req.response_schema,
            prompt_version="procurement-v1",
            tenant_id="tenant_demo",
            execution_id="exec_flag2",
        )
        r2 = await LLMFactory.generate_with_fallback(req2)
        # With flag disabled, second should not be cached either (since we disabled cache, no hit)
        # Actually our flag check is for llm_cache enabled -> if disabled, we skip cache get/set
        # So second will be miss
        assert not r2.was_cached

    asyncio.run(_run())
    # cleanup
    reset_flag_provider()
    # restore default provider (will load infra/feature_flags.yaml)
    get_flag_provider()
    tmp.unlink(missing_ok=True)
