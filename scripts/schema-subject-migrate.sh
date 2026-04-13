#!/usr/bin/env bash
set -euo pipefail
# Schema Registry subject migration helper.
# Usage: ./scripts/schema-subject-migrate.sh --subject SUBJECT --schema-file FILE [--dry-run]

REGISTRY="${SCHEMA_REGISTRY_URL:-http://localhost:8081}"
SUBJECT=""
SCHEMA_FILE=""
DRY_RUN=false

usage() {
  echo "Usage: $0 --subject SUBJECT --schema-file FILE [--dry-run] [--registry URL]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subject) SUBJECT="$2"; shift 2 ;;
    --schema-file) SCHEMA_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    --help|-h) usage ;;
    *) echo "Unknown: $1"; usage ;;
  esac
done

[[ -z "${SUBJECT}" || -z "${SCHEMA_FILE}" ]] && { echo "ERROR: --subject and --schema-file required"; usage; }
[[ ! -f "${SCHEMA_FILE}" ]] && { echo "ERROR: Schema file not found: ${SCHEMA_FILE}"; exit 1; }

echo "Checking compatibility for subject: ${SUBJECT}"
COMPAT=$(curl -sf -X POST \
  "${REGISTRY}/compatibility/subjects/${SUBJECT}/versions/latest" \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d "{\"schema\": $(python3 -c "import json,sys; print(json.dumps(open('${SCHEMA_FILE}').read()))")}")
echo "Compatibility check: ${COMPAT}"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY RUN] Would register schema for subject: ${SUBJECT}"
  exit 0
fi

echo "Registering schema..."
curl -sf -X POST \
  "${REGISTRY}/subjects/${SUBJECT}/versions" \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d "{\"schema\": $(python3 -c "import json,sys; print(json.dumps(open('${SCHEMA_FILE}').read()))")}"
echo "Schema registered successfully."
