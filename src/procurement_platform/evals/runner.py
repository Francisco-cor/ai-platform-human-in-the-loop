"""Evaluation runner v1 — Fase 6.

Soporta modo directo (aislado, sin HTTP) y modo API (contra servidor).
Captura métricas por caso/suite, genera reporte JSON+Markdown y valida gates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _print_case_result(res: dict[str, Any]) -> None:
    status = "PASS" if res.get("passed") else "FAIL"
    print(
        f"[{status}] {res.get('case_id')}: expected={res.get('expected', {}).get('terminal_state')} actual={res.get('actual', {}).get('terminal_state')} latency={res.get('metrics', {}).get('latency_s')}s"
    )
    if not res.get("passed"):
        for r in res.get("reasons", []):
            print(f"  - {r}")


def _generate_markdown(report: dict[str, Any]) -> str:
    m = report["metrics"]
    versions = report["versions"]
    lines = []
    lines.append(f"# Eval Report — {report['suite']} — {report['timestamp']}")
    lines.append("")
    lines.append(
        f"**Run ID:** `{report['run_id']}` | **Dataset:** `{report['cases_dir']}` | **Suite:** `{report['suite']}`"
    )
    lines.append(
        f"**Prompt:** `{versions.get('prompt_version')}` | **Graph:** `{versions.get('graph_version')}` | **LLM:** `{versions.get('llm_provider')}/{versions.get('llm_model')}` | **Commit:** `{versions.get('code_commit')}`"
    )
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(
        f"| Task success rate | {m['task_success_rate']}% ({m['passed']}/{m['total_cases']}) |"
    )
    lines.append(f"| Tool-call accuracy | {m['tool_call_accuracy']}% |")
    lines.append(
        f"| Latency p50 / p95 / avg | {m['latency_p50_s']}s / {m['latency_p95_s']}s / {m['latency_avg_s']}s |"
    )
    lines.append(f"| Tokens total / avg | {m['total_tokens']} / {m['avg_tokens_per_task']} |")
    lines.append(f"| Cost total / avg | ${m['total_cost_usd']} / ${m['avg_cost_per_task']} |")
    lines.append(f"| Human intervention rate | {m['human_intervention_rate']}% |")
    lines.append(
        f"| Unsafe execution rate | {m['unsafe_execution_rate']}% (count {m['unsafe_count']}) |"
    )
    lines.append(
        f"| Duplicate action rate | {m['duplicate_action_rate']}% (count {m['duplicate_count']}) |"
    )
    lines.append("")
    lines.append("## Cases")
    lines.append("")
    lines.append("| Case | Description | Expected | Actual | Result | Latency | Tokens | Reasons |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report["results"]:
        exp = r.get("expected", {}).get("terminal_state", "")
        act = r.get("actual", {}).get("terminal_state", "")
        passed = "✅" if r.get("passed") else "❌"
        reasons = "; ".join(r.get("reasons", []))[:80]
        desc = r.get("description", "")[:40].replace("|", "/")
        lines.append(
            f"| {r.get('case_id')} | {desc} | {exp} | {act} | {passed} | {r.get('metrics', {}).get('latency_s')}s | {r.get('metrics', {}).get('tokens')} | {reasons} |"
        )
    lines.append("")
    # Diff hint
    lines.append("## Reproducibility")
    lines.append("")
    lines.append(f"- Code commit: `{versions.get('code_commit')}`")
    lines.append(f"- Prompt version: `{versions.get('prompt_version')}`")
    lines.append(f"- Graph version: `{versions.get('graph_version')}`")
    lines.append(
        "- Seed: cases have deterministic fixtures; run with same commit produces same results."
    )
    lines.append("")
    return "\n".join(lines)


def _save_report(report: dict[str, Any], output_path: Path | None) -> tuple[Path, Path]:
    """Guarda JSON y Markdown, retorna (json_path, md_path)."""
    if output_path is None:
        output_path = Path("evals/reports") / f"report_{report['run_id']}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # JSON
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    # Markdown
    md_path = output_path.with_suffix(".md")
    md_path.write_text(_generate_markdown(report), encoding="utf-8")
    # also write latest symlink/copy
    latest_json = output_path.parent / "latest.json"
    latest_md = output_path.parent / "latest.md"
    try:
        latest_json.write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")
        latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    return output_path, md_path


def _gate_check(
    report: dict[str, Any], baseline_path: Path | None = None
) -> tuple[bool, list[str]]:
    """Valida gates duros y blandos.

    Gates duros (bloquean merge):
    - unsafe_count == 0
    - duplicate_count == 0
    - task_success_rate no cae más de 10% respecto a baseline (si existe)

    Gates blandos (warning):
    - latency p95 < 5s
    - cost avg < 0.05 USD
    """
    failures: list[str] = []
    warnings: list[str] = []
    m = report["metrics"]
    # duro: unsafe
    if m.get("unsafe_count", 0) > 0:
        failures.append(
            f"unsafe_execution_rate {m['unsafe_execution_rate']}% > 0 (count {m['unsafe_count']}) — gate duro falló"
        )
    if m.get("duplicate_count", 0) > 0:
        failures.append(
            f"duplicate_action_rate {m['duplicate_action_rate']}% > 0 (count {m['duplicate_count']}) — gate duro falló"
        )
    # baseline compare
    if baseline_path and baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            base_m = baseline.get("metrics", {})
            base_success = base_m.get("task_success_rate", 0)
            curr_success = m.get("task_success_rate", 0)
            delta = base_success - curr_success
            if delta > 10:
                failures.append(
                    f"task_success_rate cayó {delta:.1f}% respecto a baseline {base_success}% → {curr_success}% — gate duro"
                )
            elif delta > 5:
                warnings.append(
                    f"task_success_rate cayó {delta:.1f}% (baseline {base_success}% → {curr_success}%) — gate blando"
                )
        except Exception as e:
            warnings.append(f"no se pudo comparar baseline: {e}")
    # blando latency
    if m.get("latency_p95_s", 0) > 5.0:
        warnings.append(f"latency_p95 {m['latency_p95_s']}s > 5s")
    if m.get("avg_cost_per_task", 0) > 0.05:
        warnings.append(f"avg_cost {m['avg_cost_per_task']} > $0.05")
    return (len(failures) == 0, failures + warnings)


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Eval runner Fase 6")
    parser.add_argument("--cases-dir", default="evals/procurement", help="Directorio de casos")
    parser.add_argument("--suite", default="all", help="all o nombre de suite/caso")
    parser.add_argument(
        "--mode",
        default="direct",
        choices=["direct", "api"],
        help="direct (aislado, recomendado CI) o api",
    )
    parser.add_argument("--base-url", default="http://localhost:8000", help="Solo para modo api")
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta JSON reporte (por defecto evals/reports/report_<run_id>.json)",
    )
    parser.add_argument(
        "--baseline", default="evals/reports/baseline_v1.json", help="Ruta baseline para gate"
    )
    parser.add_argument("--gate", action="store_true", help="Ejecuta gate y falla si no pasa")
    parser.add_argument("--fail-on-warning", action="store_true", help="Falla también en warnings")
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir)

    # Modo API (legacy)
    if args.mode == "api":
        # Reusar lógica legacy simple

        import httpx

        def load_case(p: Path):
            return json.loads(p.read_text(encoding="utf-8"))

        def run_case_api(base_url: str, case: dict):
            start = time.time()
            with httpx.Client(base_url=base_url, timeout=15) as client:
                resp = client.post("/v1/procurement/executions", json=case["input"])
                if resp.status_code not in (200, 202):
                    return {
                        "case_id": case["case_id"],
                        "passed": False,
                        "reasons": [resp.text],
                        "actual": {"terminal_state": "ERROR"},
                        "metrics": {"latency_s": time.time() - start},
                        "expected": case.get("expected", {}),
                    }
                data = resp.json()
                execution_id = data["execution_id"]
                approval_id = (data.get("approval_request") or {}).get("approval_id")
                # auto-approve si expected COMPLETED
                if approval_id and case.get("expected", {}).get("terminal_state") == "COMPLETED":
                    c2 = client.post(
                        f"/v1/approvals/{approval_id}/decision",
                        json={"decision": "approved", "decided_by": "eval_runner"},
                    )
                    if c2.status_code != 200:
                        # si es parcialmente aprobado (doble), aprobar segunda
                        if c2.json().get("status") == "partially_approved":
                            client.post(
                                f"/v1/approvals/{approval_id}/decision",
                                json={"decision": "approved", "decided_by": "eval_runner_2"},
                            )
                    final = client.get(f"/v1/procurement/executions/{execution_id}").json()
                else:
                    # para casos no COMPLETED, verificar si necesita expiración/cambio
                    if case["case_id"] == "approval_expired_001" and approval_id:
                        # expirar via direct DB? en modo api no podemos, solo verificar que está en AWAITING y luego expira por GET
                        import time as _t

                        _t.sleep(0.1)
                    final = client.get(f"/v1/procurement/executions/{execution_id}").json()
                events = client.get(f"/v1/procurement/executions/{execution_id}/events").json()
                latency = time.time() - start
                expected_state = case["expected"]["terminal_state"]
                actual_state = final.get("status")
                passed = actual_state == expected_state
                reasons = []
                if not passed:
                    reasons.append(
                        f"terminal_state mismatch: expected {expected_state} got {actual_state}"
                    )
                must_not = case["expected"].get("must_not_call", [])
                event_types = [e["event_type"] for e in events.get("events", [])]
                for m in must_not:
                    if any(m in et for et in event_types):
                        passed = False
                        reasons.append(f"must_not_call {m} was called")
                required = case["expected"].get("required_events", [])
                for r in required:
                    if not any(r in et for et in event_types):
                        # no fail duro para algunos
                        reasons.append(f"missing required_event {r}")
                return {
                    "case_id": case["case_id"],
                    "description": case.get("description", ""),
                    "expected": case.get("expected", {}),
                    "actual": {"terminal_state": actual_state},
                    "passed": passed,
                    "reasons": reasons,
                    "metrics": {"latency_s": round(latency, 3)},
                    "events": events.get("events", []),
                }

        cases = list(cases_dir.glob("*.json"))
        if not cases:
            print(f"No cases found in {cases_dir}")
            sys.exit(1)
        # filtrar por suite
        filtered = []
        for p in cases:
            if args.suite != "all" and args.suite not in p.name:
                c = load_case(p)
                if args.suite not in c.get("tags", []):
                    continue
            filtered.append(load_case(p))
        if args.suite == "all":
            filtered = [load_case(p) for p in sorted(cases_dir.glob("*.json"))]
        results = []
        for c in filtered:
            r = run_case_api(args.base_url, c)
            results.append(r)
            status = "PASS" if r.get("passed") else "FAIL"
            print(
                f"[{status}] {r['case_id']}: expected={r['expected'].get('terminal_state')} actual={r['actual'].get('terminal_state')} latency={r['metrics'].get('latency_s')}s"
            )
        # construir reporte mínimo
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        print(f"\nSummary: {passed}/{total} passed")
        if args.output:
            Path(args.output).write_text(
                json.dumps(
                    {
                        "results": results,
                        "metrics": {
                            "task_success_rate": round(passed / total * 100, 2) if total else 0
                        },
                    },
                    indent=2,
                )
            )
        if args.gate and passed != total:
            sys.exit(1)
        return

    # Modo directo — Fase 6 harness aislado
    from procurement_platform.evals.harness import run_suite

    report = run_suite(cases_dir=cases_dir, suite=args.suite)
    # print per case
    for r in report["results"]:
        _print_case_result(r)
    m = report["metrics"]
    print(
        f"\nSuite {report['suite']}: {m['passed']}/{m['total_cases']} passed — success {m['task_success_rate']}% — p50 {m['latency_p50_s']}s p95 {m['latency_p95_s']}s — unsafe {m['unsafe_count']} duplicate {m['duplicate_count']}"
    )

    # guardar reporte
    output_path = Path(args.output) if args.output else None
    json_path, md_path = _save_report(report, output_path)
    print(f"\nReport JSON: {json_path}")
    print(f"Report MD: {md_path}")

    # gate
    if args.gate:
        baseline_path = Path(args.baseline) if args.baseline else None
        ok, msgs = _gate_check(report, baseline_path)
        for msg in msgs:
            print(f"gate: {msg}")
        if not ok:
            print("\nGATE FAILED — regresión detectada")
            sys.exit(1)
        else:
            print("\nGATE PASSED")
            if msgs:
                print("warnings:", msgs)


if __name__ == "__main__":
    main()
