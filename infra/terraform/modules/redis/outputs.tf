output "redis_host" {
  value = google_redis_instance.cache.host
}

output "redis_port" {
  value = google_redis_instance.cache.port
}

output "redis_url" {
  value = "redis://${google_redis_instance.cache.host}:${google_redis_instance.cache.port}/0"
  sensitive = true
}
