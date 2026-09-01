"""RBAC tests — F3-4."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from procurement_platform.security.rbac import has_role, require_role
from procurement_platform.security.auth import Principal


def _jwt(tenant="tenant_demo", roles=None):
    roles = roles or ["requester"]
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": "u1", "tenant_id": tenant, "roles": roles}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


def test_has_role():
    p = Principal(sub="u", tenant_id="tenant_demo", roles=["requester"], auth_method="jwt")
    assert has_role(p, "requester") is True
    assert has_role(p, "approver") is False
    p2 = Principal(sub="u", tenant_id="t", roles=["approver"], auth_method="jwt")
    assert has_role(p2, "requester") is True  # approver >= requester
    assert has_role(p2, "approver") is True
    p3 = Principal(sub="u", tenant_id="t", roles=["admin"], auth_method="jwt")
    assert has_role(p3, "approver") is True


def test_approver_required_success(client: TestClient):
    # create execution
    resp = client.post(
        "/v1/procurement/executions", json={"tenant_id": "tenant_demo", "raw_intent": "rbac test"}
    )
    assert resp.status_code == 202
    appr_id = resp.json()["approval_request"]["approval_id"]
    token = _jwt(tenant="tenant_demo", roles=["approver"])
    r = client.post(
        f"/v1/approvals/{appr_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] in ("approved", "partially_approved")


def test_requester_forbidden_on_approval(client: TestClient):
    resp = client.post(
        "/v1/procurement/executions",
        json={"tenant_id": "tenant_demo", "raw_intent": "rbac forbidden"},
    )
    appr_id = resp.json()["approval_request"]["approval_id"]
    token = _jwt(tenant="tenant_demo", roles=["requester"])
    r = client.post(
        f"/v1/approvals/{appr_id}/decision",
        json={"decision": "approved", "decided_by": "user_requester"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_tenant_mismatch_forbidden_on_approval(client: TestClient):
    resp = client.post(
        "/v1/procurement/executions",
        json={"tenant_id": "tenant_demo", "raw_intent": "tenant mismatch"},
    )
    appr_id = resp.json()["approval_request"]["approval_id"]
    token = _jwt(tenant="tenant_other", roles=["approver"])
    r = client.post(
        f"/v1/approvals/{appr_id}/decision",
        json={"decision": "approved", "decided_by": "approver_other"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "tenant_forbidden"
