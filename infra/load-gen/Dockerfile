# Multi-stage Dockerfile for the synthetic transaction load generator
# Context: repo root (.)
# Build: docker build -f infra/load-gen/Dockerfile .

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

COPY pyproject.toml ./
COPY pipelines/ ./pipelines/

# Base deps cover confluent-kafka + fastavro needed by generate_transactions.py
RUN pip install --no-cache-dir .

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

# Default: run indefinitely with 500ms delay between transactions.
# Override CMD or pass args: docker run load-gen --count 100 --delay 200
CMD ["python3.11", "scripts/generate_transactions.py"]
