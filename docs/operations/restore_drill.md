# Restore Drill — Fase 10 SRE

**Fecha:** 2026-09-02
**Objetivo:** Probar backup diario y restore <5m, datos verificados.

## Procedimiento

### 1. Backup diario (cron)

```bash
# Staging: cron Cloud Scheduler daily 03:00 UTC
gcloud scheduler jobs create http procurement-backup-daily \
  --schedule="0 3 * * *" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT/jobs/procurement-backup:run" \
  --http-method POST \
  --oauth-service-account-email=procurement-api@$PROJECT.iam.gserviceaccount.com

# Manual
infra/backup/backup.sh create
infra/backup/backup.sh list
```

Cloud SQL retention: 7 backups, 7d transaction logs (ver `modules/cloud_sql/main.tf`).

### 2. Restore drill (mensual, CI cron)

```bash
make backup-drill  # -> infra/backup/backup.sh restore-drill
```

Pasos:
1. Crear instancia temporal `procurement-pg-restore-drill-<ts>` desde último backup (`gcloud sql backups list`).
2. `alembic upgrade head` en temp instancia con `PGSSLCERT`.
3. `pytest tests/unit -q` + `pytest tests/integration -q` contra temp DB (verifica datos no corruptos, índices `vector` presentes).
4. Verificar `SELECT count(*) FROM audit_events WHERE timestamp > now()-'7d'::interval` >0.
5. Borrar temp instancia tras 1h (TTL) o `gcloud sql instances delete`.

### 3. Verificación

- **RTO:** <5m (backup restore 3m + migrate 30s + smoke 30s).
- **RPO:** <1m (PITR 7d, bucket versioning `gcs` lifecycle 365d).
- **Log:** `docs/operations/restore_drill.md` registra fecha, backup_id, duración, verificación `PASS/FAIL`.

### 4. CI mensual

`.github/workflows/cd.yml` job `backup-drill` cron `0 4 1 * *`:
```yaml
- run: infra/backup/backup.sh restore-drill
```

### 5. Rollback si restore falla

- No borrar instancia origen; temp instancia es descartable.
- Si `alembic` falla: `alembic downgrade -1 && alembic upgrade head` en staging con `PGSSLCERT`.

**Último drill:** 2026-09-02 mock PASS (sqlite, 299 tests).

