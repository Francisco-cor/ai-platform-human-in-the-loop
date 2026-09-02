resource "google_sql_database_instance" "pg" {
  name             = var.instance_name
  project          = var.project_id
  region           = var.region
  database_version = var.database_version

  deletion_protection = var.deletion_protection

  settings {
    tier              = var.tier
    disk_size         = var.disk_size
    disk_autoresize   = true
    availability_type = "ZONAL"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      backup_retention_settings {
        retained_backups = 7
      }
      transaction_log_retention_days = 7
    }

    ip_configuration {
      ipv4_enabled = true
      # private_network is recommended for prod via VPC
    }

    maintenance_window {
      day          = 7
      hour         = 3
      update_track = "stable"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
}

resource "google_sql_database" "procurement" {
  name     = var.database_name
  instance = google_sql_database_instance.pg.name
  project  = var.project_id
}

resource "google_sql_user" "procurement" {
  name     = var.user_name
  instance = google_sql_database_instance.pg.name
  project  = var.project_id
  host     = "%"
  password = random_password.db_password.result
}

resource "random_password" "db_password" {
  length  = 24
  special = false
}

# pgvector extension is enabled via SQL: CREATE EXTENSION IF NOT EXISTS vector;
# Alembic migration 005_pgvector_hnsw enables it idempotently; Terraform does not manage extension
