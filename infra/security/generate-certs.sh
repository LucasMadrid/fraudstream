#!/bin/bash
# =============================================================================
# TLS Certificate Generation for Kafka Security Testing (TB-001)
# =============================================================================
# Generates self-signed certificates for testing SASL_SSL connections.
# Run this script before starting docker-compose.security.yml for the first time.
#
# Usage:
#   cd infra/security && ./generate-certs.sh
#
# Output: Creates certs/ directory with keystores and truststores
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}/certs"
PASSWORD="testpass123"
VALIDITY_DAYS=365
KEYSTORE_FILE="kafka.server.keystore.jks"
TRUSTSTORE_FILE="kafka.server.truststore.jks"
CLIENT_KEYSTORE_FILE="kafka.client.keystore.jks"
CLIENT_TRUSTSTORE_FILE="kafka.client.truststore.jks"

echo "=== Generating TLS Certificates for Kafka Security Testing ==="

# Create certs directory
mkdir -p "${CERTS_DIR}"
cd "${CERTS_DIR}"

# Clean up any existing certificates
echo "Cleaning up existing certificates..."
rm -f *.jks *.crt *.key *.csr *.srl *.p12

# =============================================================================
# Generate CA certificate
# =============================================================================
echo ""
echo "=== Generating CA Certificate ==="

openssl genrsa -out ca-key.pem 2048 2>/dev/null
openssl req -new -x509 -key ca-key.pem -out ca-cert.pem -days ${VALIDITY_DAYS} \
  -subj "/C=US/ST=CA/L=San Francisco/O=FraudStream/OU=Security/CN=FraudStream-CA" 2>/dev/null

echo "CA certificate generated: ca-cert.pem"

# =============================================================================
# Generate Server Certificate
# =============================================================================
echo ""
echo "=== Generating Server Certificate ==="

# Generate server key and CSR
openssl genrsa -out server-key.pem 2048 2>/dev/null
openssl req -new -key server-key.pem -out server-csr.pem \
  -subj "/C=US/ST=CA/L=San Francisco/O=FraudStream/OU=Security/CN=broker-secure" 2>/dev/null

# Sign with CA
openssl x509 -req -in server-csr.pem -CA ca-cert.pem -CAkey ca-key.pem \
  -CAcreateserial -out server-cert.pem -days ${VALIDITY_DAYS} 2>/dev/null

# Create PKCS12 bundle
openssl pkcs12 -export -in server-cert.pem -inkey server-key.pem \
  -certfile ca-cert.pem -out server.p12 -password pass:${PASSWORD} 2>/dev/null

echo "Server certificate generated: server-cert.pem"

# =============================================================================
# Create Server Keystore (JKS format)
# =============================================================================
echo ""
echo "=== Creating Server Keystore (${KEYSTORE_FILE}) ==="

keytool -importkeystore \
  -deststorepass ${PASSWORD} -destkeypass ${PASSWORD} -destkeystore ${KEYSTORE_FILE} \
  -srckeystore server.p12 -srcstoretype PKCS12 -srcstorepass ${PASSWORD} \
  -srcalias 1 -destalias broker-secure 2>/dev/null

# Add CA to server truststore
keytool -keystore ${TRUSTSTORE_FILE} -alias CARoot -import -file ca-cert.pem \
  -storepass ${PASSWORD} -noprompt 2>/dev/null

echo "Server keystore created: ${KEYSTORE_FILE}"
echo "Server truststore created: ${TRUSTSTORE_FILE}"

# =============================================================================
# Generate Client Certificate
# =============================================================================
echo ""
echo "=== Generating Client Certificate ==="

# Generate client key and CSR
openssl genrsa -out client-key.pem 2048 2>/dev/null
openssl req -new -key client-key.pem -out client-csr.pem \
  -subj "/C=US/ST=CA/L=San Francisco/O=FraudStream/OU=Security/CN=kafka-client" 2>/dev/null

# Sign with CA
openssl x509 -req -in client-csr.pem -CA ca-cert.pem -CAkey ca-key.pem \
  -CAcreateserial -out client-cert.pem -days ${VALIDITY_DAYS} 2>/dev/null

# Create PKCS12 bundle
openssl pkcs12 -export -in client-cert.pem -inkey client-key.pem \
  -certfile ca-cert.pem -out client.p12 -password pass:${PASSWORD} 2>/dev/null

echo "Client certificate generated: client-cert.pem"

# =============================================================================
# Create Client Keystore and Truststore
# =============================================================================
echo ""
echo "=== Creating Client Keystore (${CLIENT_KEYSTORE_FILE}) ==="

keytool -importkeystore \
  -deststorepass ${PASSWORD} -destkeypass ${PASSWORD} -destkeystore ${CLIENT_KEYSTORE_FILE} \
  -srckeystore client.p12 -srcstoretype PKCS12 -srcstorepass ${PASSWORD} \
  -srcalias 1 -destalias kafka-client 2>/dev/null

# Create client truststore with CA
keytool -keystore ${CLIENT_TRUSTSTORE_FILE} -alias CARoot -import -file ca-cert.pem \
  -storepass ${PASSWORD} -noprompt 2>/dev/null

echo "Client keystore created: ${CLIENT_KEYSTORE_FILE}"
echo "Client truststore created: ${CLIENT_TRUSTSTORE_FILE}"

# =============================================================================
# Create credential files for Docker secrets
# =============================================================================
echo ""
echo "=== Creating credential files ==="

echo "${PASSWORD}" > keystore_cred
echo "${PASSWORD}" > truststore_cred
echo "${PASSWORD}" > client_keystore_cred
echo "${PASSWORD}" > client_truststore_cred

# =============================================================================
# Verify keystores
# =============================================================================
echo ""
echo "=== Verifying Keystores ==="

echo "Server keystore contents:"
keytool -list -v -keystore ${KEYSTORE_FILE} -storepass ${PASSWORD} 2>/dev/null | grep -E "(Alias|Entry type|Owner)" || true

echo ""
echo "Server truststore contents:"
keytool -list -v -keystore ${TRUSTSTORE_FILE} -storepass ${PASSWORD} 2>/dev/null | grep -E "(Alias|Entry type)" || true

# =============================================================================
# Cleanup intermediate files
# =============================================================================
echo ""
echo "=== Cleaning up intermediate files ==="
rm -f *.pem *.csr *.srl *.p12

echo ""
echo "=== Certificate Generation Complete ==="
echo ""
echo "Generated files in ${CERTS_DIR}:"
echo "  - ${KEYSTORE_FILE} (server keystore)"
echo "  - ${TRUSTSTORE_FILE} (server truststore)"
echo "  - ${CLIENT_KEYSTORE_FILE} (client keystore)"
echo "  - ${CLIENT_TRUSTSTORE_FILE} (client truststore)"
echo "  - keystore_cred, truststore_cred (password files)"
echo ""
echo "Keystore/Truststore Password: ${PASSWORD}"
echo ""
echo "Next steps:"
echo "  1. Run: docker compose -f infra/docker-compose.security.yml up -d"
echo "  2. Test SASL_PLAINTEXT: kafka-console-producer --bootstrap-server localhost:9093 ..."
echo "  3. Test SASL_SSL: kafka-console-producer --bootstrap-server localhost:9094 ..."
