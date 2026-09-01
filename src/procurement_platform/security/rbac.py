"""RBAC/ABAC — F3-4 roles requester/approver/admin + approval ABAC."""

from __future__ import annotations

from fastapi import Depends, HTTPException

from procurement_platform.security.auth import Principal, get_current_principal


# Role hierarchy: admin > approver > requester
ROLE_HIERARCHY = {"requester": 1, "approver": 2, "admin": 3}


def has_role(principal: Principal, required: str) -> bool:
    req_level = ROLE_HIERARCHY.get(required, 0)
    return any(ROLE_HIERARCHY.get(r, 0) >= req_level for r in principal.roles)


def require_role(required: str):
    """FastAPI dependency that raises 403 if principal lacks role."""

    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not has_role(principal, required):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden",
                    "message": f"role {required} required, has {principal.roles}",
                },
            )
        return principal

    return dependency


def require_approver_for_execution(principal: Principal, tenant_id: str) -> None:
    """ABAC: approver must be same tenant and have approver/admin."""
    if principal.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "tenant_forbidden", "message": "tenant mismatch for approver"},
        )
    if not has_role(principal, "approver"):
        raise HTTPException(
            status_code=403, detail={"code": "forbidden", "message": "approver role required"}
        )
