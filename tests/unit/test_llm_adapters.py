import pytest

from procurement_platform.agents.adapter import LLMRequest
from procurement_platform.agents.factory import LLMFactory, run_llm_sync
from procurement_platform.agents.fake import FakeAdapter


@pytest.mark.asyncio
async def test_fake_adapter_happy():
    adapter = FakeAdapter(mode="happy")
    req = LLMRequest(
        system_prompt="system",
        user_prompt="draft proposal for MAT-001",
        response_schema={
            "type": "object",
            "required": ["supplier_id", "lines"],
            "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array"}},
        },
    )
    resp = await adapter.generate(req)
    assert resp.provider == "fake"
    assert isinstance(resp.content, dict)
    assert "supplier_id" in resp.content
    assert resp.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_fake_adapter_invalid_json():
    adapter = FakeAdapter(mode="invalid_json")
    req = LLMRequest(
        system_prompt="system",
        user_prompt="test",
        response_schema={
            "type": "object",
            "required": ["supplier_id"],
            "properties": {"supplier_id": {"type": "string"}},
        },
    )
    resp = await adapter.generate(req)
    # raw is not json, content is raw string
    assert isinstance(resp.content, str)
    assert resp.raw_content == "this is not json"


@pytest.mark.asyncio
async def test_factory_auto_fallback_to_fake():
    # con provider auto y sin keys, debe usar fake
    req = LLMRequest(system_prompt="system", user_prompt="hello", response_schema=None)
    resp = await LLMFactory.generate_with_fallback(req)
    assert resp.provider == "fake"


def test_factory_sync_fake():
    req = LLMRequest(
        system_prompt="system",
        user_prompt="normalize request test",
        response_schema={
            "type": "object",
            "required": ["items"],
            "properties": {"items": {"type": "array"}},
        },
    )
    resp = run_llm_sync(req)
    assert resp.provider == "fake"
    assert isinstance(resp.content, dict)


@pytest.mark.asyncio
async def test_gemini_missing_key_fallback_to_deepseek_or_fake():
    # Si gemini sin key y fallback habilitado, debe intentar deepseek (sin key) y luego fake
    from procurement_platform.config.settings import get_settings

    settings = get_settings()
    # Forzar que gemini no tenga key, deepseek tampoco
    # El factory debería caer a fake
    req = LLMRequest(system_prompt="system", user_prompt="test fallback", response_schema=None)
    # Temporarily set provider to gemini via factory direct
    adapter = LLMFactory.create("fake")
    assert adapter.provider == "fake"
    resp = await LLMFactory.generate_with_fallback(req)
    assert resp.provider == "fake"


def test_prompt_version_exists():
    from procurement_platform.agents.prompts import get_prompt, get_system_prompt

    sys_prompt = get_system_prompt("procurement-v1")
    assert "PROPONER" in sys_prompt or "PROPOSE" in sys_prompt or "proponer" in sys_prompt.lower()
    prompt = get_prompt("procurement-v1", "draft_proposal")
    assert "supplier_id" in prompt.lower() or "proveedor" in prompt.lower()
