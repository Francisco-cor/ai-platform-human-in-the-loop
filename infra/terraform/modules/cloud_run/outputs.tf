output "service_url" {
  value       = google_cloud_run_v2_service.api.uri
  description = "Cloud Run service URL"
}

output "service_name" {
  value = google_cloud_run_v2_service.api.name
}

output "location" {
  value = google_cloud_run_v2_service.api.location
}
