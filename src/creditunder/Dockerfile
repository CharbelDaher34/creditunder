# ── Stage 1: dependency installer ────────────────────────────────────────────
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests (cache-friendly layer)
COPY pyproject.toml uv.lock ./

# Install all runtime deps into /app/.venv, skip the project itself for now
RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.14-slim

# WeasyPrint system dependencies (Cairo, Pango, etc.)
# libgdk-pixbuf2.0-0 was split in Debian Trixie (python:3.14-slim base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpangocairo-1.0-0 \
    libcairo2 \
    libpango-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy pre-built venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY pyproject.toml uv.lock ./
COPY src/        /app/src/
COPY alembic/    /app/alembic/
COPY alembic.ini /app/alembic.ini
COPY mockups/    /app/mockups/

# Install the project package into the existing venv (deps already present)
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Entrypoint lives alongside this Dockerfile in src/creditunder/
COPY src/creditunder/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
