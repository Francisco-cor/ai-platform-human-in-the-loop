# Runbook — Redis down / degraded (F10)

**Alerta:** `RedisDown` o `readyz` returns `degraded` + `redis: degraded`, locks/rate limit fall back to memory.

## Diagnóstico
1. `curl /readyz` → `checks.redis` debe ser `ok`, si `degraded` → Redis no responde.
2. `curl /metrics | grep redis` → `redis_errors_total`.
3. `docker compose logs redis` o `gcloud redis instances describe procurement-redis --region us-central1`.
4. `GET /v1/approvals` sin tenant → si rate_limiter usa fallback dict (memory) funciona pero no distribuido.

## Causas
- Memorystore mantenimiento, network partition, `redis_url` mal configurado.
- `PROCUREMENT_REDIS_URL` vacío en staging → fallback `fakeredis` memory.

## Acciones
- **Local:** `docker compose restart redis` + `docker compose logs redis`.
- **Staging:** `gcloud redis instances describe procurement-redis` → `STATE_READY`; si no, `gcloud redis instances failover` (si HA) o recreate via `terraform apply -target=module.redis`.
- **App:** locks caen a `MemoryLockManager` (ver `infra/locks/manager.py`). Verificar `infra/locks/manager.py:40` log `redis unavailable, fallback to memory`.
- **Verificación:** `pytest tests/unit/test_locks_abstraction.py -k redis` con `fakeredis`.
- **Prevención:** dashboard `observability/dashboards/procurement.json` panel `Redis hit rate`; alerta `RedisErrors >0.1/s`.

**RTO:** 2m (fallback memory permite seguir aprobando, solo rate limit no distribuido). **Peligro:** con fallback memory, `duplicate` risk aumenta en multi-replica; canary debe pausarse hasta Redis `ok`.
