"""Secrets rotation helper — Fase 10 SRE + workload identity.

Cloud Run no usa key file; usa Workload Identity Federation (WIF) via google-github-actions/auth
y google_service_account IAM workloadIdentityUser.

Secret Manager rotation 30d está en terraform/modules/secrets rotation_period = 2592000s.

Este helper emite audit secret.rotation y permite rotación manual via API (admin).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.domain.models import new_id


def is_workload_identity_enabled() -> bool:
    """Verifica que no se usa key file (GOOGLE_APPLICATION_CREDENTIALS no apunta a json con key)."""
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        return True  # WIF via metadata server
    # Si creds apunta a file, verificar si es WIF (external_account) vs service_account
    try:
        import json
        import pathlib

        p = pathlib.Path(creds)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            # WIF config has type=external_account
            if data.get("type") == "external_account":
                return True
            # service_account key file is deprecated for Run (should be WIF)
            return False
    except Exception:
        return True
    return True


def emit_secret_rotation_audit(
    db: Session,
    secret_id: str,
    actor_id: str = "system",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Emite audit event secret.rotation (usado por terraform rotation + manual API)."""
    from procurement_platform.audit.service import create_audit_event

    event = create_audit_event(
        db,
        execution_id=f"secret_{secret_id}",
        request_id=new_id("req"),
        event_type="secret.rotation",
        actor_type="system",
        actor_id=actor_id,
        trace_id=trace_id,
        details={
            "secret_id": secret_id,
            "rotation_period_days": 30,
            "workload_identity": is_workload_identity_enabled(),
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    db.commit()
    return {
        "secret_id": secret_id,
        "event_id": event.event_id,
        "workload_identity": is_workload_identity_enabled(),
    }
