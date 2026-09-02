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
    bucket = "procurement-tf-state-prod"
    prefix = "terraform/state/prod"
  }
}

variable "project_id" {
  type    = string
  default = "procurement-platform-prod"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  type    = string
  default = "us-central1-docker.pkg.dev/procurement-platform-prod/procurement/procurement-api:prod-latest"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

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
  depends_on = [google_project_service.apis]
}

module "bq" {
  source     = "../../modules/bq"
  project_id = var.project_id
  depends_on = [google_project_service.apis]
}

module "cloud_sql" {
  source     = "../../modules/cloud_sql"
  project_id = var.project_id
  region     = var.region
  tier       = "db-custom-1-3840"
  deletion_protection = true
  depends_on = [google_project_service.apis]
}

module "redis" {
  source     = "../../modules/redis"
  project_id = var.project_id
  region     = var.region
  memory_size_gb = 5
  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "procurement" {
  project       = var.project_id
  location      = var.region
  repository_id = "procurement"
  format        = "DOCKER"
}

module "cloud_run" {
  source                = "../../modules/cloud_run"
  project_id            = var.project_id
  region                = var.region
  image                 = var.image
  service_account_email = module.iam.service_account_email
  min_instance_count    = 2
  max_instance_count    = 20
  depends_on            = [google_project_service.apis]
}
