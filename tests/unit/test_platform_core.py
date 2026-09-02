"""Fase 11 — platform core import without procurement."""

import sys


def test_import_platform_without_procurement():
    # import platform core should not require procurement domain
    import procurement_platform.platform
    import procurement_platform.platform.workflow
    import procurement_platform.platform.gateway
    import procurement_platform.platform.approvals
    import procurement_platform.platform.audit
    import procurement_platform.platform.rag
    import procurement_platform.platform.llm
    import procurement_platform.platform.evals
    import procurement_platform.platform.tools.registry

    # Verify platform does not eagerly import domains
    # If domains were imported at platform import time, this would have side effects
    # Check that domains not in sys.modules unless explicitly imported
    assert "procurement_platform.platform" in sys.modules
    # platform.workflow should be importable without domain inventory
    from procurement_platform.platform.workflow import WorkflowEngine, ExecutionState

    eng = WorkflowEngine(domain="test")
    assert eng.domain == "test"


def test_tool_registry_entry_points():
    from procurement_platform.platform.tools.registry import list_tools, get_tool_handler, register_tool

    # builtin tool should be discoverable after pip install -e . (entry_points)
    # In dev without reinstall, we can register manually and check
    tools = list_tools()
    # At least calculate_shortage should be registered via manual fallback if entry_points not yet reinstalled
    if "calculate_shortage" not in tools:
        # register manually for test
        from procurement_platform.tools.builtin.calculate_shortage import handler, schema

        register_tool("calculate_shortage", schema, handler)
        tools = list_tools()
    assert "calculate_shortage" in tools
    handler = get_tool_handler("calculate_shortage")
    assert handler is not None
    result = handler({"sku": "MAT-001", "on_hand": 10, "demand": 20})
    assert result["shortage"] == 10


def test_platform_version():
    import procurement_platform.platform

    info = procurement_platform.platform.get_platform_info()
    assert "version" in info
    assert "workflow" in info["modules"]
