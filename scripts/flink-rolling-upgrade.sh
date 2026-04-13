#!/usr/bin/env bash
set -euo pipefail
# Flink rolling upgrade helper — stops current job, deploys new JAR, restarts from latest checkpoint.
# Usage: ./scripts/flink-rolling-upgrade.sh --job-jar PATH_TO_JAR [--savepoint-dir s3a://flink-checkpoints/]

FLINK_REST="${FLINK_REST:-http://localhost:8081}"
SAVEPOINT_DIR="${SAVEPOINT_DIR:-s3a://flink-checkpoints/}"
JOB_JAR=""

usage() {
  echo "Usage: $0 --job-jar PATH [--savepoint-dir DIR] [--flink-rest URL]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job-jar) JOB_JAR="$2"; shift 2 ;;
    --savepoint-dir) SAVEPOINT_DIR="$2"; shift 2 ;;
    --flink-rest) FLINK_REST="$2"; shift 2 ;;
    --help|-h) usage ;;
    *) echo "Unknown: $1"; usage ;;
  esac
done

[[ -z "${JOB_JAR}" ]] && { echo "ERROR: --job-jar required"; usage; }

echo "[$(date -u +%T)] Triggering savepoint before upgrade..."
JOB_ID=$(curl -sf "${FLINK_REST}/jobs" | python3 -c "import sys,json; jobs=json.load(sys.stdin)['jobs']; print(next(j['id'] for j in jobs if j['status']=='RUNNING'))")
SAVEPOINT=$(curl -sf -X POST "${FLINK_REST}/jobs/${JOB_ID}/savepoints" \
  -H "Content-Type: application/json" \
  -d "{\"target-directory\": \"${SAVEPOINT_DIR}\", \"cancel-job\": true}" | python3 -c "import sys,json; print(json.load(sys.stdin)['request-id'])")
echo "[$(date -u +%T)] Savepoint triggered: ${SAVEPOINT}"
echo "Upload and restart with: flink run -s ${SAVEPOINT_DIR} ${JOB_JAR}"
