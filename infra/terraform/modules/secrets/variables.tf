variable "project_id" {
  type = string
}

variable "secrets" {
  type = map(object({
    rotation_days = number
  }))
  default = {
    gemini_api_key = {
      rotation_days = 30
    }
    deepseek_api_key = {
      rotation_days = 30
    }
    db_password = {
      rotation_days = 30
    }
    jwt_secret = {
      rotation_days = 30
    }
  }
}

variable "service_account_email" {
  type        = string
  description = "Service account that can access secrets (workload identity)"
}
