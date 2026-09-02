"""Fase 8 — OpenAPI strict lint + breaking change."""

import json
import pathlib
import subprocess
import sys


def _generate_via_subprocess():
    # Use tools/openapi_lint.py as module to avoid import path issues
    result = subprocess.run([sys.executable, "tools/openapi_lint.py", "--generate"], capture_output=True, text=True)
    # Also directly generate via FastAPI for lint
    from procurement_platform.api.main import app

    return app.openapi()


def test_openapi_lint_pass():
    from procurement_platform.api.main import app

    openapi = app.openapi()
    # Import lint function via file load to avoid package issue
    import importlib.util

    spec = importlib.util.spec_from_file_location("openapi_lint", "tools/openapi_lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    errors = mod.lint_openapi(openapi)
    assert errors == [], f"lint errors: {errors}"


def test_openapi_has_v1_paths():
    from procurement_platform.api.main import app

    openapi = app.openapi()
    paths = openapi.get("paths", {})
    # at least these v1 paths must exist
    required = [
        "/v1/procurement/executions",
        "/v1/procurement/executions/{execution_id}",
        "/v1/procurement/executions/{execution_id}/events",
        "/v1/approvals/{approval_id}",
        "/v1/approvals/{approval_id}/decision",
        "/v1/webhooks/subscriptions",
        "/v1/approvals/bulk/decision",
        "/v1/approvals/export",
    ]
    for p in required:
        assert p in paths, f"missing path {p}"


def test_openapi_no_breaking_without_bump():
    from procurement_platform.api.main import app
    import importlib.util

    spec = importlib.util.spec_from_file_location("openapi_lint", "tools/openapi_lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    openapi = app.openapi()
    old = json.loads(json.dumps(openapi))  # deep copy
    removed_path = "/v1/webhooks/subscriptions"
    if removed_path in old["paths"]:
        del old["paths"][removed_path]
        breaking = mod.detect_breaking_changes(old, openapi)
        breaking2 = mod.detect_breaking_changes(openapi, old)
        assert any("removed path" in b for b in breaking2)


def test_openapi_file_exists_and_valid():
    path = pathlib.Path("docs/api/openapi.json")
    assert path.exists(), "openapi.json not generated — run python tools/openapi_lint.py --generate"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "openapi" in data
    assert "paths" in data
    assert data["info"]["version"] == "0.1.0"
