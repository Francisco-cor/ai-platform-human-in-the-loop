"""Fase 10 — CI/CD pipeline tests."""

import pathlib


def test_cd_workflow_exists():
    p = pathlib.Path(".github/workflows/cd.yml")
    assert p.exists(), "cd.yml missing"
    content = p.read_text(encoding="utf-8")
    assert "build" in content
    assert "trivy" in content.lower()
    assert "pip-audit" in content.lower()
    assert "syft" in content.lower() or "sbom" in content.lower()
    assert "terraform" in content.lower()
    assert "deploy-staging" in content or "deploy_staging" in content
    assert "deploy-prod" in content or "deploy_prod" in content


def test_cd_build_uses_buildx_and_version_arg():
    content = pathlib.Path(".github/workflows/cd.yml").read_text(encoding="utf-8")
    assert "buildx" in content
    assert "VERSION" in content
    assert "cache-from" in content
    assert "cache-to" in content


def test_ci_still_has_eval_gate():
    content = pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "eval gate" in content.lower() or "eval-gate" in content.lower()
    assert "prompt" in content.lower()


def test_dockerfile_non_root_and_healthcheck():
    content = pathlib.Path("Dockerfile").read_text(encoding="utf-8")
    assert "user app" in content.lower() or "USER app" in content
    assert "HEALTHCHECK" in content
    assert "curl" in content and "/healthz" in content
    assert "multi-stage" in content.lower() or "AS builder" in content


def test_docker_scan_and_sbom_in_cd():
    content = pathlib.Path(".github/workflows/cd.yml").read_text(encoding="utf-8")
    assert "trivy" in content.lower()
    assert "sbom" in content.lower()


def test_makefile_targets():
    content = pathlib.Path("Makefile").read_text(encoding="utf-8")
    for target in [
        "docker-build",
        "terraform-validate",
        "backup-drill",
        "migrate-job",
        "chaos-test",
    ]:
        assert target in content, f"Missing Makefile target {target}"
    assert "VERSION" in content


def test_cd_uses_workload_identity_not_key_file():
    content = pathlib.Path(".github/workflows/cd.yml").read_text(encoding="utf-8")
    assert "workload_identity_provider" in content or "WIF" in content
    assert "service_account" in content.lower()
    # ensure no key file secret like GCP_SA_KEY
    assert "GCP_SA_KEY" not in content
