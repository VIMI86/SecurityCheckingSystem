import logging
import os
import threading
from contextlib import asynccontextmanager

import pika
import psycopg
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.worker import run_consumer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SERVICE_NAME = os.environ.get("SERVICE_NAME", "orchestrator")


def _check_postgres() -> str:
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception as exc:
        return type(exc).__name__


def _check_redis() -> str:
    try:
        client = redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3)
        client.ping()
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=run_consumer, name="task-consumer", daemon=True)
    thread.start()
    yield


app = FastAPI(title="SentinelAI Orchestrator", version="0.2.0", lifespan=lifespan)


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
