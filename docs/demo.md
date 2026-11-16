# Сценарии демонстрации

Команды ниже — план защиты. Статус каждого пункта в [матрице готовности](progress.md). Сценарии A, B, C, D, E и F проверены на ноутбуке.

Повторный показ этапов 4 и 5 одной командой: `./scripts/demo-stage4-5.sh`. Он не запускает старый primary и перед проверками заново применяет firewall.

Готовым считается сценарий, который прогнан и чей вывод сохранён. Наличие файла в репозитории статус «проверено» не даёт.

## A. Базовый запуск

Этап 2.

```bash
docker compose up --build
docker compose ps
curl -fsS http://127.0.0.1:8000/health
```

Ожидается ответ health у API, Orchestrator и Agent и работающие PostgreSQL, Redis и RabbitMQ. У API `/ready` дополнительно показывает `postgres`, `redis` и `rabbitmq` со значением `ok`. Статус: проверено на ноутбуке.

## B. Полный путь задачи

Этап 3, до агентов — заглушка воркера.

```bash
curl -fsS -X POST http://127.0.0.1:8000/tasks \
  -H 'content-type: application/json' \
  -d '{"event_type":"ids_alert","payload":{"src":"10.0.0.8","msg":"ET SCAN"}}'
curl -fsS http://127.0.0.1:8000/tasks/TASK_ID
```

Ожидается `task_id`, сообщение в очереди `sentinel.tasks`, вызов `POST /analyze` у Agent, строка в `tasks` и `agent_results`, события `task.created` и `task.completed`. Подтверждение обработки пишется в лог Orchestrator как `task_id=... ack`. Статус: проверено на ноутбуке.

## C. Кэш

Этап 3.

Первый `GET /tasks/{id}` после обработки читает БД и пишет ключ `task:{id}` в Redis с TTL 30 секунд. Ответ содержит `"cache":"miss"`. Второй `GET` содержит `"cache":"hit"`. `PATCH` удаляет ключ, следующий `GET` снова `miss`. Статус: проверено на ноутбуке.

## D. Policy

Три решения проверяются с `LLM_PROVIDER=mock` и не вызывают Ollama:

```bash
PYTHONPATH=.pydeps python3 -m pytest -q tests/test_agents_policy.py
```

- `record_only` → `ALLOW`, исполнение не запускается;
- имя вне списка, включая пустое → `DENY`;
- `block_host` и `isolate_host` → `REQUIRE_APPROVAL`.

Живой прогон с текущим Ollama — обычный `POST /tasks`. В ответе есть шаги `analyst`, `security` и `policy`, в аудите есть `policy.allow`, `policy.deny` или `policy.require_approval`. Карточка выбранного запуска на `http://127.0.0.1:8000/ui` показывает решение. Статус: проверено на ноутбуке.

## E. Firewall

Этап 4. Команда `./scripts/firewall.sh test`.

1. Orchestrator открывает RabbitMQ на порт 5672.
2. Agent не резолвит `postgres`. Чужой контейнер в сети данных получает таймаут на порт 5432, счётчик DROP растёт.
3. Временный DROP на путь Orchestrator → RabbitMQ оставляет новую задачу в `pending`.
4. `unbreak` возвращает правило: следующее соединение открывается, новая задача доходит до `done`.

SSH и доступ ноутбука к Ollama на homelab этими правилами не затрагиваются. Статус: проверено на ноутбуке. Подробности в [network-security.md](network-security.md).

## F. Отказоустойчивость

Этап 5. Команды `./scripts/postgres-backup.sh`, `./scripts/postgres-failover.sh`, `./scripts/rabbitmq-cluster.sh`.

1. Запись на primary видна на standby до остановки.
2. `pg_promote()` оставляет эту запись на бывшей реплике. Старый том primary не удаляется.
3. Остановка одного узла из трёх не мешает задаче в quorum-очереди. Два узла из трёх очередь не обязана пережить.
4. `pg_dump` восстанавливается в отдельный экземпляр `postgres-restore`.
5. Целевые SLO, RPO, RTO, MTTR и MTBF лежат отдельно от наблюдений одного прогона.

Статус: проверено на ноутбуке. Подробности в [reliability.md](reliability.md).
