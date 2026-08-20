# ADR 0002 — Orquestador de Workflow: runtime propio vs LangGraph

**Fecha:** 2026-08-20  
**Estado:** Aceptada (Fase 1)  
**Fase:** 0 → 1

## Contexto

Plan §4 y §7 proponen LangGraph o grafo propio. Necesitamos checkpoints durables, pausa human-in-the-loop, retries, y migración futura a Cloud Run.

Criterios:
- Durabilidad de checkpoints (Postgres, no solo memoria/Redis).
- Pausa y reanudación por aprobación humana.
- Observabilidad (OpenTelemetry por nodo).
- Complejidad y lock-in.

## Decisión

**Fase 1:** Implementar **runtime propio minimal** con interfaz `WorkflowOrchestrator`.

Motivos:
- Checklist Fase 1 exige servicio que arranque sin modelo real; LangGraph añadiría dependencia pesada y requiere evaluación de embeddings, checkpoints, etc.
- Runtime propio permite implementar estados `ExecutionState` exactamente como §5, transiciones validadas, y persistencia en Postgres (`workflow_executions` + `workflow_checkpoints`).
- Interfaz abstrae ejecución para poder migrar a LangGraph en Fase 4 sin rediseñar dominio:

```python
class WorkflowOrchestrator(Protocol):
    def start(self, request: NormalizedRequest) -> Execution: ...
    def advance(self, execution_id: str) -> Execution: ...
    def transition(self, execution_id: str, target: ExecutionState, ...) -> Execution: ...
    def get_execution(self, execution_id: str) -> Execution
```

## Consecuencias

- Fase 1 entrega grafo lineal sintético: `RECEIVED → NORMALIZED → CONTEXT_LOADED → ... → COMPLETED` sin LLM.
- Fase 4 evaluará LangGraph: si aporta checkpoints superiores y pausa humana nativa, se implementará `LangGraphOrchestrator` detrás de la misma interfaz.
- Tests de transición y reanudación garantizan invariantes (§5 Reglas de transición).

## Estado futuro

Revisitar en Sesión E (Grafo y tools) con benchmark de checkpoints y human pause.
