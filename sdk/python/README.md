# procurement-sdk-py

Python SDK for Procurement Platform (Fase 8 API Platform). Auto-generated from OpenAPI + manual wrapper with retries and `Idempotency-Key`.

## Install

```bash
pip install -e sdk/python  # local
# or
pip install procurement-sdk-py
```

## Quickstart

```python
from procurement_sdk import ProcurementClient

client = ProcurementClient(base_url="http://localhost:8000", api_key="test")

# create execution (auto Idempotency-Key, retries 429/5xx)
resp = client.create_execution({
    "tenant_id": "tenant_demo",
    "requester_id": "user_01",
    "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]
})
print(resp["execution_id"], resp["approval_request"]["approval_id"])

# poll
exec_detail = client.get_execution(resp["execution_id"])
print(exec_detail["status"])

# approve
approval_id = resp["approval_request"]["approval_id"]
dec = client.approve(approval_id, decided_by="approver_01")
print(dec["status"])  # approved -> COMPLETED

# resume if needed
client.resume(resp["execution_id"])

# events with pagination
events = client.list_events(resp["execution_id"], limit=10)
print(events["total"], events["events"][0]["event_type"])

# list executions with pagination
page = client.list_executions(tenant_id="tenant_demo", state="COMPLETED", limit=10)
print(page["total_count"], page["has_more"])
```

See `examples/sdk_happy.py`.

## Features

- `Idempotency-Key` auto per `create_execution` and `approve`
- retries with backoff for 429/5xx + `Retry-After`
- pagination helpers `has_more`/`next_cursor`
- trace propagation `X-Request-Id`/`X-Trace-Id`
- sync via `httpx.Client`, timeout 15s, max_retries 3
