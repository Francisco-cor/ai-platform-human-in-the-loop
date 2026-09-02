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
