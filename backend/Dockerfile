# syntax=docker/dockerfile:1
# Base image pinned to an exact patch version. Digest pinning (AD-16) is NOT yet
# done — see PROGRESS.md "Known issues" and scripts/pin-digests.sh.
FROM python:3.12.15-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# curl is needed by HEALTHCHECK; nothing else is installed. `upgrade` pulls in Debian
# security fixes that landed after the pinned base was built (CI's Trivy gate fails on a
# fixable HIGH, e.g. libssl3 and libbrotli1; PROGRESS.md issue 126 is the same fix for the
# frontend's Alpine). The base is still not digest-pinned (AD-16).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# ---------------------------------------------------------------- dependencies
FROM base AS deps
COPY pyproject.toml uv.lock ./
# --frozen: install exactly what the lockfile pins, or fail (AD-16).
RUN uv sync --frozen --no-install-project --no-dev

# ---------------------------------------------------------------- development
FROM base AS development
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --all-groups
COPY . .
# /var/lib/dcp/targets is where the worker writes the Prometheus target file (M2.3).
# Created and owned here so the named volume mounted over it inherits that
# ownership on first use; otherwise it is root-owned and the non-root worker
# cannot write to it. /var/log/sample is the same trick for the sample log
# producer (M3.1); it exists in this development image only, never in production.
RUN useradd --create-home --uid 10001 dcp \
    && mkdir -p /var/lib/dcp/targets /var/log/sample \
    && chown -R dcp:dcp /app /opt/venv /var/lib/dcp /var/log/sample
USER dcp
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------- production
FROM base AS production
COPY --from=deps /opt/venv /opt/venv
COPY app ./app
# Migrations run as an explicit deploy step from this image, never from
# application startup (docs/DATABASE.md §16 item 9), so the revisions and the
# Alembic config have to ship with it. The readiness check also reads the head
# revision from here.
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN useradd --create-home --uid 10001 dcp \
    && mkdir -p /var/lib/dcp/targets \
    && chown -R dcp:dcp /app /var/lib/dcp
USER dcp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1
# No --reload, and workers are set by the orchestrator.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
