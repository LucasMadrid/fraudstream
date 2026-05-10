#!/bin/bash
# =============================================================================
# Setup SCRAM Users and ACLs for Kafka Security Testing (TB-001)
# =============================================================================
# This script creates SCRAM-SHA-256 credentials and ACLs for test users.
# Run after Kafka broker is healthy.
# =============================================================================

set -e

KAFKA_BIN="/usr/bin"
BOOTSTRAP_SERVER="broker-secure:9093"
CLIENT_CONFIG="/tmp/client.properties"

echo "=== Setting up SCRAM-SHA-256 users ==="

# Create admin user (super user - already created in JAAS, but ensure SCRAM credentials)
${KAFKA_BIN}/kafka-configs --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --alter --add-config 'SCRAM-SHA-256=[password=admin-secret]' \
  --entity-type users --entity-name admin 2>/dev/null || echo "Admin user already exists"

# Create producer user
${KAFKA_BIN}/kafka-configs --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --alter --add-config 'SCRAM-SHA-256=[password=producer-secret]' \
  --entity-type users --entity-name producer 2>/dev/null || echo "Producer user already exists"

# Create consumer user
${KAFKA_BIN}/kafka-configs --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --alter --add-config 'SCRAM-SHA-256=[password=consumer-secret]' \
  --entity-type users --entity-name consumer 2>/dev/null || echo "Consumer user already exists"

# Create fraudapp user (application service account)
${KAFKA_BIN}/kafka-configs --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --alter --add-config 'SCRAM-SHA-256=[password=fraudapp-secret]' \
  --entity-type users --entity-name fraudapp 2>/dev/null || echo "Fraudapp user already exists"

# Create schema-registry user
${KAFKA_BIN}/kafka-configs --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --alter --add-config 'SCRAM-SHA-256=[password=schema-registry-secret]' \
  --entity-type users --entity-name schema-registry 2>/dev/null || echo "Schema-registry user already exists"

echo ""
echo "=== Setting up ACLs ==="

# Admin user - full access to all resources (super user, ACLs not strictly needed but explicit)
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:admin --operation All --topic '*' --group '*' 2>/dev/null || true

# Producer user - write access to txn.* topics
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:producer --operation Write --topic 'txn.*' 2>/dev/null || true
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:producer --operation Create --topic 'txn.*' 2>/dev/null || true
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:producer --operation Describe --topic 'txn.*' 2>/dev/null || true

# Consumer user - read access to txn.* topics
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:consumer --operation Read --topic 'txn.*' 2>/dev/null || true
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:consumer --operation Describe --topic 'txn.*' 2>/dev/null || true
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:consumer --operation Read --group 'consumer-group-*' 2>/dev/null || true

# Fraudapp user - full access to fraud-related topics
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:fraudapp --operation All --topic 'txn.*' 2>/dev/null || true
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:fraudapp --operation All --topic 'fraud.*' 2>/dev/null || true
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:fraudapp --operation All --group 'fraud-*' 2>/dev/null || true

# Schema Registry user - describe and write to _schemas topic
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --add --allow-principal User:schema-registry --operation All --topic '_schemas' 2>/dev/null || true

echo ""
echo "=== Verifying users ==="
${KAFKA_BIN}/kafka-configs --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --describe --entity-type users --all 2>/dev/null || echo "Could not list users (expected on first run)"

echo ""
echo "=== Listing ACLs ==="
${KAFKA_BIN}/kafka-acls --bootstrap-server ${BOOTSTRAP_SERVER} \
  --command-config ${CLIENT_CONFIG} \
  --list 2>/dev/null || echo "Could not list ACLs (expected on first run)"

echo ""
echo "=== Setup complete ==="
echo "Test users created:"
echo "  - admin/admin-secret (super user)"
echo "  - producer/producer-secret (write to txn.*)"
echo "  - consumer/consumer-secret (read from txn.*)"
echo "  - fraudapp/fraudapp-secret (full access to fraud topics)"
echo "  - schema-registry/schema-registry-secret (schema registry access)"
