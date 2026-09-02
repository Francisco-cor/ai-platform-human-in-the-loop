# Scorecard — v1.0.0 (Fase 11)

| Metrica | Valor | Objetivo | Estado |
|---------|-------|----------|--------|
| **code_shared %** | 79.66% | >70% | PASS |
| platform lines | 1895 | — | — |
| domain lines | 484 | — | — |
| task_success_rate | 100.0% | >95% | PASS |
| unsafe | 0 | 0 | PASS |
| duplicate | 0 | 0 | PASS |
| p95 latency s | 0.091 | <1s | PASS |
| cost/task USD | 0.00073 | — | — |
| rag precision@5 | 1.0 | >=0.80 | PASS |
| coverage | 85.0% | >=85% | PASS |

*Generado:* `python scripts/scorecard.py` — Fase 11
*Reusabilidad:* `code_shared = platform / (platform+domain)` líneas Python
*Fuente eval:* `evals/reports/baseline_v2.json` o último `report_*.json`
