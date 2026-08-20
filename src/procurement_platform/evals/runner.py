"""Evaluation harness mínimo — Fase 1 (G).

Carga casos JSON, ejecuta contra la API (o orchestrator directo), compara expected.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_case_api(base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    import httpx

    start = time.time()
    with httpx.Client(base_url=base_url, timeout=10) as client:
        # create execution
        resp = client.post("/v1/procurement/executions", json=case["input"])
        if resp.status_code not in (200, 202):
            return {"status": "error", "error": resp.text, "latency_s": time.time() - start}
        data = resp.json()
        execution_id = data["execution_id"]
        approval_id = (data.get("approval_request") or {}).get("approval_id")
        # si hay approval pendiente, aprobar automáticamente para happy_path
        if approval_id and case["expected"].get("terminal_state") == "COMPLETED":
            appr_resp = client.post(
                f"/v1/approvals/{approval_id}/decision",
                json={"decision": "approved", "decided_by": "eval_runner", "reason": "auto-approve happy_path"},
            )
            if appr_resp.status_code != 200:
                return {"status": "error", "error": appr_resp.text, "execution_id": execution_id}
            # re-fetch
            final = client.get(f"/v1/procurement/executions/{execution_id}").json()
        else:
            final = client.get(f"/v1/procurement/executions/{execution_id}").json()
        # events
        events = client.get(f"/v1/procurement/executions/{execution_id}/events").json()
        latency = time.time() - start
        # compare
        expected_state = case["expected"]["terminal_state"]
        actual_state = final.get("status")
        passed = actual_state == expected_state
        required_events = case["expected"].get("required_events") or []
        event_types = [e["event_type"] for e in events.get("events", [])]
        missing_events = [e for e in required_events if not any(e in et for et in event_types)]
        if missing_events:
            passed = False
        return {
            "case_id": case["case_id"],
            "passed": passed,
            "expected_state": expected_state,
            "actual_state": actual_state,
            "missing_events": missing_events,
            "latency_s": round(latency, 3),
            "execution_id": execution_id,
            "events_count": len(event_types),
        }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Eval runner Fase 1")
    parser.add_argument("--dataset", default="procurement")
    parser.add_argument("--suite", default="happy_path")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases-dir", default="evals/procurement")
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir)
    cases = list(cases_dir.glob("*.json"))
    if not cases:
        print(f"No cases found in {cases_dir}")
        return
    print(f"Running {len(cases)} cases against {args.base_url}")
    results = []
    for p in cases:
        case = load_case(p)
        if args.suite != "all" and args.suite not in p.name:
            continue
        res = run_case_api(args.base_url, case)
        results.append(res)
        status = "PASS" if res.get("passed") else "FAIL"
        print(f"[{status}] {res.get('case_id')}: expected={res.get('expected_state')} actual={res.get('actual_state')} latency={res.get('latency_s')}s")

    passed = sum(1 for r in results if r.get("passed"))
    print(f"\nSummary: {passed}/{len(results)} passed")


if __name__ == "__main__":
    main()
