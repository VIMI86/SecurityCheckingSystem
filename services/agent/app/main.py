import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.llm import AnalyzeRequest, LLMError, analyze, ollama_reachable, provider_name

app = FastAPI(title="SentinelAI Agent", version="0.3.0")

SERVICE_NAME = os.environ.get("SERVICE_NAME", "agent")


@app.get("/")
def hello() -> dict[str, str]:
    return {"service": SERVICE_NAME, "message": "hello"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    provider = provider_name()
    if provider == "mock":
        checks = {"llm": "mock"}
        ok = True
    elif provider == "ollama":
        ok = ollama_reachable()
        checks = {"llm": "ok" if ok else "unreachable"}
    else:
        ok = False
        checks = {"llm": "unknown"}
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"service": SERVICE_NAME, "status": "ok" if ok else "not_ready", "checks": checks},
    )


@app.post("/analyze")
def analyze_event(body: AnalyzeRequest) -> dict:
    try:
        return analyze(body).model_dump()
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
