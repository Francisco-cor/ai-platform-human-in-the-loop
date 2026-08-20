"""Fake in-memory de Agent Station — para desarrollo y CI (Fase 0).

No implementa lógica de negocio; solo almacena callbacks recibidos y expone
endpoints mínimos para que el cliente pueda ser probado sin depender de un servicio real.

Uso en tests:
    from procurement_platform.integrations.agent_station.fake import FakeAgentStation
    fake = FakeAgentStation()
    payload = ExecutionUpdateCallbackDTO(...)
    await fake.receive_callback(payload)

Uso como servidor HTTP (docker-compose):
    uvicorn procurement_platform.integrations.agent_station.fake_server:app --port 8001
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from procurement_platform.integrations.agent_station.dtos import ExecutionUpdateCallbackDTO


@dataclass
class FakeAgentStation:
    """Fake sincrónico / asíncrono en memoria."""

    max_events: int = 1000
    _callbacks: deque[ExecutionUpdateCallbackDTO] = field(
        default_factory=lambda: deque(maxlen=1000)
    )
    _fail_next_n: int = 0
    _fail_status: int = 503

    async def receive_callback(self, payload: ExecutionUpdateCallbackDTO) -> int:
        if self._fail_next_n > 0:
            self._fail_next_n -= 1
            return self._fail_status
        self._callbacks.append(payload)
        return 200

    def inject_failures(self, n: int, status: int = 503) -> None:
        self._fail_next_n = n
        self._fail_status = status

    @property
    def callbacks(self) -> list[ExecutionUpdateCallbackDTO]:
        return list(self._callbacks)

    def last_callback(self) -> ExecutionUpdateCallbackDTO | None:
        return self._callbacks[-1] if self._callbacks else None

    def clear(self) -> None:
        self._callbacks.clear()
        self._fail_next_n = 0

    def executions_notified(self) -> set[str]:
        return {c.execution_id for c in self._callbacks}

    def was_notified(self, execution_id: str) -> bool:
        return any(c.execution_id == execution_id for c in self._callbacks)
