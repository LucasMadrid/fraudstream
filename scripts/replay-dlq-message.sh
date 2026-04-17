#!/usr/bin/env bash
#
# replay-dlq-message.sh
#
# Replays messages from a Kafka Dead Letter Queue (DLQ) topic to a target topic.
# Supports dry-run mode for safe inspection before committing to replay.
#
# Usage:
#   scripts/replay-dlq-message.sh [--dry-run] [--target-topic TOPIC] [--max-messages N] [--broker HOST:PORT] [--dlq-topic TOPIC]
#
# Examples:
#   # Dry-run: inspect first message
#   scripts/replay-dlq-message.sh --dry-run --max-messages 1
#
#   # Replay 50 messages from DLQ to main topic
#   scripts/replay-dlq-message.sh --max-messages 50 --target-topic txn.fraud.alerts
#
#   # Custom broker
#   scripts/replay-dlq-message.sh --broker kafka-prod-1:9092 --max-messages 10
#

set -euo pipefail

# Default values
DRY_RUN=false
TARGET_TOPIC="txn.fraud.alerts"
MAX_MESSAGES=1
BROKER="localhost:9092"
DLQ_TOPIC="txn.fraud.alerts.dlq"
KAFKA_BIN="${KAFKA_BIN:-/opt/kafka/bin}"

# Helper functions
usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Replay messages from Kafka Dead Letter Queue topic to a target topic.

OPTIONS:
  --dry-run           Print messages to stdout without replaying to target topic (default: false)
  --target-topic      Destination topic for replayed messages (default: txn.fraud.alerts)
  --max-messages      Maximum number of messages to consume from DLQ (default: 1)
  --broker            Kafka bootstrap server address (default: localhost:9092)
  --dlq-topic         Source DLQ topic name (default: txn.fraud.alerts.dlq)
  --help, -h          Show this help message

ENVIRONMENT VARIABLES:
  KAFKA_BIN           Path to Kafka bin directory (default: /opt/kafka/bin)

EXAMPLES:
  # Inspect first DLQ message without replaying
  $0 --dry-run

  # Replay 5 messages to main topic
  $0 --max-messages 5

  # Custom broker with batch replay
  $0 --broker kafka-prod-1:9092,kafka-prod-2:9092 --max-messages 100

EOF
  exit 0
}

log_info() {
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "[${timestamp}] $*"
}

log_error() {
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "[${timestamp}] ERROR: $*" >&2
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --target-topic)
      TARGET_TOPIC="$2"
      shift 2
      ;;
    --max-messages)
      MAX_MESSAGES="$2"
      shift 2
      ;;
    --broker)
      BROKER="$2"
      shift 2
      ;;
    --dlq-topic)
      DLQ_TOPIC="$2"
      shift 2
      ;;
    --help | -h)
      usage
      ;;
    *)
      log_error "Unknown option: $1"
      usage
      ;;
  esac
done

# Validate inputs
if ! [[ "$MAX_MESSAGES" =~ ^[0-9]+$ ]]; then
  log_error "MAX_MESSAGES must be a positive integer, got: $MAX_MESSAGES"
  exit 1
fi

if [[ $MAX_MESSAGES -le 0 ]]; then
  log_error "MAX_MESSAGES must be greater than 0, got: $MAX_MESSAGES"
  exit 1
fi

# Verify Kafka binaries exist
if [[ ! -d "$KAFKA_BIN" ]]; then
  log_error "Kafka bin directory not found: $KAFKA_BIN"
  echo "Set KAFKA_BIN environment variable to the correct path."
  exit 1
fi

if [[ ! -x "$KAFKA_BIN/kafka-console-consumer.sh" ]]; then
  log_error "kafka-console-consumer.sh not found at: $KAFKA_BIN/kafka-console-consumer.sh"
  exit 1
fi

if [[ ! -x "$KAFKA_BIN/kafka-console-producer.sh" ]] && [[ "$DRY_RUN" == "false" ]]; then
  log_error "kafka-console-producer.sh not found at: $KAFKA_BIN/kafka-console-producer.sh"
  exit 1
fi

# Main logic
log_info "Starting DLQ message replay"
log_info "  DLQ Topic: $DLQ_TOPIC"
log_info "  Target Topic: $TARGET_TOPIC"
log_info "  Broker: $BROKER"
log_info "  Max Messages: $MAX_MESSAGES"
log_info "  Dry Run: $DRY_RUN"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "DRY RUN MODE — Messages will NOT be replayed"
  log_info "Reading $MAX_MESSAGES message(s) from DLQ topic..."

  # Consume messages and print with [DRY RUN] prefix
  message_count=0
  "$KAFKA_BIN/kafka-console-consumer.sh" \
    --bootstrap-server "$BROKER" \
    --topic "$DLQ_TOPIC" \
    --max-messages "$MAX_MESSAGES" \
    --timeout-ms 5000 \
    --from-beginning 2>/dev/null | while IFS= read -r msg; do
    message_count=$((message_count + 1))
    echo "[DRY RUN] Message $message_count: $msg"
  done

  log_info "Dry run complete. To replay these messages, run without --dry-run"
  exit 0
fi

# Live replay mode
log_info "Replaying $MAX_MESSAGES message(s) from $DLQ_TOPIC to $TARGET_TOPIC..."

message_count=0
error_count=0

# Pipe DLQ messages to producer with error handling
if ! "$KAFKA_BIN/kafka-console-consumer.sh" \
  --bootstrap-server "$BROKER" \
  --topic "$DLQ_TOPIC" \
  --max-messages "$MAX_MESSAGES" \
  --timeout-ms 5000 \
  --from-beginning 2>/dev/null | \
  "$KAFKA_BIN/kafka-console-producer.sh" \
  --bootstrap-server "$BROKER" \
  --topic "$TARGET_TOPIC" 2>/tmp/replay-error.log; then
  error_count=$((error_count + 1))
  log_error "Producer failed. See /tmp/replay-error.log for details:"
  cat /tmp/replay-error.log >&2
  exit 1
fi

log_info "Replay complete. $MAX_MESSAGES message(s) sent to $TARGET_TOPIC"
log_info "Next steps:"
log_info "  1. Monitor DLQ lag: kafka-consumer-groups --bootstrap-server $BROKER --describe --group fraud-alerts-dlq-consumer"
log_info "  2. Verify database: SELECT COUNT(*) FROM fraud_alerts WHERE created_at > NOW() - INTERVAL '5 minutes';"
log_info "  3. Check Grafana 'Fraud Detection — Main' dashboard for DLQ Depth panel"

exit 0
