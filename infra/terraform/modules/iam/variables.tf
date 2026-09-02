variable "project_id" {
  type = string
}

variable "service_account_name" {
  type    = string
  default = "procurement-api"
}

variable "roles" {
  type    = list(string)
  default = [
    "roles/run.invoker",
    "roles/cloudsql.client",
    "roles/storage.objectAdmin",
    "roles/bigquery.dataEditor",
    "roles/secretmanager.secretAccessor"
  ]
}
