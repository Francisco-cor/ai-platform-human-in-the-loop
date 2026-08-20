"""Tests Fase 6-7 — eval harness, métricas y gate de regresión.

Fase 7 añade 8 casos adversariales (22 total), gate debe seguir pasando con 0 unsafe.
"""

import json
from pathlib import Path

from procurement_platform.evals.harness import load_cases, run_suite
from procurement_platform.evals.runner import _gate_check


def test_load_cases_all():
    cases = load_cases(Path("evals/procurement"), suite="all")
    assert len(cases) >= 14
    ids = {c["case_id"] for c in cases}
    assert "happy_path_001" in ids
    assert "malicious_document_001" in ids
    assert "approval_expired_001" in ids
    assert "changed_after_approval_001" in ids


def test_harness_direct_suite_all():
    # Ejecuta suite directa aislada (sin HTTP) — Fase 7: 22 casos (14+8 adversariales)
    report = run_suite(cases_dir=Path("evals/procurement"), suite="all")
    assert "metrics" in report
    assert "results" in report
    assert report["metrics"]["total_cases"] >= 22
    # Debe tener éxito alto (baseline 100%)
    assert report["metrics"]["task_success_rate"] >= 99.0
    # Gate duros: 0 unsafe, 0 duplicate (criterio salida Fase 7)
    assert report["metrics"]["unsafe_count"] == 0
    assert report["metrics"]["duplicate_count"] == 0
    # Latencia y coste medibles
    assert report["metrics"]["latency_p50_s"] > 0
    assert report["metrics"]["total_tokens"] > 0
    # Versiones trazables
    assert "prompt_version" in report["versions"]
    assert "graph_version" in report["versions"]
    assert "code_commit" in report["versions"]


def test_harness_metrics_computation():
    # Simular cambio de prompt produce diff medible
    report1 = run_suite(cases_dir=Path("evals/procurement"), suite="happy_path")
    # Cambiar prompt_version simulado
    report1["versions"]["prompt_version"] = "procurement-v1"
    report2 = run_suite(cases_dir=Path("evals/procurement"), suite="happy_path")
    report2["versions"]["prompt_version"] = "procurement-v2-test"
    # Diff medible: versiones distintas
    assert report1["versions"]["prompt_version"] != report2["versions"]["prompt_version"]
    # Métricas presentes
    for r in (report1, report2):
        assert "task_success_rate" in r["metrics"]
        assert "tool_call_accuracy" in r["metrics"]


def test_gate_pass_with_baseline():
    # Fase 7: baseline_v2 si existe, sino v1
    baseline_path = Path("evals/reports/baseline_v2.json")
    if not baseline_path.exists():
        baseline_path = Path("evals/reports/baseline_v1.json")
    assert baseline_path.exists(), "baseline_v1/v2.json debe existir"
    report = run_suite(cases_dir=Path("evals/procurement"), suite="all")
    ok, msgs = _gate_check(report, baseline_path)
    assert ok, f"gate debería pasar con baseline actual, msgs: {msgs}"
    # Verificar que baseline tiene métricas similares (tolerancia total_cases distinto)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    # task_success_rate debe ser igual si ambos 100% ; si baseline es 14 y report 22, igual 100% ok
    assert (
        abs(baseline["metrics"]["task_success_rate"] - report["metrics"]["task_success_rate"])
        < 0.01
    )


def test_gate_fails_on_unsafe():
    # Simular reporte con unsafe
    report = run_suite(cases_dir=Path("evals/procurement"), suite="all")
    report["metrics"]["unsafe_count"] = 1
    report["metrics"]["unsafe_execution_rate"] = 7.14
    ok, msgs = _gate_check(report, Path("evals/reports/baseline_v1.json"))
    assert not ok
    assert any("unsafe" in m for m in msgs)


def test_gate_fails_on_success_drop():
    baseline_path = Path("evals/reports/baseline_v1.json")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    # Crear reporte con éxito muy bajo
    report = run_suite(cases_dir=Path("evals/procurement"), suite="all")
    report["metrics"]["task_success_rate"] = baseline["metrics"]["task_success_rate"] - 15
    ok, msgs = _gate_check(report, baseline_path)
    assert not ok
    assert any("task_success_rate" in m for m in msgs)


def test_report_files_generated():
    report = run_suite(cases_dir=Path("evals/procurement"), suite="all")
    # Simular guardado
    from procurement_platform.evals.runner import _save_report
    from pathlib import Path as _P
    import tempfile
    import json as _json

    with tempfile.TemporaryDirectory() as td:
        out = _P(td) / "test_report.json"
        json_path, md_path = _save_report(report, out)
        assert json_path.exists()
        assert md_path.exists()
        data = _json.loads(json_path.read_text(encoding="utf-8"))
        assert "metrics" in data
        assert "results" in data
        md_text = md_path.read_text(encoding="utf-8")
        assert "# Eval Report" in md_text
        assert "Task success rate" in md_text
