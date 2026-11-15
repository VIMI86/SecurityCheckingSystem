#!/usr/bin/env bash
# Повторный показ этапов 4 и 5.
# Поднимает только текущий контур: базу postgres-standby и три узла RabbitMQ.
# Старый primary не запускается. После старта заново применяются правила firewall.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if docker compose ps --status running --format '{{.Service}}' | grep -qx 'postgres'; then
  echo "Запущен устаревший primary. Остановите его и повторите показ:" >&2
  echo "  docker compose stop postgres" >&2
  exit 1
fi

join_rabbit() {
  local service="$1"
  if docker compose exec -T "$service" rabbitmqctl cluster_status | grep -Eq '(^|[^[:alnum:]_@-])rabbit@rabbitmq([^[:alnum:]_@-]|$)'; then
    echo "${service} уже в кластере"
    return
  fi
  docker compose exec -T "$service" rabbitmqctl stop_app
  docker compose exec -T "$service" rabbitmqctl reset
  docker compose exec -T "$service" rabbitmqctl join_cluster rabbit@rabbitmq
  docker compose exec -T "$service" rabbitmqctl start_app
}

echo "==== Поднятие живого контура ===="
docker compose up -d redis rabbitmq rabbitmq-2 rabbitmq-3 postgres-standby agent
for _ in $(seq 1 40); do
  ready=0
  for service in rabbitmq rabbitmq-2 rabbitmq-3; do
    if docker compose exec -T "$service" rabbitmq-diagnostics -q ping >/dev/null 2>&1; then
      ready=$((ready + 1))
    fi
  done
  [[ "$ready" -eq 3 ]] && break
  sleep 2
done
join_rabbit rabbitmq-2
join_rabbit rabbitmq-3
docker compose up -d api orchestrator

echo "==== Firewall под текущие адреса ===="
./scripts/firewall.sh apply
docker compose restart api orchestrator

for _ in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:8001/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/ready
echo
curl -fsS http://127.0.0.1:8001/ready
echo

echo "==== Этап 4 ===="
./scripts/firewall.sh test

echo "==== Этап 5: база после переключения ===="
recovery="$(docker compose exec -T postgres-standby psql -U sentinel -d sentinel -tAc "SELECT pg_is_in_recovery();")"
echo "база всё ещё в режиме реплики: ${recovery}"
if [[ "$recovery" != "f" ]]; then
  echo "postgres-standby не принимает запись. Показ этапа 5 рассчитан на уже продвинутую базу." >&2
  exit 1
fi
docker compose exec -T postgres-standby psql -U sentinel -d sentinel -c \
  "SELECT count(*) AS tasks FROM tasks;"

echo "==== Этап 5: backup и restore ===="
POSTGRES_SERVICE=postgres-standby ./scripts/postgres-backup.sh

echo "==== Этап 5: один узел брокера ===="
docker compose stop rabbitmq-3
created="$(curl -fsS -X POST http://127.0.0.1:8000/tasks \
  -H 'content-type: application/json' \
  -d '{"event_type":"demo_quorum","payload":{"node":"rabbitmq-3-down"}}')"
task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$created")"
task_state="pending"
for _ in $(seq 1 20); do
  task_state="$(curl -fsS "http://127.0.0.1:8000/tasks/${task_id}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [[ "$task_state" == "done" ]] && break
  sleep 1
done
docker compose start rabbitmq-3
echo "задача при остановленном rabbitmq-3: ${task_state}"
[[ "$task_state" == "done" ]]

echo "==== Показ завершён ===="
echo "API http://127.0.0.1:8000/ready"
echo "Старый primary не запускался и в показ не входит."
