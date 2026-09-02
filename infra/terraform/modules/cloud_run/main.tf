resource "google_cloud_run_v2_service" "api" {
  name     = var.service_name
  location = var.region
  project  = var.project_id

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    containers {
      image = var.image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle = true
      }

      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        http_get {
          port = 8000
          path = "/healthz"
        }
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 3
      }

      liveness_probe {
        http_get {
          port = 8000
          path = "/healthz"
        }
        period_seconds = 30
      }
    }

    timeout = "300s"
  }

  traffic {
    percent         = 100
    latest_revision = true
  }
}

resource "google_cloud_run_service_iam_member" "public" {
  location = google_cloud_run_v2_service.api.location
  project  = var.project_id
  service  = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Canary traffic splitting is managed via gcloud run deploy --traffic 90:stable,10:canary
# This module exposes stable revision for deploy script
