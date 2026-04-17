# redis module

Manages Redis feature cache infrastructure.

**v1**: Local dev uses docker-compose Redis (see `infra/docker-compose.yml`).
Cloud deployment (AWS ElastiCache, GCP Memorystore) is deferred to TD-004-10.

## Variables

| Name | Description | Default |
|------|-------------|---------|
| environment | local/aws/gcp | local |
| node_type | AWS ElastiCache node type | cache.t3.micro |
| ttl_hours | Feature cache TTL (hours) | 24 |
| enable_sentinel | Redis Sentinel HA | false |
| num_replicas | Replica count | 0 |

## Sentinel / Cluster topology (v2)

For HA in cloud deployments (TD-004-10):
- AWS: ElastiCache Redis Cluster mode with 3 shards, 1 replica each
- GCP: Memorystore with HA replication enabled
- Sentinel: 3-node Sentinel configuration minimum
