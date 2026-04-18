FROM python:3.11.10-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi==0.136.0" \
    "uvicorn[standard]==0.34.3" \
    "pydantic==2.13.2" \
    "pybreaker==1.4.1" \
    "prometheus-client==0.25.0" \
    "pyyaml==6.0.3" \
    "redis==5.2.1" \
    "slowapi==0.1.9"

COPY . .

ENV PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/healthz')" || exit 1

# Default non-root user for production; override with user: directive in compose
# when bind-mounting host files that require a specific UID.
USER nobody

CMD ["uvicorn", "pipelines.scoring.management_api:app", "--host", "0.0.0.0", "--port", "8090"]
