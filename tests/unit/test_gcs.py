"""Fase 9 — GCS ArtifactStore tests."""

import json
import pathlib

from procurement_platform.infra.gcs import ArtifactStore, get_artifact_store, reset_artifact_store


def test_artifact_store_file(tmp_path):
    # Use file:// tmp_path
    store = ArtifactStore(bucket=f"file://{tmp_path}")
    data = json.dumps({"hello": "world"}).encode()
    key = "evals/report_test.json"
    path = store.put(key, data)
    assert pathlib.Path(path).exists()
    assert store.exists(key)
    retrieved = store.get(key)
    assert retrieved == data
    listed = store.list("evals/")
    assert any("report_test.json" in k for k in listed)
    assert store.delete(key)
    assert not store.exists(key)


def test_artifact_store_via_runner(tmp_path):
    # Test that eval runner saves to GCS (file://)
    import os
    from pathlib import Path
    from procurement_platform.evals.harness import run_suite
    from procurement_platform.infra.gcs import get_artifact_store, reset_artifact_store
    from procurement_platform.persistence.database import Base, get_engine, get_sessionmaker

    os.environ["PROCUREMENT_GCS_BUCKET"] = f"file://{tmp_path}"
    reset_artifact_store()
    # Need to reset settings cache to pick up env
    from procurement_platform.config.settings import reset_settings_cache

    reset_settings_cache()
    # run a small suite
    from sqlalchemy.orm import Session

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        # use harness directly to save report
        from procurement_platform.evals.runner import _save_report

        report = {
            "run_id": "test_gcs_123",
            "timestamp": "2026-09-02T00:00:00Z",
            "suite": "test",
            "cases_dir": "evals/procurement",
            "versions": {"prompt_version": "v1", "graph_version": "v1", "llm_provider": "fake"},
            "metrics": {"task_success_rate": 100, "passed": 1, "total_cases": 1, "tool_call_accuracy": 100, "latency_p50_s": 0.1, "latency_p95_s": 0.1, "latency_avg_s": 0.1, "total_tokens": 100, "avg_tokens_per_task": 100, "total_cost_usd": 0.001, "avg_cost_per_task": 0.001, "human_intervention_rate": 0, "unsafe_execution_rate": 0, "duplicate_action_rate": 0, "unsafe_count": 0, "duplicate_count": 0},
            "results": [],
        }
        json_path, md_path = _save_report(report, Path(tmp_path) / "report_test_gcs_123.json")
        # Check that GCS also has it under evals/
        store = get_artifact_store()
        # The _save_report should have put to GCS under evals/
        assert store.exists(f"evals/{json_path.name}")
        assert store.exists(f"evals/{md_path.name}")
    finally:
        db.close()
        os.environ.pop("PROCUREMENT_GCS_BUCKET", None)
        reset_settings_cache()
        reset_artifact_store()
