#!/usr/bin/env bash
# Управление учебным firewall SentinelAI.
# Правила попадают в DOCKER-USER хоста. SSH и исходящий маршрут ноутбука к homelab не меняются.
# Откат: ./scripts/firewall.sh rollback
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
IMAGE="sentinelai-firewall"

usage() {
  echo "использование: $0 apply | rollback | break | unbreak | status | test"
  exit 1
}

build_image() {
  docker build -t "$IMAGE" -f scripts/firewall.Dockerfile "$ROOT"
}

cid() {
  docker compose ps -aq "$1" | head -n 1
}

ip_of() {
  local id="$1"
  local net="$2"
  local value
  value="$(docker inspect -f "{{with index .NetworkSettings.Networks \"${net}\"}}{{.IPAddress}}{{end}}" "$id" 2>/dev/null || true)"
  if [[ "$value" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
    printf '%s' "$value"
  fi
}

collect_env() {
  local api orch agent postgres redis rabbit name id
  local data_ips=() broker_ips=()
  api="$(cid api)"
  orch="$(cid orchestrator)"
  agent="$(cid agent)"
  postgres="$(cid postgres)"
  standby="$(cid postgres-standby)"
  redis="$(cid redis)"
  rabbit="$(cid rabbitmq)"
  if [[ -z "$api" || -z "$orch" || -z "$agent" || -z "$standby" || -z "$redis" || -z "$rabbit" ]]; then
    echo "стек не запущен: сначала ./scripts/demo-stage4-5.sh" >&2
    exit 1
  fi
  for name in api api-2 api-3; do
    id="$(cid "$name")"
    if [[ -z "$id" ]]; then
      echo "нет контейнера $name" >&2
      exit 1
    fi
    data_ips+=("$(ip_of "$id" sentinelai_data)")
    broker_ips+=("$(ip_of "$id" sentinelai_broker)")
  done
  export API_DATA_IPS="$(IFS=,; echo "${data_ips[*]}")"
  export API_BROKER_IPS="$(IFS=,; echo "${broker_ips[*]}")"
  export API_BROKER_IP="${broker_ips[0]}"
  export API_DATA_IP="${data_ips[0]}"
  export ORCH_BROKER_IP="$(ip_of "$orch" sentinelai_broker)"
  export ORCH_DATA_IP="$(ip_of "$orch" sentinelai_data)"
  export ORCH_APP_IP="$(ip_of "$orch" sentinelai_app)"
  export AGENT_IP="$(ip_of "$agent" sentinelai_app)"
  export POSTGRES_IP=""
  if [[ -n "$postgres" ]]; then
    POSTGRES_IP="$(ip_of "$postgres" sentinelai_data)"
    export POSTGRES_IP
  fi
  export POSTGRES_STANDBY_IP="$(ip_of "$(cid postgres-standby)" sentinelai_data)"
  export REDIS_IP="$(ip_of "$redis" sentinelai_data)"
  export RABBIT_IP="$(ip_of "$rabbit" sentinelai_broker)"
  local name
  for name in API_BROKER_IP API_DATA_IP ORCH_BROKER_IP ORCH_DATA_IP ORCH_APP_IP AGENT_IP POSTGRES_STANDBY_IP REDIS_IP RABBIT_IP; do
    if [[ -z "${!name}" ]]; then
      echo "пустой адрес $name" >&2
      exit 1
    fi
  done
}

run_fw() {
  docker run --rm --network host --cap-add NET_ADMIN --cap-add NET_RAW \
    -e API_BROKER_IP -e API_DATA_IP -e API_BROKER_IPS -e API_DATA_IPS \
    -e ORCH_BROKER_IP -e ORCH_DATA_IP -e ORCH_APP_IP \
    -e AGENT_IP -e POSTGRES_IP -e POSTGRES_STANDBY_IP -e REDIS_IP -e RABBIT_IP \
    "$IMAGE" "$@"
}

tcp_from() {
  local service="$1"
  local host="$2"
  local port="$3"
  docker compose exec -T "$service" python -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
    print('open')
except Exception as exc:
    print(type(exc).__name__)
" "$host" "$port"
}

drop_count() {
  run_fw status | awk '/dpt:5432/ && $4 == "DROP" { print $2; exit }'
}

run_test() {
  build_image
  collect_env
  trap 'run_fw unbreak || true' EXIT
  run_fw apply
  echo "---- разрешённый поток: orchestrator -> rabbitmq:5672 ----"
  local allowed
  allowed="$(tcp_from orchestrator rabbitmq 5672)"
  echo "$allowed"
  [[ "$allowed" == "open" ]]
  echo "---- запрещённый поток: agent не видит имя postgres ----"
  docker compose exec -T agent python -c "
import socket
try:
    socket.getaddrinfo('postgres', 5432)
    print('resolved')
except socket.gaierror as exc:
    print('denied', exc.errno)
"
  local before after probe
  before="$(drop_count)"
  local db_ip="${POSTGRES_IP:-$POSTGRES_STANDBY_IP}"
  echo "---- запрещённый поток: чужой контейнер в data-net -> ${db_ip}:5432 ----"
  docker rm -f sentinel-probe >/dev/null 2>&1 || true
  docker run -d --name sentinel-probe --network sentinelai_data python:3.12-slim sleep 30 >/dev/null
  probe="$(docker exec sentinel-probe python -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect((sys.argv[1], 5432))
    print('open')
except Exception as exc:
    print(type(exc).__name__)
" "$db_ip")"
  docker rm -f sentinel-probe >/dev/null
  echo "пробный контейнер -> postgres: $probe"
  after="$(drop_count)"
  echo "счётчик DROP на PostgreSQL: было ${before:-0}, стало ${after:-0}"
  [[ "$probe" == "TimeoutError" ]]
  [[ "${after:-0}" -gt "${before:-0}" ]]

  echo "---- временная блокировка легитимного правила ----"
  run_fw break
  local blocked
  blocked="$(tcp_from orchestrator rabbitmq 5672)"
  echo "orchestrator -> rabbitmq во время блокировки: $blocked"
  local created task_id state
  created="$(curl -fsS -X POST http://127.0.0.1:8000/tasks -H 'content-type: application/json' -d '{"event_type":"ids_alert","payload":{"src":"10.9.9.9","msg":"firewall-break"}}')"
  task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$created")"
  sleep 6
  state="$(curl -fsS "http://127.0.0.1:8000/tasks/${task_id}")"
  local blocked_status
  blocked_status="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["status"])' "$state")"
  echo "задача во время блокировки: $blocked_status"
  [[ "$blocked" == "TimeoutError" ]]
  [[ "$blocked_status" == "pending" ]]
  run_fw unbreak
  trap - EXIT
  local recovered
  recovered="$(tcp_from orchestrator rabbitmq 5672)"
  echo "orchestrator -> rabbitmq после отката: $recovered"
  local created2 task2
  created2="$(curl -fsS -X POST http://127.0.0.1:8000/tasks -H 'content-type: application/json' -d '{"event_type":"ids_alert","payload":{"src":"10.9.9.10","msg":"firewall-restored"}}')"
  task2="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$created2")"
  local i status2="pending"
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    status2="$(curl -fsS "http://127.0.0.1:8000/tasks/${task2}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
    [[ "$status2" == "done" ]] && break
    sleep 1
  done
  echo "новая задача после отката: $status2"
  [[ "$recovered" == "open" ]]
  [[ "$status2" == "done" ]]
  curl -fsS -o /dev/null http://127.0.0.1:8000/health && echo "API на localhost:8000 жив"
  curl -fsS -o /dev/null http://127.0.0.1:8002/health && echo "Agent на localhost:8002 жив"
}

[[ $# -eq 1 ]] || usage
case "$1" in
  apply|rollback|break|unbreak|status)
    build_image
    collect_env
    run_fw "$1"
    ;;
  test) run_test ;;
  *) usage ;;
esac
