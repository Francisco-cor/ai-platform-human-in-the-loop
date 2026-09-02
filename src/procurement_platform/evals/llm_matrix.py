"""LLM Provider comparison harness — Fase 6 LLMOps.

Ejecuta suite 22 con llm_provider=gemini vs deepseek vs fake y compara
success, unsafe, cost, latency, tool_accuracy en evals/reports/llm_matrix.json

Uso:
  python -m procurement_platform.evals.llm_matrix
  python -m procurement_platform.evals.llm_matrix --providers fake gemini deepseek --cases-dir evals/procurement

No compara solo texto, sino tool_calls y terminal_state (via harness metrics).
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from procurement_platform.evals.harness import run_suite
from procurement_platform.config.settings import get_settings, reset_settings_cache
from procurement_platform.persistence.database import Base, get_sessionmaker


def run_matrix(
    cases_dir: Path = Path("evals/procurement"),
    providers: list[str] | None = None,
    suite: str = "all",
    output_path: Path | None = None,
) -> dict[str, Any]:
    if providers is None:
        providers = ["fake", "gemini", "deepseek"]

    results_by_provider: dict[str, dict[str, Any]] = {}

    for provider in providers:
        # set provider via env + reset cache
        os.environ["PROCUREMENT_LLM_PROVIDER"] = provider
        # Ensure fallback enabled for ci (without keys, gemini/deepseek will fallback to fake)
        os.environ["PROCUREMENT_LLM_FALLBACK_ENABLED"] = "true"
        reset_settings_cache()
        # clear cache between providers to isolate (no cross-provider cache hit)
        try:
            from procurement_platform.agents.cache import reset_llm_cache

            reset_llm_cache()
        except Exception:
            pass
        try:
            from procurement_platform.workflows.orchestrator import reset_finops_state

            reset_finops_state()
        except Exception:
            pass
        try:
            from procurement_platform.observability.metrics import reset_metrics

            reset_metrics()
        except Exception:
            pass

        # Use isolated DB session per provider (clear tables via harness clear_db each suite)
        SessionLocal = get_sessionmaker()
        # Ensure tables
        try:
            import procurement_platform.persistence.models  # noqa: F401

            engine = SessionLocal().get_bind()
            if engine is None:
                from procurement_platform.persistence.database import get_engine

                engine = get_engine()
            Base.metadata.create_all(bind=engine)
        except Exception:
            pass

        db = SessionLocal()
        try:
            report = run_suite(cases_dir=cases_dir, suite=suite, db=db)
            metrics = report["metrics"]
            # also compute tool_calls avg from results
            tool_calls_total = sum(r["metrics"].get("tool_calls_count", 0) for r in report["results"])
            avg_tool_calls = round(tool_calls_total / metrics["total_cases"], 2) if metrics["total_cases"] else 0
            # cost per provider from harness metrics
            results_by_provider[provider] = {
                "provider": provider,
                "model": report["versions"].get("llm_model"),
                "prompt_version": report["versions"].get("prompt_version"),
                "graph_version": report["versions"].get("graph_version"),
                "task_success_rate": metrics["task_success_rate"],
                "tool_call_accuracy": metrics["tool_call_accuracy"],
                "latency_p50_s": metrics["latency_p50_s"],
                "latency_p95_s": metrics["latency_p95_s"],
                "latency_avg_s": metrics["latency_avg_s"],
                "total_cost_usd": metrics["total_cost_usd"],
                "avg_cost_per_task": metrics["avg_cost_per_task"],
                "total_tokens": metrics["total_tokens"],
                "avg_tokens_per_task": metrics["avg_tokens_per_task"],
                "unsafe_count": metrics["unsafe_count"],
                "unsafe_execution_rate": metrics["unsafe_execution_rate"],
                "duplicate_count": metrics["duplicate_count"],
                "duplicate_action_rate": metrics["duplicate_action_rate"],
                "avg_tool_calls_per_task": avg_tool_calls,
                "passed": metrics["passed"],
                "total_cases": metrics["total_cases"],
                "human_intervention_rate": metrics["human_intervention_rate"],
            }
        finally:
            try:
                db.close()
            except Exception:
                pass

    # compute deltas vs fake baseline
    baseline = results_by_provider.get("fake") or next(iter(results_by_provider.values()))
    deltas = {}
    for prov, data in results_by_provider.items():
        if prov == "fake":
            continue
        deltas[prov] = {
            "success_delta_pct": round(data["task_success_rate"] - baseline["task_success_rate"], 2),
            "unsafe_delta": data["unsafe_count"] - baseline["unsafe_count"],
            "cost_delta_usd": round(data["total_cost_usd"] - baseline["total_cost_usd"], 5),
            "latency_p95_delta_s": round(data["latency_p95_s"] - baseline["latency_p95_s"], 3),
            "tool_accuracy_delta_pct": round(data["tool_call_accuracy"] - baseline["tool_call_accuracy"], 2),
            "avg_tool_calls_delta": round(data["avg_tool_calls_per_task"] - baseline["avg_tool_calls_per_task"], 2),
        }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "suite": suite,
        "cases_dir": str(cases_dir),
        "providers": results_by_provider,
        "baseline_provider": "fake",
        "deltas_vs_baseline": deltas,
        "versions": {
            "code_commit": _get_commit(),
            "prompt_version": get_settings().prompt_version,
            "graph_version": get_settings().graph_version,
        },
        "notes": "Comparison via harness.run_suite direct mode; tool_calls and terminal_state compared, not just text. Gemini/DeepSeek fallback to Fake in CI without keys.",
    }

    # save
    if output_path is None:
        output_path = Path("evals/reports/llm_matrix.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    # also write latest matrix
    try:
        (output_path.parent / "latest_llm_matrix.json").write_text(
            output_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except Exception:
        pass
    return report


def _get_commit() -> str:
    try:
        import subprocess

        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="LLM matrix comparison — Fase 6")
    parser.add_argument("--cases-dir", default="evals/procurement")
    parser.add_argument("--providers", nargs="+", default=["fake", "gemini", "deepseek"])
    parser.add_argument("--suite", default="all")
    parser.add_argument("--output", default="evals/reports/llm_matrix.json")
    parser.add_argument("--json-out", default=None, help="Alias for --output")
    args = parser.parse_args()
    out = Path(args.json_out or args.output)
    report = run_matrix(
        cases_dir=Path(args.cases_dir),
        providers=args.providers,
        suite=args.suite,
        output_path=out,
    )
    # print summary table
    print(f"\nLLM Matrix — {report['generated_at']} — suite {report['suite']}")
    print("-" * 90)
    hdr = f"{'provider':<10} {'success%':<9} {'unsafe':<7} {'cost':<9} {'p95':<7} {'tool_acc%':<10} {'tools/task':<10}"
    print(hdr)
    print("-" * 90)
    for prov, data in report["providers"].items():
        print(
            f"{prov:<10} {data['task_success_rate']:<9} {data['unsafe_count']:<7} ${data['total_cost_usd']:<8} {data['latency_p95_s']:<7} {data['tool_call_accuracy']:<10} {data['avg_tool_calls_per_task']:<10}"
        )
    if report["deltas_vs_baseline"]:
        print("\nDeltas vs fake baseline:")
        for prov, d in report["deltas_vs_baseline"].items():
            print(f"  {prov}: success {d['success_delta_pct']:+}% tool_acc {d['tool_accuracy_delta_pct']:+}% cost {d['cost_delta_usd']:+} p95 {d['latency_p95_delta_s']:+}s")
    print(f"\nReport written to {out}")
    if report["providers"]:
        # also ensure success threshold
        all_success = all(v["task_success_rate"] >= 90 for v in report["providers"].values())
        if not all_success:
            print("WARNING: some provider success <90%")

if __name__ == "__main__":
    main()
