"""Tenant isolation — Fase 7 §16.

Validación de acceso tenant-scoped para:
- retrieval (documentos)
- tools (inventory, suppliers)
- approvals
"""

from __future__ import annotations


def is_tenant_allowed(
    request_tenant: str, resource_tenant: str, allowed_tenants: list[str] | None
) -> bool:
    """Verifica si request_tenant puede acceder a recurso de resource_tenant.

    Reglas:
    - Si allowed_tenants es None o vacío → solo resource_tenant == request_tenant.
    - Si allowed_tenants contiene request_tenant o resource_tenant, pero resource debe ser el mismo tenant que request.
    - Cross-tenant nunca permitido salvo que resource sea shared y request esté en allowlist.
    """
    if not request_tenant or not resource_tenant:
        return False
    if request_tenant == resource_tenant:
        return True
    # cross-tenant: solo si resource permite explícitamente request_tenant
    if allowed_tenants and request_tenant in allowed_tenants:
        # pero no permitir si resource es otro tenant (aislamiento estricto para procurement)
        # En MVP, procurement es tenant-isolated: nunca cross-tenant.
        return False
    return False


def assert_tenant_access(
    request_tenant: str, resource_tenant: str, allowed_tenants: list[str] | None = None
) -> None:
    if not is_tenant_allowed(request_tenant, resource_tenant, allowed_tenants):
        from procurement_platform.tools.gateway import ToolGatewayError  # lazy to avoid cycle

        raise ToolGatewayError(
            "tenant_isolation_violation",
            f"tenant {request_tenant} no autorizado para recurso de {resource_tenant}",
            {"request_tenant": request_tenant, "resource_tenant": resource_tenant},
        )


def filter_by_tenant(
    items: list[dict], request_tenant: str, tenant_key: str = "tenant_id"
) -> list[dict]:
    """Filtra lista de dicts por tenant."""
    return [it for it in items if it.get(tenant_key) == request_tenant]
