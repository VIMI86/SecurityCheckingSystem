import json
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

EXCHANGE = "sentinel.tasks"
QUEUE = "sentinel.tasks"
ROUTING_KEY = "task"


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def apply_schema() -> None:
    path = os.environ.get("SCHEMA_PATH", "/app/schema.sql")
    script = open(path, encoding="utf-8").read()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=3) as conn:
        with psycopg.ClientCursor(conn) as cur:
            cur.execute(script)


def _task_from_row(row: dict, results: list[dict] | None = None) -> dict:
    steps = results or []
    policy = next((item["result"] for item in steps if item["agent"] == "policy"), None)
    return {
        "id": str(row["id"]),
        "event_type": row["event_type"],
        "payload": row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]),
        "status": row["status"],
        "result": _json_or_none(row.get("result")),
        "results": steps,
        "policy": policy,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _load_results(conn: psycopg.Connection, task_ids: list[str]) -> dict[str, list[dict]]:
    if not task_ids:
        return {}
    rows = conn.execute(
        """
        SELECT task_id::text AS task_id, agent_name, result
        FROM agent_results
        WHERE task_id::text = ANY(%s)
        ORDER BY created_at
        """,
        (task_ids,),
    ).fetchall()
    grouped: dict[str, list[dict]] = {task_id: [] for task_id in task_ids}
    for row in rows:
        grouped[str(row["task_id"])].append(
            {"agent": row["agent_name"], "result": _json_or_none(row["result"])}
        )
    return grouped


def _json_or_none(value):
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


_TASK_SQL = """
SELECT t.id, t.event_type, t.payload, t.status, t.created_at, t.updated_at, r.result
FROM tasks t
LEFT JOIN LATERAL (
    SELECT result
    FROM agent_results
    WHERE task_id = t.id
    ORDER BY created_at DESC
    LIMIT 1
) r ON true
"""


def insert_task(event_type: str, payload: dict) -> dict:
    task_id = str(uuid4())
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO tasks (id, event_type, payload, status)
            VALUES (%s, %s, %s, 'pending')
            RETURNING id, event_type, payload, status, created_at, updated_at
            """,
            (task_id, event_type, Jsonb(payload)),
        ).fetchone()
        row["result"] = None
        conn.commit()
    return _task_from_row(row, [])


def get_task(task_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(_TASK_SQL + " WHERE t.id = %s", (task_id,)).fetchone()
        if row is None:
            return None
        results = _load_results(conn, [task_id]).get(task_id, [])
    return _task_from_row(row, results)


def list_tasks() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(_TASK_SQL + " ORDER BY t.created_at DESC LIMIT 50").fetchall()
        results = _load_results(conn, [str(row["id"]) for row in rows])
    return [_task_from_row(row, results.get(str(row["id"]), [])) for row in rows]


def update_task(task_id: str, event_type: str | None, payload: dict | None) -> dict | None:
    current = get_task(task_id)
    if current is None:
        return None
    new_type = event_type if event_type is not None else current["event_type"]
    new_payload = payload if payload is not None else current["payload"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET event_type = %s, payload = %s, updated_at = now()
            WHERE id = %s
            """,
            (new_type, Jsonb(new_payload), task_id),
        )
        conn.commit()
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        conn.commit()
        return cur.rowcount == 1


def set_status(task_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE tasks SET status = %s, updated_at = now() WHERE id = %s",
            (status, task_id),
        )
        conn.commit()


def add_audit(task_id: str, event_type: str, details: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_events (id, task_id, event_type, details)
            VALUES (%s, %s, %s, %s)
            """,
            (str(uuid4()), task_id, event_type, Jsonb(details)),
        )
        conn.commit()


def list_audit(task_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, event_type, details, created_at
            FROM audit_events
            WHERE task_id = %s
            ORDER BY created_at
            """,
            (task_id,),
        ).fetchall()
    events = []
    for row in rows:
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        events.append(
            {
                "id": str(row["id"]),
                "task_id": str(row["task_id"]) if row["task_id"] else None,
                "event_type": row["event_type"],
                "details": details,
                "created_at": row["created_at"].isoformat(),
            }
        )
    return events
