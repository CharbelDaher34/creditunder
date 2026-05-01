# ── Stage 1: dependency installer ────────────────────────────────────────────
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

COPY src/creditunder/pyproject.toml src/creditunder/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpangocairo-1.0-0 \
    libcairo2 \
    libpango-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY src/creditunder/pyproject.toml src/creditunder/uv.lock ./
COPY src/        /app/src/
COPY alembic/    /app/alembic/
COPY alembic.ini /app/alembic.ini
COPY mockups/    /app/mockups/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src:/app"

COPY src/creditunder/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
