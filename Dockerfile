# OpenSpine — production container.
#
# Multi-stage:
#   - builder:  installs build deps and the project into a venv
#   - runtime:  copies the venv into a minimal image, drops privileges
#
# Build:    docker build -t openspine .
# Run API:  docker run --rm -p 8000:8000 --env-file .env openspine
# Worker:   docker run --rm --env-file .env openspine python -m openspine.workers.embedding

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# libpq is needed by asyncpg / psycopg at runtime; everything else stays out
# so the runtime image is small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. Container best practice; also avoids RLS bypass via
# `SECURITY DEFINER` functions running with elevated privileges.
RUN groupadd --system --gid 1001 openspine \
    && useradd --system --uid 1001 --gid openspine --create-home openspine

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=openspine:openspine alembic.ini ./
COPY --chown=openspine:openspine migrations ./migrations
COPY --chown=openspine:openspine src ./src

USER openspine

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/system/health || exit 1

# Default command runs the API. Override for the worker:
#   docker run … openspine python -m openspine.workers.embedding
CMD ["uvicorn", "openspine.main:app", "--host", "0.0.0.0", "--port", "8000"]
