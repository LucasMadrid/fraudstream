COMPOSE          := docker compose -f infra/docker-compose.yml
COMPOSE_SECURITY := docker compose -f infra/docker-compose.security.yml
PYTHON           := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3.11)
export DOCKER_BUILDKIT := 1

## MinIO credentials for PyIceberg S3FileIO (override for production)
MINIO_ACCESS_KEY ?= minioadmin
MINIO_SECRET_KEY ?= minioadmin

## Kafka connector version compatible with PyFlink/Flink 2.x
KAFKA_CONNECTOR_VERSION := 4.0.1-2.0
KAFKA_CONNECTOR_URL     := https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/$(KAFKA_CONNECTOR_VERSION)/flink-sql-connector-kafka-$(KAFKA_CONNECTOR_VERSION).jar

.PHONY: infra-up infra-down infra-clean infra-ps infra-logs \
        infra-restart infra-restart-grafana infra-restart-prometheus \
        topics bootstrap update-geoip download-jars \
        flink-job flink-job-analytics iceberg-init \
        generate generate-suspicious simulate-alerts consume generate-dlq \
        analytics-counts analytics-join analytics-feast analytics-verify \
        analytics-up analytics-down \
        install test test-unit test-integration test-contract \
        infra-security-up infra-security-down infra-security-ps infra-security-logs \
        test-security test-performance \
        lint lint-fix format format-fix help

# ── Infrastructure lifecycle ──────────────────────────────────────────────

infra-up:
	$(COMPOSE) up -d
	$(COMPOSE) ps

infra-down:
	$(COMPOSE) down

infra-clean:
	$(COMPOSE) down -v

infra-ps:
	$(COMPOSE) ps

## SERVICE=broker make infra-logs   → tail a single service
## make infra-logs                  → tail all services
infra-logs:
	$(COMPOSE) logs -f $(SERVICE)

## Restart individual services (picks up config changes without full teardown)
## make infra-restart-grafana     → reload provisioning mounts
## make infra-restart-prometheus  → reload scrape config + alert rules
## make infra-restart             → both

infra-restart-grafana:
	$(COMPOSE) up -d --force-recreate grafana

infra-restart-prometheus:
	$(COMPOSE) up -d --force-recreate prometheus

infra-restart: infra-restart-prometheus infra-restart-grafana

# ── Security Test Environment (TB-001) ───────────────────────────────────
## Starts the security test environment for P0-001 Kafka auth/encryption testing.
## Includes SASL/SCRAM authentication and TLS encryption.
## Requires: infra/docker-compose.security.yml

infra-security-up:
	@echo "Starting security test environment (Kafka SASL/TLS)..."
	$(COMPOSE_SECURITY) up -d
	@echo "Waiting for Kafka to be ready (this may take 30-60 seconds)..."
	@sleep 10
	$(COMPOSE_SECURITY) ps
	@echo ""
	@echo "Security environment ready:"
	@echo "  PLAINTEXT:      localhost:9092"
	@echo "  SASL_PLAINTEXT: localhost:9093 (SCRAM-SHA-256)"
	@echo "  SASL_SSL:       localhost:9094 (SCRAM-SHA-256 + TLS)"
	@echo "  Kafka UI:       http://localhost:8080"
	@echo "  Schema Registry: http://localhost:8081"
	@echo ""
	@echo "Test users: admin/admin-secret, producer/producer-secret, consumer/consumer-secret"

infra-security-down:
	$(COMPOSE_SECURITY) down

infra-security-ps:
	$(COMPOSE_SECURITY) ps

## SERVICE=broker-secure make infra-security-logs → tail a single service
## make infra-security-logs → tail all services
infra-security-logs:
	$(COMPOSE_SECURITY) logs -f $(SERVICE)

# ── Kafka topics + Schema Registry ───────────────────────────────────────

topics:
	@echo "Waiting for Kafka broker..."
	@until docker exec broker kafka-topics --bootstrap-server localhost:9092 --list >/dev/null 2>&1; do \
	    printf '.'; sleep 2; \
	done
	@echo " ready."
	bash infra/kafka/topics.sh

# ── Flink connector JARs ─────────────────────────────────────────────────
## Downloads the Kafka connector JAR compatible with PyFlink 2.x and removes
## the incompatible 1.19 JAR bundled by the pip package.
## Re-run after upgrading pyflink.

download-jars:
	@echo "Installing Kafka connector JAR for PyFlink 2.x..."
	@$(PYTHON) -c "\
import urllib.request, os, sys, pyflink as _p; \
lib  = os.path.join(os.path.dirname(_p.__file__), 'lib'); \
jar  = 'flink-sql-connector-kafka-$(KAFKA_CONNECTOR_VERSION).jar'; \
old  = os.path.join(lib, 'flink-sql-connector-kafka-3.3.0-1.19.jar'); \
dest = os.path.join(lib, jar); \
(os.rename(old, old+'.bak') if os.path.exists(old) and not os.path.exists(old+'.bak') else None) or True; \
print('  Already present: ' + jar) or sys.exit(0) if os.path.exists(dest) else None; \
print('  Downloading ' + jar + ' ...'); \
urllib.request.urlretrieve('$(KAFKA_CONNECTOR_URL)', dest); \
print('  Done:', os.path.getsize(dest), 'bytes'); \
"
	@echo "Kafka connector JAR ready."

# ── One-shot dev bootstrap ────────────────────────────────────────────────
## Starts infra, waits for health, creates topics and registers all schemas.
## Run once after cloning or after make infra-clean.

bootstrap: download-jars infra-up topics
	@echo ""
	@echo "Bootstrap complete. Next steps:"
	@echo "  make update-geoip   (if infra/geoip/GeoLite2-City.mmdb is missing)"
	@echo "  make flink-job      (dedicated terminal)"
	@echo "  make generate       (another terminal)"

# ── GeoIP database ────────────────────────────────────────────────────────
## Requires MAXMIND_LICENCE_KEY env var (free at maxmind.com).
## Re-run after make infra-clean or after cloning.

update-geoip:
	@if [ -z "$$MAXMIND_LICENCE_KEY" ]; then \
	    echo "Error: MAXMIND_LICENCE_KEY is not set. Get a free key at https://www.maxmind.com/en/geolite2/signup"; \
	    exit 1; \
	fi
	@echo "Downloading GeoLite2-City database..."
	@tmpdir=$$(mktemp -d) && \
	  curl -sL \
	    "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-City&license_key=$${MAXMIND_LICENCE_KEY}&suffix=tar.gz" \
	    | tar -xz -C $$tmpdir && \
	  find $$tmpdir -name "GeoLite2-City.mmdb" -exec mv {} infra/geoip/GeoLite2-City.mmdb \; && \
	  rm -rf $$tmpdir
	@echo "GeoLite2-City.mmdb downloaded to infra/geoip/"
	@ls -lh infra/geoip/GeoLite2-City.mmdb

# ── Flink enrichment job ─────────────────────────────────────────────────

flink-job:
	AWS_ACCESS_KEY_ID=$(MINIO_ACCESS_KEY) \
	AWS_SECRET_ACCESS_KEY=$(MINIO_SECRET_KEY) \
	PYICEBERG_CATALOG__ICEBERG__URI=http://localhost:8181 \
	PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/ \
	PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://localhost:9000 \
	PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true \
	RULES_YAML_PATH=$(PWD)/rules/rules.yaml \
	$(PYTHON) -m pipelines.processing.job \
	  --kafka-brokers localhost:9092 \
	  --input-topic txn.api \
	  --output-topic txn.enriched \
	  --geoip-db-path $(PWD)/infra/geoip/GeoLite2-City.mmdb

## flink-job-analytics: run the enrichment job with Iceberg + Feast side-outputs enabled.
## Requires MinIO to be running (make infra-up). Writes to:
##   iceberg.default.enriched_transactions  (PyIceberg → MinIO)
##   iceberg.default.fraud_decisions        (PyIceberg → MinIO)
##   storage/feature_store/                 (Feast online store)
## Override credentials: MINIO_ACCESS_KEY=x MINIO_SECRET_KEY=y make flink-job-analytics

flink-job-analytics: iceberg-init
	AWS_ACCESS_KEY_ID=$(MINIO_ACCESS_KEY) \
	AWS_SECRET_ACCESS_KEY=$(MINIO_SECRET_KEY) \
	PYICEBERG_CATALOG__ICEBERG__URI=http://localhost:8181 \
	PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/ \
	PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://localhost:9000 \
	PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true \
	RULES_YAML_PATH=$(PWD)/rules/rules.yaml \
	$(PYTHON) -m pipelines.processing.job \
	  --kafka-brokers localhost:9092 \
	  --input-topic txn.api \
	  --output-topic txn.enriched \
	  --geoip-db-path $(PWD)/infra/geoip/GeoLite2-City.mmdb

## iceberg-init: Create Iceberg tables required by flink-job-analytics.
## Creates default.enriched_transactions and default.fraud_decisions tables
## using PyIceberg. Idempotent - safe to run multiple times.
## Requires: iceberg-rest service running (make infra-up)

iceberg-init:
	@echo "Initializing Iceberg tables..."
	@AWS_ACCESS_KEY_ID=$(MINIO_ACCESS_KEY) \
	AWS_SECRET_ACCESS_KEY=$(MINIO_SECRET_KEY) \
	PYICEBERG_CATALOG__ICEBERG__URI=http://localhost:8181 \
	PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/ \
	PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://localhost:9000 \
	PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true \
	$(PYTHON) scripts/iceberg_init.py

# ── Analytics persistence verification ───────────────────────────────────
## analytics-counts: row counts for both Iceberg tables via DuckDB/PyIceberg
analytics-counts:
	@echo "==> enriched_transactions"
	@$(PYTHON) -c "from analytics.queries.iceberg_reader import load_table; t = load_table('enriched_transactions'); print(f'  rows: {len(t.scan().to_arrow())}')" 2>/dev/null || echo "  (table not found or Iceberg not running)"
	@echo "==> fraud_decisions"
	@$(PYTHON) -c "from analytics.queries.iceberg_reader import load_table; t = load_table('fraud_decisions'); print(f'  rows: {len(t.scan().to_arrow())}')" 2>/dev/null || echo "  (table not found or Iceberg not running)"

## analytics-join: join both tables on transaction_id via DuckDB
analytics-join:
	@$(PYTHON) -c "\
from analytics.queries.duckdb_runner import DuckDBQueryRunner; \
runner = DuckDBQueryRunner(); \
df = runner.execute('SELECT e.account_id, e.amount, d.decision, d.fraud_score FROM enriched e JOIN decisions d ON e.transaction_id = d.transaction_id ORDER BY d.fraud_score DESC LIMIT $(or $(LIMIT),20)'); \
print(df.to_string()) if df is not None and len(df) > 0 else print('  (no data or tables not found)') \
" 2>/dev/null || echo "  (DuckDB query failed — ensure Iceberg is running)"

## analytics-feast: check Feast online store for a given account.
## ACCOUNT=acc-0007 make analytics-feast   → inspect a specific account
analytics-feast:
	@$(PYTHON) -c "from feast import FeatureStore; store = FeatureStore(repo_path='storage/feature_store'); rows = [{'account_id': '$(or $(ACCOUNT),acc-0001)'}]; feats = store.get_online_features(features=['velocity_features:vel_count_1m','velocity_features:vel_count_1h','velocity_features:vel_amount_24h'],entity_rows=rows).to_dict(); [print(f'{k}: {v}') for k, v in feats.items()]"

## analytics-verify: run all three checks in sequence (counts + join sample + feast)
analytics-verify: analytics-counts analytics-join analytics-feast

## analytics-up: start the Analytics tier (Streamlit + DuckDB) alongside Core.
## Requires Core tier to be running: make bootstrap first.
analytics-up:
	docker compose -f infra/docker-compose.yml --profile analytics up -d --build streamlit
	@echo "Streamlit: http://localhost:8501"
	@echo "Metrics:   http://localhost:8004/metrics"

## analytics-down: stop the Analytics tier only; Core tier remains running.
analytics-down:
	docker compose -f infra/docker-compose.yml --profile analytics stop streamlit
	docker compose -f infra/docker-compose.yml --profile analytics rm -f streamlit

# ── Data generation ──────────────────────────────────────────────────────
## Runs forever by default (COUNT=0). Override: COUNT=50 DELAY=200 make generate
## SUSPICIOUS_RATE=0 make generate   → disable suspicious injection
## Ctrl+C to stop

generate:
	$(PYTHON) scripts/generate_transactions.py \
	  --count $(or $(COUNT),0) \
	  --delay $(or $(DELAY),500) \
	  --suspicious-rate $(or $(SUSPICIOUS_RATE),0.25)

## Inject only suspicious transactions forever (velocity_burst + high_amount + hosting_ip)
## Ctrl+C to stop   |   COUNT=100 DELAY=150 make generate-suspicious

generate-suspicious:
	$(PYTHON) scripts/generate_transactions.py \
	  --count $(or $(COUNT),0) \
	  --delay $(or $(DELAY),300) \
	  --suspicious-rate 1.0

## Simulate alert storms for Grafana/Prometheus validation — runs forever.
## Loops 3 waves of suspicious traffic indefinitely:
##   Wave 1 — velocity burst (100 txns × 50ms)  → triggers VEL-001
##   Wave 2 — high-amount accumulation (50 txns × 200ms) → triggers VEL-002
##   Wave 3 — mixed suspicious (60 txns × 100ms) → keeps all counters climbing
## Ctrl+C to stop.   Watch: http://localhost:9090 and http://localhost:3000

simulate-alerts:
	@echo "==> Continuous alert simulation started (Ctrl+C to stop)"
	@wave=1; while true; do \
	  echo "==> [wave $$wave] Wave 1: velocity burst (100 txns, 50 ms)"; \
	  $(PYTHON) scripts/generate_transactions.py --count 100 --delay 50 --suspicious-rate 1.0; \
	  echo "==> [wave $$wave] Wave 2: high-amount accumulation (50 txns, 200 ms)"; \
	  $(PYTHON) scripts/generate_transactions.py --count 50 --delay 200 --suspicious-rate 1.0; \
	  echo "==> [wave $$wave] Wave 3: mixed traffic (60 txns, 100 ms)"; \
	  $(PYTHON) scripts/generate_transactions.py --count 60 --delay 100 --suspicious-rate 0.8; \
	  echo "==> [wave $$wave] Complete. Starting next wave..."; \
	  wave=$$((wave + 1)); \
	done

## Produce N synthetic DLQ messages to txn.api.dlq for DLQ Inspector testing.
## COUNT=20 make generate-dlq   → change message count (default: 10)

generate-dlq:
	@echo "==> Producing $(or $(COUNT),10) DLQ noise messages to txn.api.dlq"
	$(PYTHON) scripts/generate_dlq.py --count $(or $(COUNT),10)

## Tail txn.enriched without producing
consume:
	$(PYTHON) scripts/generate_transactions.py --consume-only

# ── Python dependencies ──────────────────────────────────────────────────

install:
	pip install -e ".[dev,processing,scoring]"

# ── Tests ─────────────────────────────────────────────────────────────────

test-unit:
	pytest tests/unit/ --cov=pipelines/processing --cov=pipelines/scoring --cov-fail-under=80 -v

test-integration:
	pytest -m integration tests/integration/ -v

## CHB-006: Interface contract tests - Avro/Iceberg schema alignment
test-contract:
	@echo "Running CHB-006 interface contract tests..."
	pytest tests/contract/ tests/contracts/ -v

test: test-unit test-contract

# ── Security Tests (TB-001) ────────────────────────────────────────────────
## Run TB-001 Kafka SASL/SCRAM security tests
## Requires: Security environment running (make infra-security-up)

test-security:
	@echo "Running TB-001 Kafka SASL/SCRAM security tests..."
	@echo "Note: Requires security environment running on localhost:9093"
	pytest tests/security/ -v -m "not skip"

# ── Performance Tests (TB-003) ───────────────────────────────────────────────
## Run TB-003 performance benchmarks
## Run with: make test-performance
## Run with slow tests: make test-performance SLOW=1

test-performance:
	@echo "Running TB-003 performance benchmarks..."
	@if [ "$(SLOW)" = "1" ]; then \
		echo "Including slow tests..."; \
		pytest tests/performance/ -v; \
	else \
		echo "Skipping slow tests (run with SLOW=1 to include)"; \
		pytest tests/performance/ -v -m "not slow"; \
	fi

# ── Code Quality (Lint/Format) ───────────────────────────────────────────────
## Check code style with ruff (configured in pyproject.toml)

lint:
	ruff check .

## Auto-fix code style issues where possible

lint-fix:
	ruff check --fix .

## Check code formatting

format:
	ruff format --check .

## Apply code formatting

format-fix:
	ruff format .

# ── Help ───────────────────────────────────────────────────────────────────
## Show this help message

help:
	@echo "FraudStream Makefile Targets"
	@echo "============================="
	@echo ""
	@echo "Infrastructure:"
	@echo "  make infra-up              Start core infrastructure"
	@echo "  make infra-down            Stop core infrastructure"
	@echo "  make infra-clean           Stop and remove volumes"
	@echo "  make infra-logs            Tail logs (SERVICE=x for specific service)"
	@echo "  make infra-security-up     Start security test environment (TB-001)"
	@echo "  make infra-security-down   Stop security test environment"
	@echo ""
	@echo "Development:"
	@echo "  make bootstrap             Full setup (download-jars + infra-up + topics)"
	@echo "  make download-jars         Download Flink connector JARs"
	@echo "  make update-geoip          Download MaxMind GeoIP database"
	@echo "  make install               Install Python dependencies"
	@echo ""
	@echo "Flink Jobs:"
	@echo "  make flink-job             Run enrichment job"
	@echo "  make flink-job-analytics   Run enrichment job with Iceberg/Feast"
	@echo "  make iceberg-init          Initialize Iceberg tables (run automatically)"
	@echo ""
	@echo "Data Generation:"
	@echo "  make generate              Generate transactions (COUNT=0 for infinite)"
	@echo "  make generate-suspicious   Generate only suspicious transactions"
	@echo "  make simulate-alerts       Run alert storm simulation"
	@echo "  make consume               Tail txn.enriched topic"
	@echo ""
	@echo "Analytics:"
	@echo "  make analytics-up          Start Streamlit dashboard"
	@echo "  make analytics-down        Stop Streamlit dashboard"
	@echo "  make analytics-verify      Run all analytics checks"
	@echo ""
	@echo "Testing:"
	@echo "  make test                  Run all tests (unit + contract)"
	@echo "  make test-unit             Run unit tests only"
	@echo "  make test-integration      Run integration tests"
	@echo "  make test-contract         Run CHB-006 contract tests"
	@echo "  make test-security         Run TB-001 security tests"
	@echo "  make test-performance      Run TB-003 performance benchmarks"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint                  Check code style"
	@echo "  make lint-fix              Auto-fix code style issues"
	@echo "  make format                Check code formatting"
	@echo "  make format-fix            Apply code formatting"
	@echo ""
	@echo "Environment Variables:"
	@echo "  COUNT=100                  Number of records to generate"
	@echo "  DELAY=500                  Delay between records (ms)"
	@echo "  SUSPICIOUS_RATE=0.25       Rate of suspicious transactions"
	@echo "  SERVICE=broker             Service name for logs"
	@echo "  MINIO_ACCESS_KEY=x         MinIO credentials"
	@echo "  MINIO_SECRET_KEY=x         MinIO credentials"
