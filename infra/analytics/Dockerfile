# Multi-stage Dockerfile for the Streamlit Analytics Dashboard
# Context: repo root (.)
# Build: docker build -f infra/analytics/Dockerfile .

# ============================================================================
# BUILDER STAGE
# ============================================================================
FROM python:3.11-slim AS builder

RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefer-binary \
        "confluent-kafka[schema-registry]>=2.3" \
        "fastavro>=1.9" \
        "prometheus-client>=0.20" \
        "pybreaker==1.4.1" \
        "streamlit>=1.35" \
        "duckdb>=1.0" \
        "pandas>=2.0" \
        "pyiceberg[pyarrow,s3fs]>=0.11"

COPY analytics/ ./analytics/
COPY pipelines/ ./pipelines/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-build-isolation --no-deps .

# ============================================================================
# RUNTIME STAGE — minimal final image, no build tools
# ============================================================================
FROM python:3.11-slim

RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv
COPY analytics/ /opt/app/analytics/
COPY pipelines/ /opt/app/pipelines/

WORKDIR /opt/app

EXPOSE 8501 8004

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

RUN useradd -r -s /bin/false appuser
USER appuser

CMD ["streamlit", "run", "analytics/app/Home.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
