COMPOSE        := docker compose -f infra/docker-compose.yml
PYTHON         := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3.11)
TRINO          := docker exec fraudstream-trino trino
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
        flink-job flink-job-analytics \
        generate generate-suspicious simulate-alerts consume generate-dlq \
        analytics-counts analytics-join analytics-feast analytics-verify \
        analytics-up analytics-down \
        install test test-unit test-integration

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
	RULES_YAML_PATH=rules/rules.yaml \
	$(PYTHON) -m pipelines.processing.job \
	  --kafka-brokers localhost:9092 \
	  --input-topic txn.api \
	  --output-topic txn.enriched \
	  --geoip-db-path infra/geoip/GeoLite2-City.mmdb

## flink-job-analytics: run the enrichment job with Iceberg + Feast side-outputs enabled.
## Requires MinIO to be running (make infra-up). Writes to:
##   iceberg.default.enriched_transactions  (PyIceberg → MinIO)
##   iceberg.default.fraud_decisions        (PyIceberg → MinIO)
##   storage/feature_store/                 (Feast online store)
## Override credentials: MINIO_ACCESS_KEY=x MINIO_SECRET_KEY=y make flink-job-analytics

flink-job-analytics:
	AWS_ACCESS_KEY_ID=$(MINIO_ACCESS_KEY) \
	AWS_SECRET_ACCESS_KEY=$(MINIO_SECRET_KEY) \
	PYICEBERG_CATALOG__ICEBERG__URI=http://localhost:8181 \
	PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/ \
	PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://localhost:9000 \
	PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true \
	RULES_YAML_PATH=rules/rules.yaml \
	$(PYTHON) -m pipelines.processing.job \
	  --kafka-brokers localhost:9092 \
	  --input-topic txn.api \
	  --output-topic txn.enriched \
	  --geoip-db-path infra/geoip/GeoLite2-City.mmdb

# ── Analytics persistence verification ───────────────────────────────────
## analytics-counts: row counts for both Iceberg tables via Trino
analytics-counts:
	@echo "==> enriched_transactions"
	@$(TRINO) --execute "SELECT COUNT(*) AS rows FROM iceberg.default.enriched_transactions;"
	@echo "==> fraud_decisions"
	@$(TRINO) --execute "SELECT COUNT(*) AS rows FROM iceberg.default.fraud_decisions;"

## analytics-join: join both tables on transaction_id — show decisions with features
## LIMIT=20 make analytics-join   → show more rows
analytics-join:
	@$(TRINO) --execute " \
	  SELECT e.account_id, e.amount, e.vel_count_1h, \
	         d.decision, d.fraud_score, d.rule_triggers \
	  FROM iceberg.default.enriched_transactions e \
	  JOIN iceberg.default.fraud_decisions d \
	    ON e.transaction_id = d.transaction_id \
	  ORDER BY d.fraud_score DESC \
	  LIMIT $(or $(LIMIT),20);"

## analytics-feast: check Feast online store for a given account.
## ACCOUNT=acc-0007 make analytics-feast   → inspect a specific account
analytics-feast:
	@$(PYTHON) -c "from feast import FeatureStore; store = FeatureStore(repo_path='storage/feature_store'); rows = [{'account_id': '$(or $(ACCOUNT),acc-0001)'}]; feats = store.get_online_features(features=['velocity_features:vel_count_1m','velocity_features:vel_count_1h','velocity_features:vel_amount_24h'],entity_rows=rows).to_dict(); [print(f'{k}: {v}') for k, v in feats.items()]"

## analytics-verify: run all three checks in sequence (counts + join sample + feast)
analytics-verify: analytics-counts analytics-join analytics-feast

# ── Analytics tier lifecycle ──────────────────────────────────────────────
## trino-views: create or replace all analytics Trino views over Iceberg tables.
## Requires Core tier (Trino) to be running. Safe to re-run at any time.
trino-views:
	@echo "Creating Trino views..."
	@$(TRINO) --execute "$$(cat analytics/views/v_fraud_rate_daily.sql)"
	@$(TRINO) --execute "$$(cat analytics/views/v_rule_triggers.sql)"
	@$(TRINO) --execute "$$(cat analytics/views/v_model_versions.sql)"
	@$(TRINO) --execute "$$(cat analytics/views/v_transaction_audit.sql)"
	@echo "Trino views ready."

## analytics-up: start the Analytics tier (Trino + Streamlit) alongside Core.
## Requires Core tier to be running: make bootstrap first.
analytics-up: trino-views
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
	pip install -e ".[processing]"

# ── Tests ─────────────────────────────────────────────────────────────────

test-unit:
	pytest tests/unit/ --cov=pipelines/processing --cov=pipelines/scoring --cov-fail-under=80 -v

test-integration:
	pytest -m integration tests/integration/ -v

test: test-unit
