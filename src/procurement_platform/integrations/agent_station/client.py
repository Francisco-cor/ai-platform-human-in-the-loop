"""AgentStationClient — HTTP aislado con retries, circuit breaker e idempotencia (Fase 0).

Solo depende de DTOs externos y de la configuración; nunca de modelos internos ni de DB.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass, field

import httpx

from procurement_platform.config.settings import Settings, get_settings
from procurement_platform.integrations.agent_station.dtos import (
    AgentStationErrorDTO,
    ExecutionUpdateCallbackDTO,
)


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout_s: float = 30.0
    _failures: int = 0
    _opened_at: float | None = None

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self.reset_timeout_s:
            # half-open: allow one trial
            self._opened_at = None
            self._failures = 0
            return False
        return True


@dataclass
class AgentStationClient:
    """Cliente HTTP para callbacks hacia Agent Station.

    Uso:
        client = AgentStationClient()  # lee Settings
        await client.notify_execution_update(payload)
    """

    settings: Settings = field(default_factory=get_settings)
    _client: httpx.AsyncClient | None = None
    _breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    # transient codes that are safe to retry
    RETRYABLE_STATUS = {429, 502, 503, 504}
    MAX_RETRIES = 3
    BACKOFF_BASE_S = 0.2

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = self.settings.agent_station_timeout_ms / 1000.0
            headers = {}
            if self.settings.agent_station_api_token:
                headers["Authorization"] = f"Bearer {self.settings.agent_station_api_token}"
            self._client = httpx.AsyncClient(
                base_url=self.settings.agent_station_base_url or "http://localhost:8001",
                timeout=timeout,
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _sign(self, body: bytes) -> str | None:
        token = self.settings.platform_callback_token
        if not token:
            return None
        return hmac.new(token.encode(), body, hashlib.sha256).hexdigest()

    async def notify_execution_update(self, payload: ExecutionUpdateCallbackDTO) -> bool:
        """Best-effort callback; retorna True si 2xx, False si skipped o error no retryable.

        No debe bloquear el workflow; los errores se registran pero no propagan como fallo del workflow.
        """
        if not self.settings.agent_station_callback_enabled:
            return False
        if not self.settings.agent_station_base_url:
            return False
        if self._breaker.is_open:
            raise CircuitOpenError("circuit open — skipping callback")

        body = payload.model_dump_json().encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        sig = self._sign(body)
        if sig:
            headers["X-Signature"] = f"sha256={sig}"
        if payload.trace_id:
            headers["traceparent"] = payload.trace_id

        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                client = self._get_client()
                resp = await client.post("/v1/callbacks/execution-update", content=body, headers=headers)
                if resp.status_code in self.RETRYABLE_STATUS:
                    last_exc = RuntimeError(f"retryable {resp.status_code}: {resp.text[:500]}")
                    self._breaker.record_failure()
                elif 200 <= resp.status_code < 300:
                    self._breaker.record_success()
                    return True
                else:
                    # non-retryable
                    self._breaker.record_failure()
                    return False
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                self._breaker.record_failure()
            # backoff before next retry
            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(self.BACKOFF_BASE_S * (2**attempt) + 0.05 * attempt)
        # exhausted
        return False

    async def health_check(self) -> bool:
        if not self.settings.agent_station_base_url:
            return False
        try:
            client = self._get_client()
            resp = await client.get("/v1/health", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def parse_error(resp: httpx.Response, request_id: str | None = None) -> AgentStationErrorDTO:
        try:
            data = resp.json()
            return AgentStationErrorDTO.model_validate(data)
        except Exception:
            return AgentStationErrorDTO(
                code="unknown",
                message=resp.text[:500],
                request_id=request_id,
            )
