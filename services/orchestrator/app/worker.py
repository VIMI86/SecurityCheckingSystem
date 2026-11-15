import json
import logging
import os
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pika
import psycopg
import redis
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.policy import decide_stored

logger = logging.getLogger("orchestrator")

EXCHANGE = "sentinel.tasks"
QUEUE = "sentinel.tasks"
ROUTING_KEY = "task"


class TemporaryFailure(Exception):
    pass


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def invalidate(task_id: str) -> None:
    client = redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3, decode_responses=True)
    client.delete(f"task:{task_id}")


def add_audit(conn: psycopg.Connection, task_id: str, event_type: str, details: dict) -> None:
    conn.execute(
        """
        INSERT INTO audit_events (id, task_id, event_type, details)
        VALUES (%s, %s, %s, %s)
        """,
        (str(uuid4()), task_id, event_type, Jsonb(details)),
    )


def call_agent(task_id: str, event_type: str, payload: dict, role: str, facts: list | None = None) -> dict:
    body = json.dumps(
        {
            "task_id": task_id,
            "event_type": event_type,
            "payload": payload,
            "role": role,
            "facts": facts or [],
        }
    ).encode()
    request = urllib.request.Request(
        os.environ.get("AGENT_URL", "http://agent:8000/analyze"),
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    timeout = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "60"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def process(task_id: str, redelivered: bool) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, event_type, payload, status FROM tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        if row is None or row["status"] in ("done", "failed"):
            return
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if payload.get("simulate_error") and not redelivered:
            add_audit(conn, task_id, "task.delivery_failed", {"reason": "simulated"})
            conn.commit()
            raise TemporaryFailure(task_id)
        conn.execute(
            "UPDATE tasks SET status = 'processing', updated_at = now() WHERE id = %s",
            (task_id,),
        )
        event_type = row["event_type"]
        conn.commit()

    invalidate(task_id)
    try:
        analyst = call_agent(task_id, event_type, payload, "analyst")
        security = call_agent(task_id, event_type, payload, "security", analyst.get("facts") or [])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TemporaryFailure(task_id) from exc

    with connect() as conn:
        action = str(security.get("proposed_action") or "")
        effect, audit_type = decide_stored(conn, action)
        conn.execute("DELETE FROM agent_results WHERE task_id = %s", (task_id,))
        policy_result = {
            "agent": "policy",
            "action": action,
            "effect": effect,
            "executed": False,
        }
        for name, result in (("analyst", analyst), ("security", security), ("policy", policy_result)):
            conn.execute(
                """
                INSERT INTO agent_results (id, task_id, agent_name, result)
                VALUES (%s, %s, %s, %s)
                """,
                (str(uuid4()), task_id, name, Jsonb(result)),
            )
        add_audit(conn, task_id, audit_type, {"action": action, "effect": effect, "executed": False})
        add_audit(conn, task_id, "task.completed", {"effect": effect, "action": action})
        conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = now() WHERE id = %s",
            (task_id,),
        )
        conn.commit()
    invalidate(task_id)
    logger.info("task_id=%s done effect=%s action=%s", task_id, effect, action or "-")


def mark_failed(task_id: str, reason: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'failed', updated_at = now()
            WHERE id = %s AND status <> 'done'
            """,
            (task_id,),
        )
        add_audit(conn, task_id, "task.failed", {"reason": reason})
        conn.commit()
    invalidate(task_id)
    logger.info("task_id=%s failed reason=%s", task_id, reason)


def _on_message(channel, method, properties, body: bytes) -> None:
    try:
        message = json.loads(body)
        task_id = str(message["task_id"])
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.exception("discarding malformed message")
        channel.basic_ack(method.delivery_tag)
        return
    try:
        process(task_id, redelivered=method.redelivered)
        channel.basic_ack(method.delivery_tag)
        logger.info("task_id=%s ack", task_id)
    except TemporaryFailure:
        if method.redelivered:
            mark_failed(task_id, "retry exhausted")
            channel.basic_ack(method.delivery_tag)
            logger.info("task_id=%s ack after retry", task_id)
        else:
            channel.basic_nack(method.delivery_tag, requeue=True)
            logger.info("task_id=%s nack requeue", task_id)
    except Exception:
        logger.exception("task_id=%s unexpected failure", task_id)
        if method.redelivered:
            mark_failed(task_id, "unexpected")
            channel.basic_ack(method.delivery_tag)
        else:
            channel.basic_nack(method.delivery_tag, requeue=True)


def _consume_forever() -> None:
    params = pika.URLParameters(os.environ["RABBITMQ_URL"])
    params.socket_timeout = 5
    params.heartbeat = 30
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
    try:
        channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
    except pika.exceptions.ChannelClosedByBroker as exc:
        if exc.reply_code != 406:
            raise
        channel = connection.channel()
        channel.queue_delete(queue=QUEUE)
        channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=QUEUE, on_message_callback=_on_message)
    logger.info("consuming %s", QUEUE)
    channel.start_consuming()


def run_consumer() -> None:
    while True:
        try:
            _consume_forever()
        except Exception:
            logger.exception("consumer stopped, retry in 3s")
            time.sleep(3)
