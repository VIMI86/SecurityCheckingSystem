import os
import time

import httpx

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")


def wait_until(task_id: str, status: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = httpx.get(f"{BASE}/tasks/{task_id}", timeout=5)
        response.raise_for_status()
        last = response.json()
        if last["status"] == status:
            return last
        time.sleep(0.2)
    raise AssertionError(last)


def test_task_flow_cache_update_and_delete() -> None:
    created = httpx.post(
        f"{BASE}/tasks",
        json={"event_type": "ids_alert", "payload": {"src": "10.0.0.8", "msg": "ET SCAN"}},
        timeout=5,
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    done = wait_until(task_id, "done", timeout=90)
    assert done["cache"] == "miss"
    agents = {item["agent"] for item in done["results"]}
    assert {"analyst", "security", "policy"} <= agents
    assert done["policy"]["effect"] in {"ALLOW", "DENY", "REQUIRE_APPROVAL"}
    assert done["policy"]["executed"] is False

    cached = httpx.get(f"{BASE}/tasks/{task_id}", timeout=5)
    assert cached.status_code == 200
    assert cached.json()["cache"] == "hit"
    assert cached.json()["status"] == "done"

    updated = httpx.patch(
        f"{BASE}/tasks/{task_id}",
        json={"payload": {"src": "10.0.0.9", "msg": "updated"}},
        timeout=5,
    )
    assert updated.status_code == 200
    assert updated.json()["payload"]["src"] == "10.0.0.9"

    reread = httpx.get(f"{BASE}/tasks/{task_id}", timeout=5)
    assert reread.json()["cache"] == "miss"
    assert reread.json()["payload"]["msg"] == "updated"

    audit = httpx.get(f"{BASE}/tasks/{task_id}/audit", timeout=5)
    assert audit.status_code == 200
    kinds = {event["event_type"] for event in audit.json()["events"]}
    assert "task.created" in kinds
    assert "task.completed" in kinds
    assert "task.updated" in kinds
    assert kinds & {"policy.allow", "policy.deny", "policy.require_approval"}

    listed = httpx.get(f"{BASE}/tasks", timeout=5)
    assert any(item["id"] == task_id for item in listed.json()["tasks"])

    deleted = httpx.delete(f"{BASE}/tasks/{task_id}", timeout=5)
    assert deleted.status_code == 204
    missing = httpx.get(f"{BASE}/tasks/{task_id}", timeout=5)
    assert missing.status_code == 404


def test_simulated_redelivery_then_completes() -> None:
    created = httpx.post(
        f"{BASE}/tasks",
        json={"event_type": "ids_alert", "payload": {"simulate_error": True}},
        timeout=5,
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    done = wait_until(task_id, "done", timeout=90)
    assert done["policy"]["executed"] is False
    audit = httpx.get(f"{BASE}/tasks/{task_id}/audit", timeout=5)
    kinds = [event["event_type"] for event in audit.json()["events"]]
    assert "task.delivery_failed" in kinds
    assert "task.completed" in kinds


def test_reject_empty_event_type() -> None:
    response = httpx.post(f"{BASE}/tasks", json={"event_type": "", "payload": {}}, timeout=5)
    assert response.status_code == 422
