output "instance_name" {
  value = google_sql_database_instance.pg.name
}

output "instance_connection_name" {
  value = google_sql_database_instance.pg.connection_name
}

output "database_name" {
  value = google_sql_database.procurement.name
}

output "password_secret_id" {
  value = random_password.db_password.result
  sensitive = true
}
