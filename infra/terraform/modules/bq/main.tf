resource "google_bigquery_dataset" "datasets" {
  for_each = var.datasets

  dataset_id  = each.key
  project     = var.project_id
  location    = var.location
  description = each.value.description

  default_table_expiration_ms = null

  labels = {
    env = "staging"
    app = "procurement"
  }
}

resource "google_bigquery_table" "audit" {
  dataset_id = google_bigquery_dataset.datasets["procurement_ops"].dataset_id
  table_id   = "audit"
  project    = var.project_id
  deletion_protection = false

  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "execution_id", type = "STRING", mode = "REQUIRED" },
    { name = "event_type", type = "STRING", mode = "REQUIRED" },
    { name = "actor_id", type = "STRING", mode = "NULLABLE" },
    { name = "trace_id", type = "STRING", mode = "NULLABLE" },
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "input_hash", type = "STRING", mode = "NULLABLE" },
    { name = "output_hash", type = "STRING", mode = "NULLABLE" },
    { name = "lineage", type = "JSON", mode = "NULLABLE" },
    { name = "model_metadata", type = "JSON", mode = "NULLABLE" }
  ])
}

resource "google_bigquery_table" "evals" {
  dataset_id = google_bigquery_dataset.datasets["procurement_evals"].dataset_id
  table_id   = "reports"
  project    = var.project_id
  deletion_protection = false
  schema = jsonencode([
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "task_success_rate", type = "FLOAT", mode = "NULLABLE" },
    { name = "prompt_version", type = "STRING", mode = "NULLABLE" },
    { name = "graph_version", type = "STRING", mode = "NULLABLE" },
    { name = "unsafe_count", type = "INTEGER", mode = "NULLABLE" }
  ])
}

resource "google_bigquery_table" "finops" {
  dataset_id = google_bigquery_dataset.datasets["procurement_finops"].dataset_id
  table_id   = "cost"
  project    = var.project_id
  deletion_protection = false
  schema = jsonencode([
    { name = "execution_id", type = "STRING", mode = "REQUIRED" },
    { name = "tenant_id", type = "STRING", mode = "NULLABLE" },
    { name = "provider", type = "STRING", mode = "NULLABLE" },
    { name = "model", type = "STRING", mode = "NULLABLE" },
    { name = "tokens", type = "INTEGER", mode = "NULLABLE" },
    { name = "cost_usd", type = "FLOAT", mode = "NULLABLE" },
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}
