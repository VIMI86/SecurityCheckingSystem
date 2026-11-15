#!/bin/bash
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
REPLICATION_USER="${REPLICATION_USER:-replicator}"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-sentinel}"
PRIMARY_HOST="${PRIMARY_HOST:-postgres}"

ensure_hba() {
  local file="$PGDATA/pg_hba.conf"
  if [[ -f "$file" ]] && ! grep -q sentinel_replication "$file"; then
    echo "host replication ${REPLICATION_USER} 0.0.0.0/0 scram-sha-256 # sentinel_replication" >> "$file"
  fi
}

ensure_role() {
  local attempt
  for attempt in $(seq 1 60); do
    if pg_isready -q -U "${POSTGRES_USER:-sentinel}" -d "${POSTGRES_DB:-sentinel}"; then
      psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-sentinel}" -d "${POSTGRES_DB:-sentinel}" <<SQL && return 0
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${REPLICATION_USER}') THEN
    CREATE ROLE ${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = 'standby1') THEN
    PERFORM pg_create_physical_replication_slot('standby1');
  END IF;
END
\$\$;
SQL
    fi
    sleep 1
  done
  echo "роль репликации не создана" >&2
  return 1
}

if [[ "${POSTGRES_ROLE:-primary}" == "standby" ]]; then
  if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    install -d -o postgres -g postgres "$PGDATA"
    echo "снимаю базовую копию с ${PRIMARY_HOST}"
    until gosu postgres pg_basebackup \
      -d "host=${PRIMARY_HOST} port=5432 user=${REPLICATION_USER} password=${REPLICATION_PASSWORD} application_name=standby1" \
      -D "$PGDATA" -Fp -Xs -P -R -S standby1
    do
      find "$PGDATA" -mindepth 1 -delete
      echo "primary ещё не готов, повтор через 2с"
      sleep 2
    done
  fi
  exec docker-entrypoint.sh postgres -c hot_standby=on
fi

if [[ -s "$PGDATA/PG_VERSION" ]]; then
  ensure_hba
fi

docker-entrypoint.sh postgres \
  -c wal_level=replica \
  -c hot_standby=on \
  -c max_wal_senders=10 \
  -c max_replication_slots=10 \
  -c hot_standby_feedback=on \
  -c wal_keep_size=64 &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true; wait "$child"' TERM INT
ensure_role || true
wait "$child"
