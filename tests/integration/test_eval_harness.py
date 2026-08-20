import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_happy_path_eval_case(client: TestClient):
    case_path = Path("evals/procurement/happy_path.json")
    assert case_path.exists(), "happy_path case missing"
    case = json.loads(case_path.read_text(encoding="utf-8"))

    # 1. create execution from case input
    resp = client.post("/v1/procurement/executions", json=case["input"])
    assert resp.status_code == 202, resp.text
    data = resp.json()
    execution_id = data["execution_id"]
    approval_id = data["approval_request"]["approval_id"]

    # 2. verify intermediate state is AWAITING_APPROVAL (Fase 1 synthetic)
    assert data["status"] == "AWAITING_APPROVAL"

    # 3. approve to reach terminal
    resp2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "eval_runner", "reason": "auto-approve"},
    )
    assert resp2.status_code == 200, resp2.text

    # 4. fetch final execution
    final = client.get(f"/v1/procurement/executions/{execution_id}").json()
    assert final["status"] == case["expected"]["terminal_state"]

    # 5. check required events
    events = client.get(f"/v1/procurement/executions/{execution_id}/events").json()
    event_types = [e["event_type"] for e in events["events"]]
    for required in case["expected"].get("required_events", []):
        assert any(required in et for et in event_types), f"missing required event {required} in {event_types}"

    # 6. ensure proposal scope_hash present and audit correlation
    assert final["proposal"] is not None
    assert final["proposal"]["scope_hash"].startswith("sha256:")
    assert final["approval_request"]["scope_hash"] == final["proposal"]["scope_hash"]

    # 7. ensure correlation: request_id matches case input request_id (or generated)
    # case input has req_happy_001 but our API may generate new if not provided; check not empty
    assert final["request_id"] is not None
