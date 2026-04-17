# kafka-topic module

Manages a Kafka topic via the Mongey/kafka Terraform provider.

## Usage

```hcl
module "fraud_alerts_topic" {
  source             = "./modules/kafka-topic"
  name               = "txn.fraud.alerts"
  partitions         = 6
  replication_factor = 3
  retention_ms       = 604800000
}
```

## Variables

| Name | Description | Default |
|------|-------------|---------|
| name | Topic name | required |
| partitions | Partition count | 6 |
| replication_factor | RF | 3 |
| retention_ms | Retention (ms) | 604800000 (7d) |
| cleanup_policy | delete/compact | delete |
