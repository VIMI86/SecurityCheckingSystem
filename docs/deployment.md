# Размещение

Две машины. Учебный контур курса живёт на ноутбуке. Homelab нужен только как сервер модели.

## Ноутбук

Хост `vpbook-MCLG-XX`, Ubuntu 26.04, Docker 29.9, Compose 5.6.

Здесь на этапах 2–5 поднимаются:

- API Gateway, Orchestrator, Analyst Agent, Security Agent, Policy, Audit;
- PostgreSQL, Redis, RabbitMQ;
- правила firewall и учебный failover.

Демонстрация идёт с этого хоста: `docker compose`, curl, логи и сетевые проверки.

## Homelab

На homelab работает Ollama. Ноутбук ходит к ней по HTTP в локальной сети.

Пока адрес и модель неизвестны, в документах и будущем `.env` стоят плейсхолдеры:

```text
LLM_PROVIDER=mock
OLLAMA_BASE_URL=http://HOMELAB_HOST:11434
OLLAMA_MODEL=MODEL_NAME
```

Реальный адрес подставляется локально и в git не попадает. Проверка связи, когда значения будут известны:

```bash
curl -sS "$OLLAMA_BASE_URL/api/tags"
```

Если homelab недоступен, `LLM_PROVIDER=mock` оставляет этапы 2–5 рабочими.

## Сети Compose

Сети уже подняты. Фильтр и проверки описаны в [network-security.md](network-security.md).

| Сеть | Кто в ней | Зачем |
| --- | --- | --- |
| `sentinelai_edge` | API | Входной сегмент. С ноутбука API доступен на `127.0.0.1:8000` |
| `sentinelai_app` | API, Orchestrator, Agent | Вызов заглушки агента |
| `sentinelai_data` | API, Orchestrator, PostgreSQL, Redis | Данные. Agent в эту сеть не входит |
| `sentinelai_broker` | API, Orchestrator, RabbitMQ | Очередь задач. Agent в эту сеть не входит |

PostgreSQL и Redis наружу не публикуются. На `127.0.0.1` дополнительно оставлены Orchestrator `8001`, Agent `8002` и панель RabbitMQ `15672`.

Правила стоят в `DOCKER-USER` и не перекрывают SSH. Путь ноутбука к `OLLAMA_BASE_URL` они тоже не режут. Откат: `./scripts/firewall.sh rollback`.

Agent остаётся только в `sentinelai_app`. Доступа к Docker socket и к shell хоста у него нет. Вызов Ollama — исходящий HTTP адаптера, а не действие, которое агент выполняет на homelab сам.
