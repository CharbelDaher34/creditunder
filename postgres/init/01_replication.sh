#!/bin/bash
# Runs inside the primary container on first initialisation only
# (Docker mounts /docker-entrypoint-initdb.d/ and executes scripts there).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator_secret';
EOSQL

# Allow the replicator user to connect for replication from anywhere in the
# Docker network. Append after the default rules so the existing app user
# rules are not disturbed.
echo "host replication replicator all md5" >> "$PGDATA/pg_hba.conf"

# Reload pg_hba.conf so the new rule takes effect without a restart.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT pg_reload_conf();
EOSQL

echo "[replication-init] Replication user and pg_hba rule created."
