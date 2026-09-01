# Runbook — Trace no encontrado (F5-3)

**Síntoma:** `GET /v1/procurement/executions/{id}/events?format=trace` retorna `trace_id=null` o Grafana `trace_id` sin spans.

## Diagnóstico <5 min desde execution_id

1. **Ver execution:** `curl /v1/procurement/executions/{id}` → anota `trace_id` y `status`.
2. **Events timeline:** `curl /v1/procurement/executions/{id}/events?format=trace` → verifica `timeline[].span_id` y `model_metadata {prompt_version, graph_version}`.
3. **Logs:** `kubectl logs -l app=procurement --since=10m | grep trace_id` o local `grep trace_id` en JSON logs (`observability/logging.py` correlaciona `trace_id/span_id`).
4. **Metrics:** `curl /metrics | grep http_request_duration_seconds` → verifica `trace` no es `none`.
5. **OTEL exporter:** `echo $PROCUREMENT_OTEL_EXPORTER` → si `none`, trazas solo en memoria y logs; si `otlp`, verifica `OTEL_EXPORTER_OTLP_ENDPOINT` y `docker logs otel-collector`.

## Causas comunes

- `PROCUREMENT_OTEL_EXPORTER=none` (default local): no hay spans OTel, solo `uuid.hex` fallback → `span_id` sintético en logs. No es error; para traces reales set `PROCUREMENT_OTEL_EXPORTER=console` o `otlp`.
- `trace_id` perdido entre `request_id → execution_id → trace_id`: middleware `api/main.py:58` propaga `traceparent`/`X-Trace-Id`; verifica que cliente envía `X-Request-Id` y no se borra en `gateway`.
- DB `audit_events.trace_id` NULL: `audit/service.py` ahora auto-correlaciona desde `tracing.get_current_span_context`; si `trace_id` NULL, reset `request_id_ctx` en `tests/conftest.py` puede haber limpiado var.

## Mitigación

- Local: `PROCUREMENT_OTEL_EXPORTER=console make run` → spans en stdout.
- Staging: `PROCUREMENT_OTEL_EXPORTER=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://otel:4317 make docker-up` + Grafana Tempo.
- Valida: `make eval && curl /v1/procurement/executions/$(jq -r .results[0].actual.execution_id evals/reports/latest.json)/events?format=trace | jq .timeline`

**SLO:** diagnóstico <5 min, ver `observability/dashboards/procurement.json` panel `p50/p95`.

