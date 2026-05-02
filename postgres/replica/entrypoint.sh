#!/bin/bash
# Entrypoint for the PostgreSQL read replica container.
#
# On first start (empty PGDATA) it waits for the primary to be ready, then
# runs pg_basebackup to clone it. The -R flag automatically creates
# standby.signal and writes primary_conninfo into postgresql.auto.conf, so
# Postgres enters streaming standby mode on the very next start.
#
# On subsequent starts PGDATA is already populated so pg_basebackup is skipped
# and Postgres resumes streaming from where it left off.
set -e

PRIMARY_HOST="${PRIMARY_HOST:-postgres}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPLICATION_USER="${REPLICATION_USER:-replicator}"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-replicator_secret}"

if [ -z "$(ls -A "$PGDATA" 2>/dev/null)" ]; then
    echo "[replica] PGDATA is empty — cloning from primary ($PRIMARY_HOST:$PRIMARY_PORT)..."

    # Wait until the primary is accepting connections.
    until pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPLICATION_USER" -q; do
        echo "[replica] Waiting for primary..."
        sleep 2
    done

    # Clone the primary.
    # -R  : write standby.signal + primary_conninfo (enters standby mode on next start)
    # -Xs : stream WAL during the base backup (avoids needing wal_keep_size to cover the backup window)
    # -P  : progress reporting
    PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
        -h "$PRIMARY_HOST" \
        -p "$PRIMARY_PORT" \
        -U "$REPLICATION_USER" \
        -D "$PGDATA" \
        -R -Xs -P

    # Set correct permissions — postgres refuses to start if PGDATA is group/world readable.
    chmod 0700 "$PGDATA"

    echo "[replica] Clone complete. Replica will stream from $PRIMARY_HOST."
else
    echo "[replica] PGDATA already populated — resuming streaming replication."
fi

# Hand off to the standard postgres entrypoint.
exec docker-entrypoint.sh postgres
