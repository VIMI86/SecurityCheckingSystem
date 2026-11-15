#!/usr/bin/env bash
# Логический backup текущего primary и восстановление в отдельный экземпляр.
# Том основной базы не удаляется и не перезаписывается.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p backups
dump="$ROOT/backups/sentinel.dump"
source_service="${POSTGRES_SERVICE:-postgres}"

echo "дамп из ${source_service}"
docker compose exec -T "$source_service" pg_dump -U sentinel -Fc sentinel > "$dump"
echo "дамп: $dump ($(wc -c < "$dump") байт)"

docker compose --profile restore up -d postgres-restore
for _ in $(seq 1 30); do
  if docker compose exec -T postgres-restore pg_isready -U sentinel -d sentinel >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker compose exec -T postgres-restore psql -U sentinel -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'sentinel' AND pid <> pg_backend_pid();" >/dev/null || true
docker compose exec -T postgres-restore psql -U sentinel -d postgres -c "DROP DATABASE IF EXISTS sentinel;"
docker compose exec -T postgres-restore psql -U sentinel -d postgres -c "CREATE DATABASE sentinel OWNER sentinel;"
docker compose exec -T postgres-restore pg_restore -U sentinel -d sentinel --no-owner < "$dump"

echo "==== восстановленная база ===="
docker compose exec -T postgres-restore psql -U sentinel -d sentinel -c "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status;"
