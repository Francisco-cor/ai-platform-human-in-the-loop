# ADR 0004 — Dominio determinista Fase 2

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 2

## Contexto

Fase 2 exige que los cálculos empresariales críticos no dependan del LLM y que los mismos fixtures produzcan el mismo resultado (§19).

## Decisión

Implementar dominio determinista puro en `src/procurement_platform/domain/`:

- `inventory.py` — `InventorySnapshot`, `DemandForecast`, `OpenPurchaseOrder`, `ShortageResult`, `calculate_shortage_for_item()`, `calculate_shortages()`, `detect_duplicate_open_order()`, `load_context_from_fixtures()`. Soporta unidades (`piece`, `box`, `kg`, `g`, `liter`, `ml`) con tabla de conversión y moneda con redondeo a 2 decimales. Regla: `shortage = max(0, demand_total - total_available)` donde `total_available = (on_hand - reserved) + snapshot.in_transit + Σ open_orders (arrival ≤ horizon)`. Si no hay forecast, shortage basado en `requested - total_available`. Cada resultado incluye `missing_data` y `assumptions`.

- `suppliers.py` — `Supplier`, `SupplierQuote`, `SupplierCatalog.search()` (filtra por activo, tenant, location, moneda, min/max) y `best_quote()` determinista (orden por precio + lead_time, recargo hash por supplier_id). `build_proposal_lines_from_shortages()` genera líneas con `qty = max(shortage, requested)` y `missing_data` si no hay supplier.

- `policies/engine.py` — `PolicyConfig` (budget_limits, allowed_currencies/units, allowlists), checks puros: `quantity_non_negative`, `unit_compatibility`, `currency_valid`, `supplier_active`, `supplier_allowlist`, `budget_limit`, `quantity_min_max_per_supplier`, `duplicate_order`, `price_validity`. `run_policy_checks()` agrega y `has_blocking_failure()` / `requires_human_approval()` determina escalamiento.

Persistencia: nuevas tablas `inventory_items`, `demand_forecasts`, `suppliers`, `purchase_orders`, `purchase_order_lines` (migration `002_inventory_domain`). `Base.metadata.create_all` crea para tests SQLite.

Integración: `workflows/orchestrator.py` ahora inyecta `InventoryContext`, `SupplierCatalog`, `PolicyConfig` (fixtures por defecto en `evals/fixtures/`). `_build_deterministic_proposal()` reemplaza stub sintético Fase 1 con fallback. `advance_synthetic()` ahora produce propuestas deterministas con `evidence` trazable y `scope_hash`.

## Consecuencias

- Cálculo de faltantes 100% determinista y testeable sin DB/LLM: 45 tests (51 con Fase 1) pasan, incluyendo propiedad `same fixtures → same qty/total`.
- Soporte unidades/moneda validado, duplicados detectados con tolerancia 5% y órdenes en tránsito filtradas por horizonte.
- Policy engine bloquea presupuesto excedido y valida moneda/unidad/proveedor; ningún cálculo crítico llama al modelo.
- NEXT: Fase 3 RAG seguro, Fase 4 graph con Gemini adapter usará mismo `PolicyConfig` y `SupplierCatalog`.

## Alternativas descartadas

- Usar ORM directamente en dominio: se mantiene dominio puro y persistencia separada para testabilidad.
- Conversión de moneda con FX dinámico: se usa tabla fija para determinismo Fase 2.
