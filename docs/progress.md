# Матрица готовности

Статусы: не начато, в документе, в коде, запущено, проверено.

Этап 1 закрыт как документ, когда описание, схема, стек и речь лежат в репозитории и их можно пересказать. Это ещё не статус «проверено»: устная защита впереди. Код сервисов статус не меняет, пока сценарий не запущен.

| Этап | Критерий | Доказательство | Статус |
| --- | --- | --- | --- |
| 1 | Описание, целесообразность, архитектура, стек, размещение ноутбук и homelab | README и `docs/`, речь в `docs/defense.md` | В документе |
| 2 | Hello-world микросервисы, Compose, PostgreSQL, Redis, RabbitMQ | `docker compose ps`, curl `/`, `/health`, `/ready` | Проверено на ноутбуке: все шесть контейнеров healthy, API `/ready` видит PostgreSQL, Redis и RabbitMQ |
| 3 | CRUD и SQL-схема, Redis read/write, RabbitMQ publish/consume | `sql/schema.sql`, curl, `redis-cli`, `psql`, `pytest tests/test_stage3.py` | Проверено на ноутбуке: 3 теста прошли; задача доходит до `done`, второй GET берётся из Redis, `simulate_error` даёт nack и повторную доставку |
| 4 | Сегменты, firewall, allow, deny, отказ легитимного правила | `docs/network-security.md`, `./scripts/firewall.sh test` | Проверено на ноутбуке: allow к RabbitMQ открыт, чужой доступ к PostgreSQL отброшен, временный DROP оставляет задачу `pending`, после отката новая задача `done` |
| 5 | Failover PostgreSQL и RabbitMQ, backup/restore, учебные SLO, RPO, RTO, MTTR, MTBF | `docs/reliability.md`, дамп, `pg_promote`, остановка одного узла quorum-очереди | Проверено на ноутбуке: маркер сохранился после promotion, restore показал 6 задач, задача прошла при остановленном `rabbitmq-3` |
| AI | Два агента, Orchestrator, Policy, Audit, mock и Ollama | `pytest tests/test_agents_policy.py`, живой `POST /tasks`, карточка `/ui` | Проверено: три решения на mock без сети; живой прогон `0e519955-e921-4c88-af10-0a06298f432f` дал `record_only` → `ALLOW`, аудит `policy.allow`, исполнение не запускалось |
| 6 | Несколько экземпляров приёма запросов, HAProxy, нагрузочный прогон | `docs/load-balancing.md`, `python3 scripts/load_test.py` | Проверено на ноутбуке: 300 запросов `GET /health`, 0 ошибок, по 100 на `api-1`, `api-2` и `api-3` |

Критерий этапа 1, который ещё зависит от защиты: за 2–4 минуты объяснить, что делает система, зачем микросервисы и как ограничиваются действия агентов. Текст для этого — в [docs/defense.md](defense.md).
