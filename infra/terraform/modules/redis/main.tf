resource "google_redis_instance" "cache" {
  name           = var.instance_name
  project        = var.project_id
  region         = var.region
  tier           = var.tier
  memory_size_gb = var.memory_size_gb
  redis_version  = var.redis_version

  authorized_network = var.authorized_network

  redis_configs = {
    maxmemory-policy = "allkeys-lru"
  }

  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 3
        minutes = 0
      }
    }
  }
}

# Memorystore for Redis is used for locks, idempotency, rate-limit, queue
# App connects via PROCUREMENT_REDIS_URL=redis://<host>:6379/0
