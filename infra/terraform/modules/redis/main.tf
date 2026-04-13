# Redis module — local dev uses docker-compose (no Terraform needed).
# This module manages Redis for cloud deployments.
# TD-004-10: AWS (ElastiCache) and GCP (Memorystore) implementations deferred.

variable "environment" {
  description = "Deployment environment: local, aws, gcp"
  type        = string
  default     = "local"

  validation {
    condition     = contains(["local", "aws", "gcp"], var.environment)
    error_message = "environment must be one of: local, aws, gcp"
  }
}

variable "node_type" {
  description = "Redis node type (used for AWS ElastiCache)"
  type        = string
  default     = "cache.t3.micro"
}

variable "ttl_hours" {
  description = "Default TTL for feature cache entries (hours)"
  type        = number
  default     = 24
}

variable "enable_sentinel" {
  description = "Enable Redis Sentinel for HA (production use)"
  type        = bool
  default     = false
}

variable "num_replicas" {
  description = "Number of Redis replicas (0 = standalone)"
  type        = number
  default     = 0
}

# Local environment: Redis is managed by docker-compose, not Terraform.
# No resources created for 'local' environment.
# Cloud resources (ElastiCache, Memorystore) deferred to TD-004-10.

output "redis_url" {
  description = "Redis connection URL"
  value       = var.environment == "local" ? "redis://localhost:6379/0" : "CONFIGURE_IN_CLOUD_MODULE"
}

output "ttl_hours" {
  value = var.ttl_hours
}
