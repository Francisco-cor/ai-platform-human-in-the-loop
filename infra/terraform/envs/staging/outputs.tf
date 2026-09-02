output "cloud_run_url" {
  value = module.cloud_run.service_url
}

output "gcs_buckets" {
  value = module.gcs.bucket_names
}

output "bq_datasets" {
  value = module.bq.dataset_ids
}

output "redis_host" {
  value = module.redis.redis_host
}
