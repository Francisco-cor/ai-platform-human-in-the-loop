variable "project_id" {
  type = string
}

variable "location" {
  type    = string
  default = "US"
}

variable "datasets" {
  type = map(object({
    description = string
  }))
  default = {
    procurement_ops = {
      description = "Operational audit, lineage, traces"
    }
    procurement_evals = {
      description = "Evaluation reports, evals"
    }
    procurement_finops = {
      description = "Cost, tokens per execution"
    }
  }
}
