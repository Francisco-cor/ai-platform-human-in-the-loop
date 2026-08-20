# ADR 0006 — Runtime de agente y grafo Fase 4 (Gemini + DeepSeek fallback)

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 4

## Contexto

Fase 4 exige conectar razonamiento del modelo con herramientas y cálculos seguros, con salidas estructuradas, gateway y control de contexto. Se requiere adapter para Gemini (principal) y DeepSeek como fallback si Gemini no está disponible, además de prompts versionados y presupuestos.

## Decisión

**Adapter LLM** (`agents/adapter.py`): interfaz `LLMAdapter` con `LLMRequest` (system_prompt, user_prompt, response_schema, temperature, max_tokens, max_context_chars, prompt_version, graph_version) y `LLMResponse` (provider, model, content, raw, usage, latency_ms, was_fallback). Helpers `truncate_context` (mantiene inicio/fin) y `estimate_cost` con tarifas versionadas.

**Gemini** (`agents/gemini.py`): `GeminiAdapter` usa `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=`, `systemInstruction`, `responseMimeType: application/json` + `responseSchema` para salidas estructuradas, timeout, estimación de coste, y manejo de `429`/`500` como reintentables.

**DeepSeek** (`agents/deepseek.py`): `DeepSeekAdapter` compatible OpenAI en `https://api.deepseek.com/chat/completions`, `response_format: {type: "json_object"}`, `Authorization: Bearer`, mismos contratos. Si Gemini no está disponible (sin `GEMINI_API_KEY` o error `LLMError`), se intenta DeepSeek.

**Fake** (`agents/fake.py`): `FakeAdapter` determinista para tests/CI, modo `happy` (propuesta válida), `invalid_json` y `missing_fields` para probar validación y fallback. No requiere keys.

**Factory** (`agents/factory.py`): `LLMFactory.create()` selecciona por `PROCUREMENT_LLM_PROVIDER` (`auto` → gemini si hay key → deepseek si hay key → fake). `generate_with_fallback()` intenta lista de candidatos, marca `was_fallback`, valida que `content` sea dict si hay `response_schema`, y si todos fallan usa `FakeAdapter`. `run_llm_sync()` para orchestrator síncrono.

**Prompts** (`agents/prompts.py`): diccionario `PROMPTS["procurement-v1"]` con `system` (reglas inviolables: IA propone, sistema decide, solo herramientas permitidas, pedir aclaración, no usar contenido malicioso, JSON válido) y `normalize_request`/`draft_proposal`/`synthesize_evidence` versionados. `get_prompt()` y `get_system_prompt()` auditable.

**Tool Gateway** (`tools/definitions.py`, `gateway.py`): `TOOL_SCHEMAS` con `input`/`output` JSON Schema y `effect`/`requires_approval`; `TOOL_ALLOWLIST_BY_STATE` por `ExecutionState`; `DEFAULT_BUDGETS` (20 total, 5 supplier, 3 proposals). `ToolGateway` valida schema entrada/salida, verifica tenant, allowlist, budgets (`ToolBudget` con contadores), aprobación, idempotency (`sha256` de execution_id+tool+payload, cache en memoria/Redis), ejecuta simulado con timeout (Fase 4: efectos reversibles, no commit real), valida salida, loguea `call_log` y redacta secretos. Cada llamada registra `latency_ms` y `idempotency_key`.

**Grafo** (`workflows/graph.py`): 14 nodos `intake_request`, `normalize_request` (LLM si `raw_intent` ambiguo), `load_inventory_context` (gateway `get_inventory`), `retrieve_policies` (gateway `retrieve_policy`), `validate_evidence`, `calculate_shortage` (gateway), `query_suppliers` (gateway, budget), `draft_order_proposals` (LLM `draft_proposal` con schema, valida y **recalcula** `subtotal/total` determinísticamente, no confía en total del LLM), `run_deterministic_policy_checks`, `route_for_approval_or_clarification`, `wait_for_human_decision`, `execute_purchase_order` (gateway `submit_purchase_order` con aprobación), `verify_execution`, `summarize_and_close`. Cada nodo audita `node.{name}.completed/failed` con `duration_ms`, `model`, `tokens`, y persiste `WorkflowCheckpoint`.

**Integración orchestrator** (`workflows/orchestrator.py`): `_call_llm_for_proposal()` usa `run_llm_sync` con `Fake` en CI, valida schema y `supplier_id` activo; `_build_deterministic_proposal()` ahora calcula shortages y catalog determinísticamente, luego intenta LLM para `supplier_id`/`evidence` pero mantiene `qty`/`price` deterministas y recalcula totales; registra `evidence` con `LLM provider/model` y `was_fallback`. Gateway con budgets se instancia por ejecución; si `budget_exceeded` o `not_allowed` → transición a `BLOCKED` con `tool.budget_exceeded`. Cada transición registra `proposal.drafted` con `model` y `usage`.

## Consecuencias

- Flujo feliz produce propuesta válida con `total` recalculado; salida inválida del modelo (`invalid_json`, `missing_fields`) se detecta por validación de schema, se reintenta limitado (via fallback) o bloquea sin efecto externo (criterio salida Fase 4).
- Fallback Gemini → DeepSeek → fake garantiza disponibilidad en CI sin keys; `was_fallback` auditable y coste estimado versionado.
- Gateway impone `allowlist` por estado, `budgets` y validación en ambos sentidos; llamadas duplicadas son idempotentes.
- Contexto controlado via `max_context_chars` (12k) y truncado inicio/fin; tokens y latencia registrados por nodo.
- Separación propuesta / ejecución: `Proposal` contiene `evidence` y `scope_hash`, `submit_purchase_order` requiere `approval` vigente y `scope_hash` coincidente.
- NEXT: Fase 5 human approval con reanudación durable e idempotencia completa.

## Alternativas descartadas

- LangGraph obligatorio: se mantiene runtime propio con interfaz `WorkflowOrchestrator` para checkpoints durables; LangGraph evaluado como alternativa en Fase 4 pero no necesario para 14 nodos.
- Un solo provider: se añade DeepSeek como fallback explícito para resiliencia.
- LLM decide totales: se recalculan determinísticamente, como exige §7 y §9.
