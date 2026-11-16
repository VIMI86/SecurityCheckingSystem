import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "agent"))

from app.llm import AnalyzeRequest, LLMError, analyze


def test_mock_does_not_call_network(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    def fail_open(*_args, **_kwargs):
        raise AssertionError("mock must not open a network connection")

    monkeypatch.setattr("urllib.request.urlopen", fail_open)
    result = analyze(AnalyzeRequest(task_id="t1", event_type="ids_alert", payload={"src": "10.0.0.8"}))
    assert result.provider == "mock"
    assert result.agent == "analyst"
    assert result.proposed_action == ""
    assert "ids_alert" in result.facts
    assert "src=10.0.0.8" in result.facts


def test_unknown_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "cloud")
    try:
        analyze(AnalyzeRequest(task_id="t1", event_type="ids_alert", payload={}))
    except LLMError as exc:
        assert "unknown provider" in str(exc)
    else:
        raise AssertionError("expected LLMError")


def test_oversized_payload_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    try:
        analyze(AnalyzeRequest(task_id="t1", event_type="ids_alert", payload={"blob": "x" * 70000}))
    except LLMError as exc:
        assert "64 KiB" in str(exc)
    else:
        raise AssertionError("expected LLMError")
