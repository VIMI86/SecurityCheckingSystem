#!/usr/bin/env bash
# Учебное переключение PostgreSQL: остановить primary, продвинуть standby, перевести API.
# Том остановленного primary не удаляется. Обратная репликация автоматически не строится.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
marker="11111111-1111-4111-8111-111111111111"

echo "маркер на primary"
docker compose --profile legacy-primary exec -T postgres psql -U sentinel -d sentinel -c \
  "INSERT INTO tasks (id, event_type, payload, status) VALUES ('${marker}', 'failover_marker', '{\"step\":\"before\"}', 'done') ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = now();"

echo "жду копию на standby"
copied=""
for _ in $(seq 1 30); do
  copied="$(docker compose exec -T postgres-standby psql -U sentinel -d sentinel -tAc "SELECT event_type FROM tasks WHERE id = '${marker}';" || true)"
  if [[ "$copied" == "failover_marker" ]]; then
    echo "standby видит маркер"
    break
  fi
  sleep 1
done
if [[ "$copied" != "failover_marker" ]]; then
  echo "маркер не дошёл до standby" >&2
  exit 1
fi

started=$(date +%s)
docker compose --profile legacy-primary stop postgres
docker compose exec -T postgres-standby psql -U sentinel -d sentinel -c "SELECT pg_promote();"
recovery="t"
for _ in $(seq 1 30); do
  recovery="$(docker compose exec -T postgres-standby psql -U sentinel -d sentinel -tAc "SELECT pg_is_in_recovery();")"
  [[ "$recovery" == "f" ]] && break
  sleep 1
done
if [[ "$recovery" != "f" ]]; then
  echo "standby не вышел из восстановления" >&2
  exit 1
fi

touch .env
if grep -q '^POSTGRES_HOST=' .env; then
  sed -i 's/^POSTGRES_HOST=.*/POSTGRES_HOST=postgres-standby/' .env
else
  printf '\nPOSTGRES_HOST=postgres-standby\n' >> .env
fi
docker compose up -d --no-deps --force-recreate api orchestrator
echo "обновляю firewall под новые адреса контейнеров"
"$(dirname "${BASH_SOURCE[0]}")/firewall.sh" apply
ready="no"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1; then
    ready="yes"
    break
  fi
  sleep 1
done
finished=$(date +%s)
elapsed=$((finished - started))
if [[ "$ready" != "yes" ]]; then
  echo "API не ответил за ${elapsed} с" >&2
  exit 1
fi

echo "==== маркер после promotion ===="
docker compose exec -T postgres-standby psql -U sentinel -d sentinel -c "SELECT id, event_type, status FROM tasks WHERE id = '${marker}';"
echo "ручное переключение заняло ${elapsed} с"
echo "старый primary остановлен, его том сохранён. Приложения смотрят на postgres-standby."
