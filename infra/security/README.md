# TB-001: Security Test Environment for Kafka SASL/TLS

Docker Compose configuration for testing Kafka with SASL/SCRAM authentication and TLS encryption.

## Overview

This environment enables testing of P0-001 (Kafka authentication and encryption) with:

- **SASL_PLAINTEXT** listener (port 9093) - SCRAM auth without TLS
- **SASL_SSL** listener (port 9094) - SCRAM auth with TLS
- **SCRAM-SHA-256** authentication mechanism
- **Kafka ACLs** for authorization
- **Self-signed TLS certificates** for testing

## Quick Start

```bash
# 1. Generate TLS certificates (first time only)
cd infra/security
chmod +x generate-certs.sh
./generate-certs.sh

# 2. Start the secure environment
cd ../..
docker compose -f infra/docker-compose.security.yml up -d

# 3. Run user setup (creates SCRAM credentials and ACLs)
docker compose -f infra/docker-compose.security.yml --profile setup run --rm kafka-setup-secure
```

## Test Users

| Username | Password | Access Level |
|----------|----------|--------------|
| `admin` | `admin-secret` | Super user - full access to all resources |
| `producer` | `producer-secret` | Write access to `txn.*` topics |
| `consumer` | `consumer-secret` | Read access to `txn.*` topics |
| `fraudapp` | `fraudapp-secret` | Full access to `txn.*` and `fraud.*` topics |
| `schema-registry` | `schema-registry-secret` | Access to `_schemas` topic |

## Testing SASL_PLAINTEXT (Port 9093)

```bash
# Create a test topic
kafka-topics --bootstrap-server localhost:9093 \
  --command-config infra/security/client.properties \
  --create --topic test-sasl-plaintext --partitions 1 --replication-factor 1

# Produce messages
kafka-console-producer --bootstrap-server localhost:9093 \
  --producer.config infra/security/client.properties \
  --topic test-sasl-plaintext

# Consume messages
kafka-console-consumer --bootstrap-server localhost:9093 \
  --consumer.config infra/security/client.properties \
  --topic test-sasl-plaintext --from-beginning
```

## Testing SASL_SSL (Port 9094)

```bash
# Create client config for SSL
cat > /tmp/client-ssl.properties << 'EOF'
security.protocol=SASL_SSL
sasl.mechanism=SCRAM-SHA-256
sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="admin" password="admin-secret";
ssl.truststore.location=infra/security/certs/kafka.client.truststore.jks
ssl.truststore.password=testpass123
ssl.endpoint.identification.algorithm=https
EOF

# Produce messages
kafka-console-producer --bootstrap-server localhost:9094 \
  --producer.config /tmp/client-ssl.properties \
  --topic test-sasl-ssl
```

## Testing ACL Enforcement

```bash
# Try to produce as consumer (should fail - consumer only has read access)
kafka-console-producer --bootstrap-server localhost:9093 \
  --producer.property security.protocol=SASL_PLAINTEXT \
  --producer.property sasl.mechanism=SCRAM-SHA-256 \
  --producer.property 'sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="consumer" password="consumer-secret";' \
  --topic test-sasl-plaintext

# List ACLs
kafka-acls --bootstrap-server localhost:9093 \
  --command-config infra/security/client.properties \
  --list
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| `broker-secure` | 9092, 9093, 9094 | Kafka with SASL_PLAINTEXT and SASL_SSL |
| `schema-registry-secure` | 8081 | Schema Registry with SASL auth |
| `kafka-ui` | 8080 | Web UI for Kafka management |
| `prometheus-secure` | 9090 | Metrics collection |

## Files

```
infra/security/
├── README.md                          # This file
├── generate-certs.sh                  # TLS certificate generation
├── setup-users.sh                     # User and ACL setup
├── kafka_server_jaas.conf             # Server JAAS config
├── kafka_client_jaas.conf             # Client JAAS template
├── client.properties                  # Client properties
├── certs/                             # Generated certificates (after running generate-certs.sh)
│   ├── kafka.server.keystore.jks
│   ├── kafka.server.truststore.jks
│   ├── kafka.client.keystore.jks
│   ├── kafka.client.truststore.jks
│   └── *.cred (password files)
└── prometheus/
    └── prometheus-security.yml        # Prometheus config
```

## Troubleshooting

### Broker fails to start
Check that certificates exist:
```bash
ls -la infra/security/certs/
```

### Authentication failures
Verify SCRAM users are created:
```bash
docker compose -f infra/docker-compose.security.yml exec broker-secure \
  kafka-configs --bootstrap-server broker-secure:9093 \
  --command-config /etc/kafka/client.properties \
  --describe --entity-type users
```

### ACL denials
List current ACLs:
```bash
docker compose -f infra/docker-compose.security.yml exec broker-secure \
  kafka-acls --bootstrap-server broker-secure:9093 \
  --command-config /etc/kafka/client.properties --list
```

## Integration with Test Suite

Use this environment for:

1. **Unit Tests**: Test SASL/SCRAM authentication logic
2. **Integration Tests**: Test producer/consumer with auth
3. **Security Tests**: Validate ACL enforcement
4. **TLS Tests**: Certificate validation and mTLS

Example pytest configuration:
```python
# tests/security/conftest.py
import pytest

@pytest.fixture(scope="session")
def secure_kafka_bootstrap():
    return "localhost:9093"

@pytest.fixture
def sasl_plaintext_config():
    return {
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": "admin",
        "sasl.password": "admin-secret",
    }
```
