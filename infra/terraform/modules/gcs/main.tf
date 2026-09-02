resource "google_storage_bucket" "buckets" {
  for_each = var.buckets

  name          = "${var.project_id}-${var.project_prefix}-${each.key}"
  project       = var.project_id
  location      = var.location
  storage_class = each.value.storage_class
  force_destroy = false

  versioning {
    enabled = each.value.versioning
  }

  uniform_bucket_level_access = true

  encryption {
    # default Google-managed key; CMEK via var.kms_key if needed
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = each.value.lifecycle_days
    }
  }

  labels = {
    env     = "staging"
    app     = "procurement"
    bucket  = each.key
    managed = "terraform"
  }
}

# Separate buckets: docs, evals, artifacts, backups
