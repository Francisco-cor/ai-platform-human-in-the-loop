"""
Procurement domain — Fase 11.

Re-exports procurement-specific modules that depend on platform core.
Platform is generic; this domain is procurement-specific.

Old imports keep working:
  from procurement_platform.domain.inventory import calculate_shortages
  from procurement_platform.domains.procurement.inventory import calculate_shortages # new

Both point to same implementation (re-export).
"""

from procurement_platform.domain import models as models
from procurement_platform.domain.inventory import (
    InventoryContext,
    calculate_shortages,
    calculate_shortage_for_item,
    load_context_from_fixtures,
)
from procurement_platform.domain.suppliers import (
    SupplierCatalog,
    build_proposal_lines_from_shortages,
    load_catalog_from_fixtures,
)
from procurement_platform.policies.engine import PolicyConfig, run_policy_checks

__all__ = [
    "InventoryContext",
    "calculate_shortages",
    "calculate_shortage_for_item",
    "load_context_from_fixtures",
    "SupplierCatalog",
    "build_proposal_lines_from_shortages",
    "load_catalog_from_fixtures",
    "PolicyConfig",
    "run_policy_checks",
    "models",
]
