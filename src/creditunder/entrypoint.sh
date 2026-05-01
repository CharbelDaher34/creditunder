#!/bin/sh
set -e

# ── Wait for PostgreSQL ───────────────────────────────────────────────────────
echo "[entrypoint] Waiting for PostgreSQL at ${DATABASE_URL}..."
# Extract host:port from DATABASE_URL
# e.g. postgresql+asyncpg://user:pass@hostname:5432/db
DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|.*@([^:/]+).*|\1|')
DB_PORT=$(echo "$DATABASE_URL" | sed -E 's|.*:([0-9]+)/.*|\1|')
DB_PORT=${DB_PORT:-5432}

until python -c "
import socket, sys
try:
    s = socket.create_connection(('${DB_HOST}', ${DB_PORT}), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    echo "[entrypoint] PostgreSQL not ready, retrying in 2s..."
    sleep 2
done
echo "[entrypoint] PostgreSQL is ready."

# ── Wait for Kafka ────────────────────────────────────────────────────────────
echo "[entrypoint] Waiting for Kafka at ${KAFKA_BOOTSTRAP_SERVERS}..."
KAFKA_HOST=$(echo "$KAFKA_BOOTSTRAP_SERVERS" | cut -d: -f1)
KAFKA_PORT=$(echo "$KAFKA_BOOTSTRAP_SERVERS" | cut -d: -f2)
KAFKA_PORT=${KAFKA_PORT:-9092}

until python -c "
import socket, sys
try:
    s = socket.create_connection(('${KAFKA_HOST}', ${KAFKA_PORT}), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    echo "[entrypoint] Kafka not ready, retrying in 2s..."
    sleep 2
done
echo "[entrypoint] Kafka is ready."

# ── Run database migrations ───────────────────────────────────────────────────
echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head
echo "[entrypoint] Migrations complete."

# ── Start the processor ───────────────────────────────────────────────────────
echo "[entrypoint] Starting processor..."
exec python -m creditunder
