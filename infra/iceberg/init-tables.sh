#!/bin/bash
set -euo pipefail

CATALOG_URI="${ICEBERG_CATALOG_URI:-http://localhost:8181}"
TRINO_HOST="${TRINO_HOST:-trino}"
TRINO_PORT="${TRINO_PORT:-8080}"
SCHEMAS_DIR="${SCHEMAS_DIR:-/opt/iceberg/schemas}"

echo "Waiting for Iceberg REST catalog at ${CATALOG_URI}..."
catalog_ready=0
for i in $(seq 1 30); do
  if curl -sf "${CATALOG_URI}/v1/config" > /dev/null 2>&1; then
    echo "Catalog ready after ${i} attempts."
    catalog_ready=1
    break
  fi
  echo "  attempt ${i}/30..."
  sleep 3
done
if [ "$catalog_ready" -eq 0 ]; then
  echo "ERROR: Iceberg catalog not ready after 30 attempts (90s). Aborting." >&2
  exit 1
fi

echo "Applying Iceberg DDL via Trino..."
for sql_file in "${SCHEMAS_DIR}/enriched_transactions.sql" "${SCHEMAS_DIR}/fraud_decisions.sql"; do
  if [ -f "${sql_file}" ]; then
    echo "Applying: ${sql_file}"
    trino --server "${TRINO_HOST}:${TRINO_PORT}" \
          --catalog iceberg \
          --schema default \
          --file "${sql_file}" \
          --output-format NULL \
      2>&1 | { grep -v "^Query " || true; }
    echo "Done: ${sql_file}"
  else
    echo "WARNING: DDL file not found: ${sql_file}"
  fi
done

echo "Iceberg table initialization complete."

# Optional daily deduplication compaction
RUN_COMPACTION="${RUN_COMPACTION:-}"

if [ -z "$RUN_COMPACTION" ]; then
  echo "Skipping compaction (RUN_COMPACTION not set)"
else
  echo "Running daily deduplication compaction..."

  # Deduplicate enriched_transactions (keep latest enrichment_time per transaction_id)
  echo "Deduplicating enriched_transactions..."
  trino --server "${TRINO_HOST}:${TRINO_PORT}" \
        --catalog iceberg \
        --schema default \
        --execute "DELETE FROM iceberg.default.enriched_transactions
WHERE (transaction_id, enrichment_time) NOT IN (
    SELECT transaction_id, MAX(enrichment_time)
    FROM iceberg.default.enriched_transactions
    GROUP BY transaction_id
);" \
        --output-format NULL \
    2>&1 | grep -v "^Query " || true
  echo "Done deduplicating enriched_transactions"

  # Deduplicate fraud_decisions (keep latest decision_time_ms per transaction_id)
  echo "Deduplicating fraud_decisions..."
  trino --server "${TRINO_HOST}:${TRINO_PORT}" \
        --catalog iceberg \
        --schema default \
        --execute "DELETE FROM iceberg.default.fraud_decisions
WHERE (transaction_id, decision_time_ms) NOT IN (
    SELECT transaction_id, MAX(decision_time_ms)
    FROM iceberg.default.fraud_decisions
    GROUP BY transaction_id
);" \
        --output-format NULL \
    2>&1 | grep -v "^Query " || true
  echo "Done deduplicating fraud_decisions"

  echo "Compaction complete."
fi
