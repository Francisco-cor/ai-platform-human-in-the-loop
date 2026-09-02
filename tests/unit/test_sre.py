"""Fase 10 — SRE runbooks, SLO, alerts tests."""

import pathlib


def test_slo_exists():
    p = pathlib.Path("docs/operations/SLO.md")
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "99.9" in content
    assert "p95" in content
    assert "burn" in content.lower()


def test_runbooks_exist():
    expected = [
        "approval-stuck.md",
        "pgvector-slow.md",
        "redis-down.md",
        "llm-timeout.md",
        "db-failover.md",
        "budget-exceeded.md",
        "trace-not-found.md",
    ]
    for fname in expected:
        p = pathlib.Path(f"docs/operations/runbooks/{fname}")
        assert p.exists(), f"Missing runbook {fname}"
        content = p.read_text(encoding="utf-8")
        assert len(content) > 100
        assert "Diagn" in content or "diagn" in content.lower()


def test_alerts_yaml():
    p = pathlib.Path("observability/alerts/alerts.yaml")
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "Http5xxHigh" in content
    assert "P95LatencyHigh" in content
    assert "ApprovalBacklogHigh" in content


def test_pgbouncer_config():
    p = pathlib.Path("infra/db/pgbouncer.ini")
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "pool_mode" in content
    assert "max_client_conn" in content


def test_migrate_job_yaml():
    p = pathlib.Path("infra/db/migrate_job.yaml")
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "kind: Job" in content
    assert "alembic" in content and "upgrade" in content and "head" in content
    assert "PGSSLCERT" in content
    assert "Workload Identity" in content or "serviceAccountName" in content


def test_workload_identity_no_key_file():
    # infra/terraform/modules/iam must use workloadIdentityUser, not key file
    content = pathlib.Path("infra/terraform/modules/iam/main.tf").read_text(encoding="utf-8")
    assert "google_service_account" in content
    assert "workloadIdentityUser" in content
    # Ensure no google_service_account_key resource (key file)
    assert "google_service_account_key" not in content


def test_secrets_rotation_audit():
    from procurement_platform.security.secrets_rotation import (
        is_workload_identity_enabled,
        emit_secret_rotation_audit,
    )
    from procurement_platform.persistence.database import get_sessionmaker

    # workload identity should be true in local (no key file)
    assert is_workload_identity_enabled() is True

    # emit audit
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        result = emit_secret_rotation_audit(
            db, secret_id="test_secret", actor_id="test", trace_id="trace-sre"
        )
        assert result["secret_id"] == "test_secret"
        assert result["workload_identity"] is True
        # check audit event created
        from procurement_platform.persistence.models import AuditEventRow

        rows = db.query(AuditEventRow).filter(AuditEventRow.event_type == "secret.rotation").all()
        assert len(rows) >= 1
        assert any("secret_id" in str(r.details) for r in rows)
    finally:
        db.close()


def test_backup_scripts():
    for p in ["infra/backup/backup.sh", "infra/deploy/cloud_run_deploy.sh"]:
        path = pathlib.Path(p)
        assert path.exists(), f"Missing {p}"
        content = path.read_text(encoding="utf-8")
        assert "gcloud" in content
        assert "canary" in content.lower() or "backup" in content.lower()
    # ensure deploy script has traffic splitting
    deploy = pathlib.Path("infra/deploy/cloud_run_deploy.sh").read_text(encoding="utf-8")
    assert "traffic" in deploy.lower()
    assert "90" in deploy and "10" in deploy


def test_restore_drill_doc():
    p = pathlib.Path("docs/operations/restore_drill.md")
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "restore" in content.lower()
    assert "backup" in content.lower()
    assert "make backup-drill" in content or "backup.sh restore-drill" in content
