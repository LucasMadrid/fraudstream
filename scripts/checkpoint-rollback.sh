#!/usr/bin/env bash
set -euo pipefail
# Roll back a Flink job to a specific checkpoint or savepoint.
# Usage: ./scripts/checkpoint-rollback.sh --checkpoint-path PATH --job-jar JAR

FLINK_REST="${FLINK_REST:-http://localhost:8081}"
CHECKPOINT_PATH=""
JOB_JAR=""

usage() {
  echo "Usage: $0 --checkpoint-path PATH --job-jar JAR [--flink-rest URL]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --checkpoint-path) CHECKPOINT_PATH="$2"; shift 2 ;;
    --job-jar) JOB_JAR="$2"; shift 2 ;;
    --flink-rest) FLINK_REST="$2"; shift 2 ;;
    --help|-h) usage ;;
    *) echo "Unknown: $1"; usage ;;
  esac
done

[[ -z "${CHECKPOINT_PATH}" || -z "${JOB_JAR}" ]] && { echo "ERROR: --checkpoint-path and --job-jar required"; usage; }

echo "[$(date -u +%T)] Rolling back to checkpoint: ${CHECKPOINT_PATH}"
echo "Cancel any running job first if needed:"
echo "  curl -X POST ${FLINK_REST}/jobs/{job_id}/yarn-cancel"
echo ""
echo "Restart from checkpoint:"
echo "  flink run -s ${CHECKPOINT_PATH} ${JOB_JAR}"
echo ""
echo "Verify job status:"
echo "  curl -s ${FLINK_REST}/jobs | python3 -c \"import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))\""
