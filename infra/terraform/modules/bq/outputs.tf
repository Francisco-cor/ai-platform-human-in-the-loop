output "dataset_ids" {
  value = { for k, v in google_bigquery_dataset.datasets : k => v.dataset_id }
}

output "audit_table" {
  value = "${google_bigquery_dataset.datasets["procurement_ops"].dataset_id}.${google_bigquery_table.audit.table_id}"
}
