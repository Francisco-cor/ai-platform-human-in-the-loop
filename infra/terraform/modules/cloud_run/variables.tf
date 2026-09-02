variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type        = string
  description = "GCP region"
  default     = "us-central1"
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name"
  default     = "procurement-api"
}

variable "image" {
  type        = string
  description = "Container image (Artifact Registry)"
}

variable "service_account_email" {
  type        = string
  description = "Service account for Cloud Run (workload identity)"
}

variable "env_vars" {
  type        = map(string)
  description = "Environment variables for Cloud Run"
  default     = {}
  sensitive   = false
}

variable "min_instance_count" {
  type    = number
  default = 1
}

variable "max_instance_count" {
  type    = number
  default = 10
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}
