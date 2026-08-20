"""Servidor HTTP fake de Agent Station — para docker-compose y desarrollo local."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from procurement_platform.integrations.agent_station.dtos import ExecutionUpdateCallbackDTO
from procurement_platform.integrations.agent_station.fake import FakeAgentStation

app = FastAPI(title="agent-station-fake", version="0.1.0")
_fake = FakeAgentStation()


@app.get("/v1/health")
async def health():
    return {"status": "ok", "service": "agent-station-fake"}


@app.post("/v1/callbacks/execution-update")
async def callback(payload: ExecutionUpdateCallbackDTO, request: Request):
    # optional: check signature if X-Signature present (noop for fake)
    status = await _fake.receive_callback(payload)
    if status != 200:
        return JSONResponse(
            status_code=status,
            content={"code": "temporarily_unavailable", "message": "injected failure"},
        )
    return {"status": "accepted", "execution_id": payload.execution_id}


@app.get("/v1/callbacks")
async def list_callbacks():
    return {
        "count": len(_fake.callbacks),
        "callbacks": [c.model_dump(mode="json") for c in _fake.callbacks],
    }


@app.post("/v1/admin/clear")
async def clear():
    _fake.clear()
    return {"status": "cleared"}


@app.post("/v1/admin/inject-failures")
async def inject(n: int = 1, status: int = 503):
    _fake.inject_failures(n, status)
    return {"injected": n, "status": status}
