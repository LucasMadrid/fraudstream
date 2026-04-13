# Local development Terraform configuration
# Manages Kafka topics and Schema Registry subjects for docker-compose environment.
# No cloud provider needed — uses local Kafka and Schema Registry.

terraform {
  required_version = ">= 1.5"
  required_providers {
    kafka = {
      source  = "Mongey/kafka"
      version = "~> 0.5"
    }
    schemaregistry = {
      source  = "Telefonica/schema-registry"
      version = "~> 2.0"
    }
  }
  # State stored locally in v1. Production: use S3 + SSE or Terraform Cloud.
  # TD-004-10: Cloud backends deferred to cloud-deployment spec.
}

provider "kafka" {
  bootstrap_servers = ["localhost:9092"]
}

provider "schemaregistry" {
  schema_registry_url = "http://localhost:8081"
}

module "fraud_alerts_topic" {
  source             = "../modules/kafka-topic"
  name               = "txn.fraud.alerts"
  partitions         = 1  # Local dev — single partition
  replication_factor = 1
  retention_ms       = 86400000  # 1 day for local dev
}

module "fraud_alerts_dlq_topic" {
  source             = "../modules/kafka-topic"
  name               = "txn.fraud.alerts.dlq"
  partitions         = 1
  replication_factor = 1
  retention_ms       = 604800000  # 7 days — DLQ retains longer
}

module "enriched_transactions_topic" {
  source             = "../modules/kafka-topic"
  name               = "txn.enriched"
  partitions         = 1
  replication_factor = 1
}
