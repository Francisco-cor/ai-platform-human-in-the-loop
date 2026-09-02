variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "instance_name" {
  type    = string
  default = "procurement-redis"
}

variable "memory_size_gb" {
  type    = number
  default = 1
}

variable "tier" {
  type    = string
  default = "BASIC"
}

variable "redis_version" {
  type    = string
  default = "REDIS_7_0"
}

variable "authorized_network" {
  type    = string
  default = null
}
