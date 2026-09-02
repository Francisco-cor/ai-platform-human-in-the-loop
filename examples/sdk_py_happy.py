"""SDK Python happy flow — Fase 8 DX (duplicate of sdk_happy.py for examples/)."""

from procurement_sdk import ProcurementClient

client = ProcurementClient(base_url="http://localhost:8000")

# create
resp = client.create_execution({
    "tenant_id": "tenant_demo",
    "requester_id": "user_01",
    "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]
})
print("created", resp["execution_id"], resp["approval_request"]["approval_id"])

# list paginated
page = client.list_executions(tenant_id="tenant_demo", limit=2)
print("list executions", page["total_count"], "has_more", page["has_more"])

# get and approve
aid = resp["approval_request"]["approval_id"]
detail = client.get_approval(aid)
print("approval status", detail["status"], "total", detail["total"])

dec = client.approve(aid, decided_by="approver_01")
print("approve", dec["status"])

# verify
final = client.get_execution(resp["execution_id"])
print("final", final["status"])

# events
events = client.list_events(resp["execution_id"], limit=5, format="trace")
print("events", events["count"], "trace", events.get("trace_id"))

# webhooks
wh = client.create_webhook("http://webhook.site/test", "secret123", ["execution.completed"])
print("webhook", wh["id"])
print("webhooks list", client.list_webhooks(tenant_id="tenant_demo")["count"])
