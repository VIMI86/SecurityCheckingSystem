#!/usr/bin/env bash
# Собирает учебный кластер из трёх узлов и проверяет, что потеря одного узла
# не останавливает quorum-очередь. Потеря двух узлов из трёх кластер не переживёт.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

join_node() {
  local service="$1"
  local node="rabbit@${service}"
  if docker compose exec -T "$service" rabbitmqctl cluster_status | grep -Eq '(^|[^[:alnum:]_@-])rabbit@rabbitmq([^[:alnum:]_@-]|$)'; then
    echo "${service} уже в кластере"
    return
  fi
  docker compose exec -T "$service" rabbitmqctl stop_app
  docker compose exec -T "$service" rabbitmqctl reset
  docker compose exec -T "$service" rabbitmqctl join_cluster rabbit@rabbitmq
  docker compose exec -T "$service" rabbitmqctl start_app
  echo "${node} присоединён"
}

docker compose up -d rabbitmq rabbitmq-2 rabbitmq-3
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

join_node rabbitmq-2
join_node rabbitmq-3
docker compose exec -T rabbitmq rabbitmqctl cluster_status

docker compose up -d --build --no-deps --force-recreate api orchestrator
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 && break
  sleep 1
done

echo "останавливаю rabbitmq-3"
docker compose stop rabbitmq-3
created="$(curl -fsS -X POST http://127.0.0.1:8000/tasks -H 'content-type: application/json' -d '{"event_type":"broker_failover","payload":{"node":"down"}}')"
task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$created")"
status="pending"
for _ in $(seq 1 20); do
  status="$(curl -fsS "http://127.0.0.1:8000/tasks/${task_id}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [[ "$status" == "done" ]] && break
  sleep 1
done
echo "задача при остановленном rabbitmq-3: ${status}"
[[ "$status" == "done" ]]
docker compose start rabbitmq-3
echo "rabbitmq-3 снова запущен"
