#!/bin/bash
# Правила пишутся в цепочку SENTINEL-FW. Она вставляется первой в DOCKER-USER
# и для чужого трафика делает RETURN, поэтому уже существующие правила хоста остаются.
set -euo pipefail

comment=( -m comment --comment sentinel-fw )

need() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "не задано $name" >&2
    exit 1
  fi
}

ensure_jump() {
  iptables -N SENTINEL-FW 2>/dev/null || true
  if ! iptables -C DOCKER-USER -j SENTINEL-FW 2>/dev/null; then
    iptables -I DOCKER-USER 1 -j SENTINEL-FW
  fi
}

allow() {
  iptables -A SENTINEL-FW -s "$1" -d "$2" -p tcp --dport "$3" "${comment[@]}" -j ACCEPT
}

allow_csv() {
  local csv="$1"
  local dest="$2"
  local port="$3"
  local src
  local IFS=','
  read -ra sources <<< "$csv"
  for src in "${sources[@]}"; do
    if [[ -n "$src" ]]; then
      allow "$src" "$dest" "$port"
    fi
  done
}

apply_rules() {
  need API_BROKER_IP
  need API_DATA_IP
  need ORCH_BROKER_IP
  need ORCH_DATA_IP
  need ORCH_APP_IP
  need AGENT_IP
  need POSTGRES_STANDBY_IP
  need REDIS_IP
  need RABBIT_IP

  ensure_jump
  iptables -F SENTINEL-FW
  iptables -A SENTINEL-FW -m conntrack --ctstate ESTABLISHED,RELATED "${comment[@]}" -j RETURN
  allow_csv "${API_BROKER_IPS:-$API_BROKER_IP}" "$RABBIT_IP" 5672
  allow "$ORCH_BROKER_IP" "$RABBIT_IP" 5672
  if [[ -n "${POSTGRES_IP:-}" ]]; then
    allow_csv "${API_DATA_IPS:-$API_DATA_IP}" "$POSTGRES_IP" 5432
    allow "$ORCH_DATA_IP" "$POSTGRES_IP" 5432
    allow "$POSTGRES_STANDBY_IP" "$POSTGRES_IP" 5432
  fi
  allow_csv "${API_DATA_IPS:-$API_DATA_IP}" "$POSTGRES_STANDBY_IP" 5432
  allow "$ORCH_DATA_IP" "$POSTGRES_STANDBY_IP" 5432
  allow_csv "${API_DATA_IPS:-$API_DATA_IP}" "$REDIS_IP" 6379
  allow "$ORCH_DATA_IP" "$REDIS_IP" 6379
  allow "$ORCH_APP_IP" "$AGENT_IP" 8000
  if [[ -n "${POSTGRES_IP:-}" ]]; then
    iptables -A SENTINEL-FW -d "$POSTGRES_IP" -p tcp --dport 5432 "${comment[@]}" -j DROP
  fi
  iptables -A SENTINEL-FW -d "$POSTGRES_STANDBY_IP" -p tcp --dport 5432 "${comment[@]}" -j DROP
  iptables -A SENTINEL-FW -d "$REDIS_IP" -p tcp --dport 6379 "${comment[@]}" -j DROP
  iptables -A SENTINEL-FW -d "$RABBIT_IP" -p tcp --dport 5672 "${comment[@]}" -j DROP
  iptables -A SENTINEL-FW "${comment[@]}" -j RETURN
  echo "SENTINEL-FW применена"
}

break_broker() {
  need ORCH_BROKER_IP
  need RABBIT_IP
  ensure_jump
  if iptables -S SENTINEL-FW | grep -q sentinel-break; then
    echo "блокировка брокера уже стоит"
    return
  fi
  iptables -I SENTINEL-FW 1 -s "$ORCH_BROKER_IP" -d "$RABBIT_IP" -p tcp --dport 5672 \
    -m comment --comment sentinel-break -j DROP
  iptables -I SENTINEL-FW 1 -s "$RABBIT_IP" -d "$ORCH_BROKER_IP" -p tcp --sport 5672 \
    -m comment --comment sentinel-break -j DROP
  echo "Orchestrator <-> RabbitMQ:5672 закрыт"
}

unbreak_broker() {
  local removed=0
  local rule
  while rule=$(iptables -S SENTINEL-FW | grep sentinel-break | head -n 1); do
    # shellcheck disable=SC2086
    iptables ${rule/-A /-D }
    removed=1
  done
  if [[ "$removed" -eq 1 ]]; then
    echo "блокировка брокера снята"
  else
    echo "блокировки брокера не было"
  fi
}

rollback_all() {
  unbreak_broker || true
  while iptables -C DOCKER-USER -j SENTINEL-FW 2>/dev/null; do
    iptables -D DOCKER-USER -j SENTINEL-FW
  done
  iptables -F SENTINEL-FW 2>/dev/null || true
  iptables -X SENTINEL-FW 2>/dev/null || true
  echo "SENTINEL-FW удалена, остальные правила DOCKER-USER не менялись"
}

status_rules() {
  echo "==== DOCKER-USER ===="
  iptables -L DOCKER-USER -n -v --line-numbers
  echo "==== SENTINEL-FW ===="
  iptables -L SENTINEL-FW -n -v --line-numbers 2>/dev/null || echo "цепочки SENTINEL-FW нет"
}

case "${1:-status}" in
  apply) apply_rules ;;
  break) break_broker ;;
  unbreak) unbreak_broker ;;
  rollback) rollback_all ;;
  status) status_rules ;;
  *)
    echo "команда: apply, break, unbreak, rollback, status" >&2
    exit 1
    ;;
esac
