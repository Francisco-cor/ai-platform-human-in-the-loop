import pytest

from procurement_platform.config.settings import Settings
from procurement_platform.integrations.agent_station.client import AgentStationClient
from procurement_platform.integrations.agent_station.dtos import ExecutionUpdateCallbackDTO
from procurement_platform.integrations.agent_station.fake import FakeAgentStation


@pytest.mark.asyncio
async def test_fake_records_callback():
    fake = FakeAgentStation()
    payload = ExecutionUpdateCallbackDTO(execution_id="exec_123", request_id="req_123", tenant_id="tenant_demo", status="COMPLETED")
    status = await fake.receive_callback(payload)
    assert status == 200
    assert fake.was_notified("exec_123")
    assert len(fake.callbacks) == 1


@pytest.mark.asyncio
async def test_client_callback_disabled():
    settings = Settings(_env_file=None, PROCUREMENT_APP_ENV="ci", AGENT_STATION_CALLBACK_ENABLED=False, AGENT_STATION_BASE_URL="http://localhost:8001")  # type: ignore
    client = AgentStationClient(settings=settings)
    payload = ExecutionUpdateCallbackDTO(execution_id="exec_1", request_id="req_1", tenant_id="tenant_demo", status="COMPLETED")
    result = await client.notify_execution_update(payload)
    assert result is False
    await client.close()


@pytest.mark.asyncio
async def test_client_circuit_breaker():
    from procurement_platform.integrations.agent_station.client import CircuitBreaker

    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=0.1)
    breaker.record_failure()
    assert not breaker.is_open
    breaker.record_failure()
    assert breaker.is_open
    # wait reset
    import asyncio

    await asyncio.sleep(0.15)
    assert not breaker.is_open
