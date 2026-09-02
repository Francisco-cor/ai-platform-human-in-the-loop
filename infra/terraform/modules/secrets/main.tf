resource "google_secret_manager_secret" "secrets" {
  for_each = var.secrets

  secret_id = each.key
  project   = var.project_id

  replication {
    auto {}
  }

  rotation {
    next_rotation_time = timeadd(timestamp(), "${each.value.rotation_days * 24}h")
    rotation_period    = "${each.value.rotation_days * 86400}s"
  }

  labels = {
    managed = "terraform"
    app     = "procurement"
  }
}

resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each = var.secrets

  project   = var.project_id
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_account_email}"
}

# Workload Identity is used; Cloud Run does not use downloaded key file
# Secret rotation triggers audit event: secret.rotation via Cloud Audit Logs -> Pub/Sub -> GCS

# Example version (initial) — actual value is added manually or via CI with `gcloud secrets versions add`
resource "google_secret_manager_secret_version" "initial" {
  for_each = var.secrets

  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = "changeme-${each.key}"

  lifecycle {
    ignore_changes = [secret_data]
  }
}
