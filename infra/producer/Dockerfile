# Multi-stage Dockerfile for the Transaction Producer API
# Context: repo root (.)
# Build: docker build -f infra/producer/Dockerfile .

# ============================================================================
# BUILDER STAGE
# ============================================================================
FROM python:3.11-slim AS builder

RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# ── Layer 1: external dependencies (slow, but cached until pyproject.toml changes) ──
# --prefer-binary forces prebuilt wheels — avoids compiling confluent-kafka/librdkafka from source
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefer-binary \
        "confluent-kafka[schema-registry]>=2.3" \
        "httpx>=0.27" \
        "fastavro>=1.9" \
        "opentelemetry-sdk>=1.24" \
        "opentelemetry-exporter-otlp-proto-http>=1.24" \
        "prometheus-client>=0.20"

# ── Layer 2: local package (fast — no external downloads, just builds the local wheel) ──
COPY pipelines/ ./pipelines/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-build-isolation --no-deps .

# ============================================================================
# RUNTIME STAGE — minimal final image, no build tools
# ============================================================================
FROM python:3.11-slim

RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv
COPY pipelines/ /opt/app/pipelines/

WORKDIR /opt/app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3.11 -c "from pipelines.ingestion.api import producer" || exit 1

RUN useradd -r -s /bin/false appuser
USER appuser

CMD ["python3.11", "-m", "pipelines.ingestion.api.producer"]
