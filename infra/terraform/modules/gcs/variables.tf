variable "project_id" {
  type = string
}

variable "location" {
  type    = string
  default = "US"
}

variable "buckets" {
  type = map(object({
    storage_class = string
    versioning    = bool
    lifecycle_days = number
  }))
  description = "Buckets to create: key is bucket suffix, value is config"
  default = {
    docs = {
      storage_class  = "STANDARD"
      versioning     = true
      lifecycle_days = 365
    }
    evals = {
      storage_class  = "STANDARD"
      versioning     = true
      lifecycle_days = 90
    }
    artifacts = {
      storage_class  = "STANDARD"
      versioning     = true
      lifecycle_days = 30
    }
  }
}

variable "project_prefix" {
  type    = string
  default = "procurement"
}
