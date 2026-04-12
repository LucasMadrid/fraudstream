# Multi-stage Dockerfile for the synthetic transaction load generator
# Context: repo root (.)
# Build: docker build -f infra/load-gen/Dockerfile .

# ============================================================================
# BUILDER STAGE
# ============================================================================
FROM python:3.11-slim AS builder

RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# ── Layer 1: external dependencies (slow, cached until pyproject.toml changes) ──
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefer-binary \
        "confluent-kafka[schema-registry]>=2.3" \
        "httpx>=0.27" \
        "fastavro>=1.9" \
        "opentelemetry-sdk>=1.24" \
        "opentelemetry-exporter-otlp-proto-http>=1.24" \
        "prometheus-client>=0.20"

# ── Layer 2: local package (fast — no external downloads) ──
COPY pipelines/ ./pipelines/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-build-isolation --no-deps .

# ============================================================================
# RUNTIME STAGE
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
COPY scripts/ /opt/app/scripts/

WORKDIR /opt/app

RUN useradd -r -s /bin/false appuser
USER appuser

CMD ["python3.11", "scripts/generate_transactions.py"]
