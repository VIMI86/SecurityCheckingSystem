import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pika
import psycopg
import redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("api")

SERVICE_NAME = os.environ.get("SERVICE_NAME", "api")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "30"))
MAX_PAYLOAD_BYTES = 65536


def _check_postgres() -> str:
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception as exc:
        return type(exc).__name__


def _check_redis() -> str:
    try:
        redis_client().ping()
        return "ok"
    except Exception as exc:
        return type(exc).__name__


def _check_rabbitmq() -> str:
    try:
        params = pika.URLParameters(os.environ["RABBITMQ_URL"])
        params.socket_timeout = 3
        params.blocked_connection_timeout = 3
        connection = pika.BlockingConnection(params)
        connection.close()
        return "ok"
    except Exception as exc:
        return type(exc).__name__


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3, decode_responses=True)


def cache_key(task_id: str) -> str:
    return f"task:{task_id}"


def invalidate(task_id: str) -> None:
    redis_client().delete(cache_key(task_id))


def open_task_channel(connection: pika.BlockingConnection) -> pika.channel.Channel:
    channel = connection.channel()
    channel.exchange_declare(exchange=db.EXCHANGE, exchange_type="direct", durable=True)
    try:
        channel.queue_declare(queue=db.QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
    except pika.exceptions.ChannelClosedByBroker as exc:
        if exc.reply_code != 406:
            raise
        channel = connection.channel()
        channel.queue_delete(queue=db.QUEUE)
        channel.exchange_declare(exchange=db.EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(queue=db.QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_bind(queue=db.QUEUE, exchange=db.EXCHANGE, routing_key=db.ROUTING_KEY)
    return channel


def publish_task(task_id: str) -> None:
    params = pika.URLParameters(os.environ["RABBITMQ_URL"])
    params.socket_timeout = 3
    connection = pika.BlockingConnection(params)
    try:
        channel = open_task_channel(connection)
        channel.confirm_delivery()
        channel.basic_publish(
            exchange=db.EXCHANGE,
            routing_key=db.ROUTING_KEY,
            body=json.dumps({"task_id": task_id}),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )
    finally:
        connection.close()


class TaskCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def limit_payload(cls, value: dict) -> dict:
        if len(json.dumps(value).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload is larger than 64 KiB")
        return value


class TaskUpdate(BaseModel):
    event_type: str | None = Field(default=None, min_length=1, max_length=100)
    payload: dict | None = None

    @field_validator("payload")
    @classmethod
    def limit_payload(cls, value: dict | None) -> dict | None:
        if value is not None and len(json.dumps(value).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload is larger than 64 KiB")
        return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.apply_schema()
    logger.info("schema applied")
    yield


app = FastAPI(title="SentinelAI API", version="0.2.0", lifespan=lifespan)
app.mount(
    "/ui",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="ui",
)


@app.get("/")
def hello() -> dict[str, str]:
    return {"service": SERVICE_NAME, "message": "hello"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    checks = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "rabbitmq": _check_rabbitmq(),
    }
    ok = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"service": SERVICE_NAME, "status": "ok" if ok else "not_ready", "checks": checks},
    )


@app.post("/tasks", status_code=201)
def create_task(body: TaskCreate) -> dict:
    task = db.insert_task(body.event_type, body.payload)
    try:
        publish_task(task["id"])
    except Exception:
        logger.exception("task_id=%s publish failed", task["id"])
        db.set_status(task["id"], "failed")
        db.add_audit(task["id"], "task.publish_failed", {})
        raise HTTPException(status_code=503, detail="queue unavailable") from None
    db.add_audit(task["id"], "task.created", {"event_type": body.event_type})
    logger.info("task_id=%s created", task["id"])
    return task


@app.get("/tasks")
def list_tasks() -> dict:
    return {"tasks": db.list_tasks()}


@app.get("/tasks/{task_id}")
def get_task(task_id: UUID) -> dict:
    key = cache_key(str(task_id))
    client = redis_client()
    cached = client.get(key)
    if cached:
        body = json.loads(cached)
        body["cache"] = "hit"
        return body
    task = db.get_task(str(task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    client.set(key, json.dumps(task), ex=CACHE_TTL_SECONDS)
    return {**task, "cache": "miss"}


@app.get("/tasks/{task_id}/audit")
def get_audit(task_id: UUID) -> dict:
    if db.get_task(str(task_id)) is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"events": db.list_audit(str(task_id))}


@app.patch("/tasks/{task_id}")
def patch_task(task_id: UUID, body: TaskUpdate) -> dict:
    if body.event_type is None and body.payload is None:
        raise HTTPException(status_code=422, detail="empty update")
    updated = db.update_task(str(task_id), body.event_type, body.payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="task not found")
    invalidate(str(task_id))
    db.add_audit(str(task_id), "task.updated", {})
    logger.info("task_id=%s updated", task_id)
    return updated


@app.delete("/tasks/{task_id}", status_code=204)
def remove_task(task_id: UUID) -> Response:
    deleted = db.delete_task(str(task_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="task not found")
    invalidate(str(task_id))
    logger.info("task_id=%s deleted", task_id)
    return Response(status_code=204)
