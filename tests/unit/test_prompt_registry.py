"""Fase 6 — Prompt registry file-based con hash."""

import hashlib
import pathlib

import pytest

from procurement_platform.agents.prompts import (
    get_prompt,
    get_prompt_hash,
    get_prompt_metadata,
    list_prompt_versions,
    reset_prompt_cache,
)


def test_prompt_registry_hash_v1():
    reset_prompt_cache()
    h = get_prompt_hash("procurement-v1")
    assert h.startswith("sha256:")
    assert len(h) == 7 + 64  # sha256: + 64 hex
    # verify file hash matches loader hash
    p = pathlib.Path("prompts/registry/procurement-v1.yaml")
    assert p.exists()
    expected = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    assert h == expected


def test_prompt_registry_hash_v2():
    reset_prompt_cache()
    h = get_prompt_hash("procurement-v2")
    assert h.startswith("sha256:")
    p = pathlib.Path("prompts/registry/procurement-v2.yaml")
    assert p.exists()
    expected = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    assert h == expected
    # v1 and v2 hashes must differ
    assert get_prompt_hash("procurement-v1") != get_prompt_hash("procurement-v2")


def test_prompt_registry_get_prompt_and_metadata():
    reset_prompt_cache()
    sys_prompt = get_prompt("procurement-v1", "system")
    assert "PROPONER, no decidir" in sys_prompt or "PROPONER" in sys_prompt
    # with hash validation
    h = get_prompt_hash("procurement-v1")
    sys2 = get_prompt("procurement-v1", "system", expected_hash=h)
    assert sys2 == sys_prompt
    # wrong hash should raise
    with pytest.raises(ValueError, match="prompt_hash mismatch"):
        get_prompt("procurement-v1", "system", expected_hash="sha256:bad")
    # metadata
    meta = get_prompt_metadata("procurement-v1")
    assert meta["prompt_version"] == "procurement-v1"
    assert meta["prompt_hash"] == h
    assert "system" in meta["keys"]
    # list versions includes both
    versions = list_prompt_versions()
    assert "procurement-v1" in versions
    assert "procurement-v2" in versions


def test_prompt_registry_audit_trace_contains_hash(client):
    # via API: create execution and check audit trace has prompt_hash
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        },
    )
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # get trace format events
    resp2 = client.get(f"/v1/procurement/executions/{exec_id}/events", params={"format": "trace"})
    assert resp2.status_code == 200
    timeline = resp2.json().get("timeline", [])
    assert len(timeline) > 0
    # at least one event should have model_metadata with prompt_hash
    found = False
    for ev in timeline:
        mm = ev.get("model_metadata") or {}
        if "prompt_hash" in mm:
            assert mm["prompt_hash"].startswith("sha256:")
            found = True
            break
    assert found, "no prompt_hash in audit trace"
