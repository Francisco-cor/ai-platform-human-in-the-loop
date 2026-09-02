"""
Platform evals — generic harness (Fase 11).

Harness(domain="procurement|expense") loads evals/{domain}/*.json,
runs isolated, captures events/tool_calls/cost, computes metrics.

Generic; domain provides case JSON and fixtures.
"""

from __future__ import annotations

from pathlib import Path

from procurement_platform.evals.harness import run_suite, run_case_direct, compute_suite_metrics, load_cases

__all__ = ["run_suite", "run_case_direct", "compute_suite_metrics", "load_cases"]
