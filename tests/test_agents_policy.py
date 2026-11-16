import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from app.llm import AnalyzeRequest, analyze

_spec = importlib.util.spec_from_file_location(
    "sentinel_policy",
    ROOT / "services" / "orchestrator" / "app" / "policy.py",
)
_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)
decide = _policy.decide


def _block_network(monkeypatch) -> None:
    def fail_open(*_args, **_kwargs):
        raise AssertionError("mock must not open a network connection")

    monkeypatch.setattr("urllib.request.urlopen", fail_open)


def _security(action):
    payload = {} if action is None else {"proposed_action": action}
    return analyze(
        AnalyzeRequest(
            task_id="t1",
            event_type="ids_alert",
            payload=payload,
            role="security",
            facts=["ids_alert"],
        )
    )


def test_mock_allow_record_only(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    _block_network(monkeypatch)
    analyst = analyze(AnalyzeRequest(task_id="t1", event_type="ids_alert", payload={"src": "10.0.0.8"}))
    security = _security(None)
    effect, audit_type = decide(security.proposed_action)
    assert analyst.agent == "analyst"
    assert analyst.facts[0] == "ids_alert"
    assert security.agent == "security"
    assert effect == "ALLOW"
    assert audit_type == "policy.allow"


def test_mock_deny_unknown_and_empty(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    _block_network(monkeypatch)
    unknown_effect, unknown_audit = decide(_security("drop_database").proposed_action)
    empty_effect, empty_audit = decide(_security("").proposed_action)
    assert unknown_effect == "DENY"
    assert unknown_audit == "policy.deny"
    assert empty_effect == "DENY"
    assert empty_audit == "policy.deny"


def test_mock_require_approval(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    _block_network(monkeypatch)
    block_effect, block_audit = decide(_security("block_host").proposed_action)
    isolate_effect, isolate_audit = decide(_security("isolate_host").proposed_action)
    assert block_effect == "REQUIRE_APPROVAL"
    assert block_audit == "policy.require_approval"
    assert isolate_effect == "REQUIRE_APPROVAL"
    assert isolate_audit == "policy.require_approval"
