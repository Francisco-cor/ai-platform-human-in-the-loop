# SLOs — Procurement Platform (Fase 10)

**Fecha:** 2026-09-02
**Fase:** 10 Cloud Native, GitOps y SRE

## Objetivos

| SLO | Objetivo | Ventana | Burn rate alerta |
|-----|----------|---------|------------------|
| **Availability** | 99.9% requests 2xx/3xx (5xx <0.1%) | 28d rolling | `burn >5` en 5m → critical |
| **Latency** | p95 <1s, p50 <500ms | 5m | `p95>2s` 3m → warning |
| **Eval success** | task_success >95% | por suite | `<90%` vs baseline → gate fail |
| **Approval backlog** | pending <50 | 5m | `>50` 5m → warning |
| **Unsafe executions** | 0% | por eval | `>0` → gate hard fail |
| **Duplicate rate** | 0% | por suite | `>0` → gate hard fail |

## Métricas fuente

- **http**: `http_requests_total{status}`, `http_request_duration_seconds` (OTel + Prometheus `observability/metrics.py`)
- **eval**: `evals/reports/baseline_v2.json` + CI `python -m procurement_platform.evals.runner --gate`
- **cost**: `llm_tokens_total`, `llm_cost_usd_total` (FinOps F5)
- **approval**: `approval_pending_total`, `approval_age_seconds` (APIs F7)

## Error budget

- 99.9% en 28d ≈ 43m downtime permitido.
- Burn rate = `error_rate / 0.001`. `error_rate 0.01` → burn `10` (consume 10x más budget).
- Alert `Http5xxHigh` → PagerDuty mock (F10).

## Ventanas y alertas (Alertmanager)

- Ver `observability/alerts/alerts.yaml`: `Http5xxHigh 2m critical`, `P95LatencyHigh 3m warning`, `ApprovalBacklogHigh 5m warning`, `BudgetExceededHigh 2m warning`.

## Runbooks

- `docs/operations/runbooks/approval-stuck.md` — backlog >50
- `docs/operations/runbooks/pgvector-slow.md` — p95 RAG >200ms
- `docs/operations/runbooks/redis-down.md` — locks/rate limit degraded
- `docs/operations/runbooks/llm-timeout.md` — LLM fallback + budget
- `docs/operations/runbooks/trace-not-found.md` — trace_id not in Grafana/Loki
- `docs/operations/runbooks/db-failover.md` — Cloud SQL failover

## Recupero (RTO/RPO)

- **RTO** 5m (rollback canary → stable via `gcloud run services update-traffic ... --to-latest`)
- **RPO** <1m (Cloud SQL point-in-time recovery 7d, bucket versioning)

## Verificación

- `make slo` → `curl /slo` `{burn_rate, error_rate, approval_backlog, p95_latency_s, status}`
- `make health` → `curl /readyz` debe ser `ready` incluso con redis degraded (fallback memory locks)
- `pytest tests/chaos -m chaos` (toxiproxy) valida DB failover sin duplicate
