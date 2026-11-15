#!/bin/bash
set -euo pipefail

if [[ "${POSTGRES_ROLE:-primary}" != "primary" ]]; then
  exit 0
fi

echo "host replication ${REPLICATION_USER:-replicator} 0.0.0.0/0 scram-sha-256 # sentinel_replication" >> "$PGDATA/pg_hba.conf"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${REPLICATION_USER:-replicator}') THEN
    CREATE ROLE ${REPLICATION_USER:-replicator} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD:-sentinel}';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = 'standby1') THEN
    PERFORM pg_create_physical_replication_slot('standby1');
  END IF;
END
\$\$;
SQL
