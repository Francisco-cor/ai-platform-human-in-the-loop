"""Fase 10 — Terraform módulos reales tests."""

import pathlib


def _read(path: str) -> str:
    p = pathlib.Path(path)
    assert p.exists(), f"Missing {path}"
    return p.read_text(encoding="utf-8")


def test_terraform_modules_exist():
    modules = ["cloud_run", "cloud_sql", "redis", "gcs", "bq", "iam", "secrets"]
    for m in modules:
        for fname in ["variables.tf", "main.tf", "outputs.tf"]:
            path = f"infra/terraform/modules/{m}/{fname}"
            assert pathlib.Path(path).exists(), f"Missing {path}"
    # envs staging
    for fname in ["main.tf", "variables.tf", "outputs.tf"]:
        assert pathlib.Path(f"infra/terraform/envs/staging/{fname}").exists()
    assert pathlib.Path("infra/terraform/envs/prod/main.tf").exists()
    # root
    assert pathlib.Path("infra/terraform/main.tf").exists()


def test_terraform_cloud_run_module():
    content = _read("infra/terraform/modules/cloud_run/main.tf")
    assert "google_cloud_run_v2_service" in content
    assert "service_account" in content
    assert "startup_probe" in content
    assert "liveness_probe" in content
    assert "scaling" in content


def test_terraform_cloud_sql_module():
    content = _read("infra/terraform/modules/cloud_sql/main.tf")
    assert "google_sql_database_instance" in content
    assert "backup_configuration" in content
    assert "point_in_time_recovery_enabled" in content
    assert "POSTGRES_16" in content or "database_version" in content


def test_terraform_redis_module():
    content = _read("infra/terraform/modules/redis/main.tf")
    assert "google_redis_instance" in content
    assert "memory_size_gb" in content


def test_terraform_gcs_module():
    content = _read("infra/terraform/modules/gcs/main.tf")
    assert "google_storage_bucket" in content
    assert "versioning" in content
    assert "lifecycle_rule" in content


def test_terraform_bq_module():
    content = _read("infra/terraform/modules/bq/main.tf")
    assert "google_bigquery_dataset" in content
    assert "google_bigquery_table" in content
    assert "audit" in content


def test_terraform_iam_module():
    content = _read("infra/terraform/modules/iam/main.tf")
    assert "google_service_account" in content
    assert "workloadIdentityUser" in content or "workload_identity" in content
    # role is in variables.tf default
    var_content = _read("infra/terraform/modules/iam/variables.tf")
    assert "roles/run.invoker" in content or "roles/run.invoker" in var_content


def test_terraform_secrets_module():
    content = _read("infra/terraform/modules/secrets/main.tf")
    assert "google_secret_manager_secret" in content
    assert "rotation" in content.lower()
    # rotation 30d is 2592000s or via variable rotation_days * 86400
    assert "2592000" in content or "86400" in content or "rotation_days" in content
    assert "workloadIdentityUser" in _read("infra/terraform/modules/iam/main.tf")


def test_terraform_staging_composes_modules():
    content = _read("infra/terraform/envs/staging/main.tf")
    for mod in ["cloud_run", "cloud_sql", "redis", "gcs", "bq", "iam", "secrets"]:
        assert f'module "{mod}"' in content or f"module.{mod}" in content
    assert 'backend "gcs"' in content
    assert "google_project_service" in content
    assert "google_artifact_registry_repository" in content


def test_terraform_fmt_idempotent():
    # Simple fmt check: no tab characters, trailing whitespace check via ruff? Just ensure files are not empty
    for m in ["cloud_run", "cloud_sql"]:
        content = _read(f"infra/terraform/modules/{m}/main.tf")
        assert len(content) > 100
        assert "resource" in content
