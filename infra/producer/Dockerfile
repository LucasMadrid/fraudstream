# Multi-stage Dockerfile for the Transaction Producer API
# Context: repo root (.)
# Build: docker build -f infra/producer/Dockerfile .

# ============================================================================
# BUILDER STAGE — install Python deps into an isolated venv
# ============================================================================
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

RUN pip install --upgrade pip setuptools wheel

# Copy source — pyproject.toml first so dependency layer is cached independently
COPY pyproject.toml ./
COPY pipelines/ ./pipelines/

# Install base deps only (no processing/scoring/dev extras)
RUN pip install --no-cache-dir .

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

# Smoke-test: verify the producer package is importable
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3.11 -c "from pipelines.ingestion.api import producer" || exit 1

RUN useradd -r -s /bin/false appuser
USER appuser

CMD ["python3.11", "-m", "pipelines.ingestion.api.producer"]
