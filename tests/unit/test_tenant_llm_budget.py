"""Fase 6 — per-tenant token budgets y model allowlist."""

import os

import pytest

from procurement_platform.agents.adapter import BudgetExhausted, LLMRequest
from procurement_platform.agents.factory import run_llm_sync
from procurement_platform.config.settings import get_settings, reset_settings_cache
from procurement_platform.observability.metrics import reset_metrics
from procurement_platform.workflows.orchestrator import reset_finops_state
from procurement_platform.agents.cache import reset_llm_cache


@pytest.fixture(autouse=True)
def _clean_env():
    # preserve env
    orig = os.environ.get("PROCUREMENT_TENANT_LLM_CONFIG")
    orig_provider = os.environ.get("PROCUREMENT_LLM_PROVIDER")
    yield
    if orig is not None:
        os.environ["PROCUREMENT_TENANT_LLM_CONFIG"] = orig
    else:
        os.environ.pop("PROCUREMENT_TENANT_LLM_CONFIG", None)
    if orig_provider is not None:
        os.environ["PROCUREMENT_LLM_PROVIDER"] = orig_provider
    else:
        os.environ["PROCUREMENT_LLM_PROVIDER"] = "fake"
    reset_settings_cache()
    reset_llm_cache()
    reset_metrics()
    reset_finops_state()


def _make_req(tenant_id: str, max_tokens: int = 2048):
    return LLMRequest(
        system_prompt="sys",
        user_prompt="hello budget test",
        response_schema={
            "type": "object",
            "required": ["supplier_id", "lines", "evidence"],
            "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array", "items": {}}, "evidence": {"type": "string"}},
        },
        prompt_version="procurement-v1",
        tenant_id=tenant_id,
        execution_id=f"exec_{tenant_id}_test",
        max_tokens=max_tokens,
    )


def test_tenant_allowlist_fake_only():
    os.environ["PROCUREMENT_TENANT_LLM_CONFIG"] = '{"tenant_demo": {"models": ["fake"], "max_tokens": 8000}}'
    os.environ["PROCUREMENT_LLM_PROVIDER"] = "fake"
    reset_settings_cache()
    reset_llm_cache()
    req = _make_req("tenant_demo")
    # fake is allowed -> should succeed
    resp = run_llm_sync(req)
    assert resp.provider == "fake"

    # tenant_other allowlist gemini only -> fake should be blocked
    os.environ["PROCUREMENT_TENANT_LLM_CONFIG"] = '{"tenant_demo": {"models": ["fake"], "max_tokens": 8000}, "tenant_other": {"models": ["gemini"], "max_tokens": 8000}}'
    reset_settings_cache()
    reset_llm_cache()
    req_other = _make_req("tenant_other")
    with pytest.raises(BudgetExhausted, match="not allowed"):
        run_llm_sync(req_other)


def test_tenant_budget_per_execution_enforced():
    os.environ["PROCUREMENT_TENANT_LLM_CONFIG"] = '{"tenant_demo": {"models": ["fake"], "max_tokens": 5}}'
    os.environ["PROCUREMENT_LLM_PROVIDER"] = "fake"
    reset_settings_cache()
    reset_llm_cache()
    reset_finops_state()
    reset_metrics()
    req = _make_req("tenant_demo", max_tokens=2048)
    # per-execution limit 5 -> 0+2048 >5 should raise BudgetExhausted
    with pytest.raises(BudgetExhausted, match="budget_exceeded"):
        run_llm_sync(req)
    # check metric incremented
    from procurement_platform.observability.metrics import get_metrics

    txt = get_metrics().generate()
    assert "budget_exceeded_total" in txt
    assert "tenant_demo" in txt


def test_tenant_budget_rate_limiter_window():
    # verify that rate_limiter key llm:{tenant}:tokens respects tenant max
    from procurement_platform.security.rate_limiter import get_rate_limiter, reset_rate_limiter

    reset_rate_limiter()
    os.environ["PROCUREMENT_TENANT_LLM_CONFIG"] = '{"tenant_demo": {"models": ["fake"], "max_tokens": 2}}'
    reset_settings_cache()
    rl = get_rate_limiter()
    # with max_tokens 2, we should be able to hit twice then blocked (window 60s)
    rl.hit("llm:tenant_demo:tokens")
    rl.hit("llm:tenant_demo:tokens")
    allowed, _ = rl.check("llm:tenant_demo:tokens")
    assert not allowed
    reset_rate_limiter()


def test_settings_get_tenant_config_defaults():
    os.environ.pop("PROCUREMENT_TENANT_LLM_CONFIG", None)
    reset_settings_cache()
    cfg = get_settings().get_tenant_llm_config("tenant_demo")
    assert "models" in cfg
    assert "max_tokens" in cfg
    assert cfg["max_tokens"] == get_settings().max_tokens_per_execution
    # is_model_allowed default allows fake
    assert get_settings().is_model_allowed_for_tenant("tenant_demo", "fake")
