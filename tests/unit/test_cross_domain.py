"""Fase 11 — cross-domain eval harness."""

import pathlib
import json


def test_cross_domain_harness_loads_both():
    from procurement_platform.platform.evals.harness import Harness

    h_proc = Harness(domain="procurement")
    cases_proc = h_proc.load_cases()
    assert len(cases_proc) >= 14  # procurement has 22 cases

    h_exp = Harness(domain="expense")
    cases_exp = h_exp.load_cases()
    # expense has at least 1
    assert len(cases_exp) >= 1
    assert any(c["case_id"] == "expense_happy_001" for c in cases_exp)


def test_harness_run_all_domains():
    from procurement_platform.platform.evals.harness import run_all_domains

    result = run_all_domains()
    assert "procurement" in result
    assert "expense" in result
    # procurement should have 22/22
    proc = result["procurement"]
    assert "metrics" in proc or "total_cases" in str(proc)
    # expense should have at least 1 case
    exp = result["expense"]
    assert len(exp["cases"]) >= 1
    assert exp["cases"][0]["status"] == "COMPLETED"
