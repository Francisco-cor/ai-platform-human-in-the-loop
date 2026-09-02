---
name: Bug report
about: Crear reporte para ayudar a mejorar la plataforma
title: "bug: "
labels: bug
assignees: ''
---

**Describe el bug**
Aclarar qué esperabas vs qué ocurrió, con `execution_id` y `trace_id` si aplica.

**Reproducir**
Pasos:
1. `POST /v1/...` payload
2. `GET /v1/procurement/executions/{id}/events`
3. Ver error

**Entorno**
- Commit: `git rev-parse HEAD`
- `PROCUREMENT_APP_ENV`:
- `docker compose` o `pytest`?

**Logs**
```
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```
