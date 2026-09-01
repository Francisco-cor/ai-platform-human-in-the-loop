# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -e .

# --------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="procurement-platform" \
      org.opencontainers.image.description="Enterprise Agentic AI Platform — Procurement HITL" \
      org.opencontainers.image.version="0.1.0"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -m app -d /home/app \
    && mkdir -p /app && chown -R app:app /app

COPY --from=builder /wheels /wheels
COPY --from=builder /app/pyproject.toml /app/README.md ./
COPY --from=builder /app/src ./src
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini ./alembic.ini

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-index --find-links=/wheels procurement-platform \
    && rm -rf /wheels \
    && chown -R app:app /app

USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "procurement_platform.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
