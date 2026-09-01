"""Row-level security helper — F3-2 tenant isolation at query layer.

Provides helpers to ensure every query filters by tenant_id and to assert access.
Not yet DB-native RLS (which would need PG policies), but application-level guard.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Query
from sqlalchemy.sql import Select


def apply_tenant_filter(query: Query | Select, model: Any, tenant_id: str):
    """Add tenant_id filter to query if model has tenant_id column."""
    if hasattr(model, "tenant_id"):
        return query.filter(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    return query


def assert_tenant_row_access(
    row_tenant_id: str, principal_tenant_id: str, allow_cross: bool = False
) -> None:
    """Raise 403 if principal cannot access row tenant."""
    if allow_cross:
        return
    if row_tenant_id != principal_tenant_id:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail={
                "code": "tenant_forbidden",
                "message": f"tenant {principal_tenant_id} cannot access {row_tenant_id}",
            },
        )


def tenant_scoped_query(db, model: Any, tenant_id: str):
    """Convenience: return query already filtered by tenant."""
    return db.query(model).filter(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
