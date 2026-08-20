# Terraform skeleton — Fase 9 (GCP staging)
# Este archivo es placeholder Fase 1; módulos reales se añadirán en Fase 9.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
  # backend "gcs" {}  # configurar por entorno
}

variable "project_id" {
  type        = string
  description = "GCP project id"
  default     = "procurement-platform-staging"
}

variable "region" {
  type    = string
  default = "us-central1"
}

# Fase 1: solo provider, sin recursos
provider "google" {
  project = var.project_id
  region  = var.region
}

# Futuros módulos (Fase 9):
# - google_project_service (run, sql, redis, storage, bigquery)
# - google_artifact_registry_repository
# - google_cloud_run_v2_service (api)
# - google_sql_database_instance (postgres + pgvector)
# - google_redis_instance
# - google_storage_bucket (docs, evals, artifacts)
# - google_bigquery_dataset (ops, evals)
# - google_secret_manager_secret (tokens)
# - google_service_account + IAM
