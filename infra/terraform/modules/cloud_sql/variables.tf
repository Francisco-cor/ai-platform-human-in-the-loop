variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "instance_name" {
  type    = string
  default = "procurement-pg"
}

variable "database_version" {
  type    = string
  default = "POSTGRES_16"
}

variable "tier" {
  type    = string
  default = "db-f1-micro"
}

variable "disk_size" {
  type    = number
  default = 20
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "database_name" {
  type    = string
  default = "procurement"
}

variable "user_name" {
  type    = string
  default = "procurement"
}
