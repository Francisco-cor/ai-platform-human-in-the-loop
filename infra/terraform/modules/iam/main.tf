resource "google_service_account" "api" {
  account_id   = var.service_account_name
  project      = var.project_id
  display_name = "Procurement API service account (workload identity)"
}

resource "google_project_iam_member" "bindings" {
  for_each = toset(var.roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Workload Identity binding for Cloud Run (no key file)
# Cloud Run service runs as this SA via google_cloud_run_v2_service.template.service_account

resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[default/procurement-api]"
}
