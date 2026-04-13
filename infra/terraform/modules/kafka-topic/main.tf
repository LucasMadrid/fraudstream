terraform {
  required_providers {
    kafka = {
      source  = "Mongey/kafka"
      version = "~> 0.5"
    }
  }
}

variable "name" {
  description = "Kafka topic name"
  type        = string
}

variable "partitions" {
  description = "Number of partitions"
  type        = number
  default     = 6
}

variable "replication_factor" {
  description = "Replication factor"
  type        = number
  default     = 3
}

variable "retention_ms" {
  description = "Log retention in milliseconds (-1 = unlimited)"
  type        = number
  default     = 604800000  # 7 days
}

variable "cleanup_policy" {
  description = "Cleanup policy: delete or compact"
  type        = string
  default     = "delete"
}

resource "kafka_topic" "this" {
  name               = var.name
  partitions         = var.partitions
  replication_factor = var.replication_factor

  config = {
    "retention.ms"   = tostring(var.retention_ms)
    "cleanup.policy" = var.cleanup_policy
  }
}

output "name" {
  value = kafka_topic.this.name
}
