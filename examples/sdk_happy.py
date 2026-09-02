"""Example happy flow via Python SDK — Fase 8 DX."""

from procurement_sdk import ProcurementClient

# Use local API (docker compose up) or test via MockTransport
# For demo without server, we use mocked transport via TestClient in tests
# Here we run against real API if available, else fallback to mock

def run_with_real_api():
    client = ProcurementClient(base_url="http://localhost:8000")
    try:
        # health
        print("health", client.health())
        # create
        resp = client.create_execution({
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
            "horizon_days": 21,
            "location_id": "warehouse_north",
        })
        print("created", resp["execution_id"], resp["approval_request"]["approval_id"])
        approval_id = resp["approval_request"]["approval_id"]
        # get
        detail = client.get_execution(resp["execution_id"])
        print("status", detail["status"])
        # approve
        dec = client.approve(approval_id, decided_by="approver_01")
        print("approve", dec)
        # verify completed
        final = client.get_execution(resp["execution_id"])
        print("final status", final["status"])
        # events
        events = client.list_events(resp["execution_id"], format="trace")
        print("events", events.get("count"), "trace", events.get("trace_id"))
        # list executions pagination
        page = client.list_executions(tenant_id="tenant_demo", limit=5)
        print("list executions", page.get("count") or page.get("total_count"))
    except Exception as e:
        print(f"real API not available: {e}")
        print("fallback to mock demo (see sdk/python/tests/test_client.py)")

if __name__ == "__main__":
    run_with_real_api()
