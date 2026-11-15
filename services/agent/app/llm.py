import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel, Field

MAX_PAYLOAD_BYTES = 65536


class AnalyzeRequest(BaseModel):
    task_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)
    role: str = "analyst"
    facts: list[str] = Field(default_factory=list)


class AnalyzeResult(BaseModel):
    agent: str
    provider: str
    task_id: str
    event_type: str
    summary: str
    facts: list[str]
    risk: str
    proposed_action: str


class LLMError(Exception):
    pass


def provider_name() -> str:
    return os.environ.get("LLM_PROVIDER", "mock").strip().lower()


def analyze(body: AnalyzeRequest) -> AnalyzeResult:
    raw = json.dumps(body.payload, ensure_ascii=False).encode()
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise LLMError("payload is larger than 64 KiB")
    if body.role == "security":
        return _security(body)
    if body.role != "analyst":
        raise LLMError(f"unknown role: {body.role}")
    return _analyst(body)


def _analyst(body: AnalyzeRequest) -> AnalyzeResult:
    provider = provider_name()
    if provider == "mock":
        return _mock_analyst(body)
    if provider == "ollama":
        return _ollama(body, role="analyst")
    raise LLMError(f"unknown provider: {provider}")


def _security(body: AnalyzeRequest) -> AnalyzeResult:
    provider = provider_name()
    if provider == "mock":
        return _mock_security(body)
    if provider == "ollama":
        return _ollama(body, role="security")
    raise LLMError(f"unknown provider: {provider}")


def _visible_facts(body: AnalyzeRequest) -> list[str]:
    facts = [body.event_type]
    for key, value in body.payload.items():
        if key in {"proposed_action", "simulate_error"}:
            continue
        facts.append(f"{key}={value}")
    return facts


def _mock_analyst(body: AnalyzeRequest) -> AnalyzeResult:
    return AnalyzeResult(
        agent="analyst",
        provider="mock",
        task_id=body.task_id,
        event_type=body.event_type,
        summary=f"Mock-разбор события {body.event_type}. Модель не вызывалась.",
        facts=_visible_facts(body),
        risk="low",
        proposed_action="",
    )


def _mock_security(body: AnalyzeRequest) -> AnalyzeResult:
    if "proposed_action" in body.payload:
        action = str(body.payload.get("proposed_action") or "")
    else:
        action = "record_only"
    facts = body.facts or _visible_facts(body)
    return AnalyzeResult(
        agent="security",
        provider="mock",
        task_id=body.task_id,
        event_type=body.event_type,
        summary=f"Mock-предложение для {body.event_type}: {action or 'пусто'}.",
        facts=facts,
        risk="low",
        proposed_action=action,
    )


def _ollama(body: AnalyzeRequest, role: str) -> AnalyzeResult:
    base = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "")
    if not base or not model:
        raise LLMError("OLLAMA_BASE_URL and OLLAMA_MODEL are required")
    payload = json.dumps(body.payload, ensure_ascii=False)
    if role == "analyst":
        prompt = (
            "Разбери событие безопасности и верни только JSON с полями "
            "summary (строка), facts (массив коротких строк), "
            "risk (low, medium или high). "
            "Не предлагай команды и не выбирай ALLOW, DENY или REQUIRE_APPROVAL.\n"
            f"event_type: {body.event_type}\n"
            f"payload: {payload}"
        )
    else:
        prompt = (
            "По фактам события предложи одно действие и верни только JSON с полями "
            "summary (строка), risk (low, medium или high), "
            "proposed_action (строка). Обычно это record_only, block_host или isolate_host. "
            "Если действие неясно, верни record_only. "
            "Не выбирай ALLOW, DENY или REQUIRE_APPROVAL и не давай команды.\n"
            f"event_type: {body.event_type}\n"
            f"facts: {json.dumps(body.facts, ensure_ascii=False)}\n"
            f"payload: {payload}"
        )
    request = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(
            {
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    timeout = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "60"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            remote = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(type(exc).__name__) from exc
    content = remote.get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError("model returned non-json") from exc
    facts = [str(item) for item in parsed.get("facts") or []]
    if role == "security" and not facts:
        facts = list(body.facts)
    action = ""
    if role == "security":
        action = str(parsed.get("proposed_action") or "")
    return AnalyzeResult(
        agent=role,
        provider="ollama",
        task_id=body.task_id,
        event_type=body.event_type,
        summary=str(parsed.get("summary") or ""),
        facts=facts,
        risk=str(parsed.get("risk") or "low"),
        proposed_action=action,
    )


def ollama_reachable() -> bool:
    base = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
    if not base:
        return False
    request = urllib.request.Request(f"{base}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False
