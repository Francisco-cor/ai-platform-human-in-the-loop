terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
  backend "gcs" {
    bucket = "procurement-tf-state-staging"
    prefix = "terraform/state"
  }
}

variable "project_id" {
  type    = string
  default = "procurement-platform-staging"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  type    = string
  default = "us-central1-docker.pkg.dev/procurement-platform-staging/procurement/procurement-api:staging-latest"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
  project = var.project_id
  service = each.value
  disable_on_destroy = false
}

module "iam" {
  source     = "../../modules/iam"
  project_id = var.project_id
  depends_on = [google_project_service.apis]
}

module "secrets" {
  source                = "../../modules/secrets"
  project_id            = var.project_id
  service_account_email = module.iam.service_account_email
  depends_on            = [google_project_service.apis]
}

module "gcs" {
  source     = "../../modules/gcs"
  project_id = var.project_id
  location   = "US"
  depends_on = [google_project_service.apis]
}

module "bq" {
  source     = "../../modules/bq"
  project_id = var.project_id
  location   = "US"
  depends_on = [google_project_service.apis]
}

module "cloud_sql" {
  source     = "../../modules/cloud_sql"
  project_id = var.project_id
  region     = var.region
  tier       = "db-g1-small"
  depends_on = [google_project_service.apis]
}

module "redis" {
  source     = "../../modules/redis"
  project_id = var.project_id
  region     = var.region
  depends_on = [google_project_service.apis]
}

# Artifact Registry
resource "google_artifact_registry_repository" "procurement" {
  project       = var.project_id
  location      = var.region
  repository_id = "procurement"
  format        = "DOCKER"
  description   = "Procurement platform images"
}

module "cloud_run" {
  source                = "../../modules/cloud_run"
  project_id            = var.project_id
  region                = var.region
  image                 = var.image
  service_account_email = module.iam.service_account_email
  env_vars = {
    PROCUREMENT_APP_ENV        = "staging"
    PROCUREMENT_DATABASE_URL   = "postgresql+psycopg://procurement:${module.cloud_sql.password_secret_id}@${module.cloud_sql.instance_connection_name}/procurement"
    PROCUREMENT_REDIS_URL      = module.redis.redis_url
    PROCUREMENT_GCS_BUCKET     = module.gcs.bucket_urls["artifacts"]
    PROCUREMENT_BIGQUERY_DATASET = "${var.project_id}.procurement_ops"
    PROCUREMENT_OTEL_EXPORTER  = "otlp"
  }
  depends_on = [google_project_service.apis]
}

output "cloud_run_url" {
  value = module.cloud_run.service_url
}

output "gcs_buckets" {
  value = module.gcs.bucket_names
}

output "bq_datasets" {
  value = module.bq.dataset_ids
}
