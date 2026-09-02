"""ProcurementClient — Fase 8 Python SDK.

Auto Idempotency-Key, retries, pagination, trace headers.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx


class ProcurementError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str | None = None, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details


class ProcurementClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers, transport=transport)

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, json: Any | None = None, params: dict | None = None) -> dict[str, Any] | list[Any] | str:
        # Auto Idempotency-Key for POST that creates
        hdrs = dict(headers or {})
        if method.upper() == "POST" and path in ("/v1/procurement/executions", "/v1/approvals/bulk/decision") and "Idempotency-Key" not in hdrs:
            hdrs["Idempotency-Key"] = f"sdk_{uuid.uuid4().hex[:16]}"
        if method.upper() == "POST" and path.startswith("/v1/approvals/") and path.endswith("/decision") and "Idempotency-Key" not in hdrs:
            hdrs["Idempotency-Key"] = f"sdk_{uuid.uuid4().hex[:16]}"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.request(method, path, headers=hdrs, json=json, params=params)
                # retry on 429/5xx
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else (0.5 * (2**attempt))
                    time.sleep(min(wait, 5))
                    continue
                if resp.status_code >= 400:
                    try:
                        data = resp.json()
                        code = data.get("code") if isinstance(data, dict) else None
                        msg = data.get("message") if isinstance(data, dict) else resp.text
                    except Exception:
                        code = None
                        msg = resp.text
                    raise ProcurementError(msg or f"HTTP {resp.status_code}", status_code=resp.status_code, code=code, details=resp.text)
                # success
                ctype = resp.headers.get("content-type", "")
                if "text/csv" in ctype:
                    return resp.text  # type: ignore
                if resp.content:
                    try:
                        return resp.json()
                    except Exception:
                        return resp.text  # type: ignore
                return {}
            except ProcurementError:
                raise
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise ProcurementError(str(e)) from e
        raise ProcurementError(str(last_exc) if last_exc else "request failed")

    # ------------------------------------------------------------------
    # Executions
    # ------------------------------------------------------------------
    def create_execution(self, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        hdrs = {}
        if idempotency_key:
            hdrs["Idempotency-Key"] = idempotency_key
        return self._request("POST", "/v1/procurement/executions", headers=hdrs, json=payload)  # type: ignore

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/procurement/executions/{execution_id}")  # type: ignore

    def list_executions(self, tenant_id: str | None = None, state: str | None = None, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if tenant_id:
            params["tenant_id"] = tenant_id
        if state:
            params["state"] = state
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/procurement/executions", params=params)  # type: ignore

    def resume(self, execution_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/procurement/executions/{execution_id}/resume", json={})  # type: ignore

    def list_events(self, execution_id: str, limit: int = 50, cursor: str | None = None, format: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if format:
            params["format"] = format
        return self._request("GET", f"/v1/procurement/executions/{execution_id}/events", params=params)  # type: ignore

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------
    def get_approval(self, approval_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/approvals/{approval_id}")  # type: ignore

    def list_approvals(self, tenant_id: str | None = None, state: str | None = None, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if tenant_id:
            params["tenant_id"] = tenant_id
        if state:
            params["state"] = state
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/approvals", params=params)  # type: ignore

    def approve(self, approval_id: str, decided_by: str, reason: str | None = None, scope_hash: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"decision": "approved", "decided_by": decided_by}
        if reason:
            payload["reason"] = reason
        if scope_hash:
            payload["scope_hash"] = scope_hash
        hdrs = {}
        if idempotency_key:
            hdrs["Idempotency-Key"] = idempotency_key
        return self._request("POST", f"/v1/approvals/{approval_id}/decision", headers=hdrs, json=payload)  # type: ignore

    def reject(self, approval_id: str, decided_by: str, reason: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/v1/approvals/{approval_id}/decision", json={"decision": "rejected", "decided_by": decided_by, "reason": reason})  # type: ignore

    def bulk_decide(self, approval_ids: list[str], decision: str, decided_by: str, reason: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/v1/approvals/bulk/decision", json={"approval_ids": approval_ids, "decision": decision, "decided_by": decided_by, "reason": reason})  # type: ignore

    def export_approvals(self, tenant_id: str | None = None, state: str | None = None) -> str:
        params: dict[str, Any] = {}
        if tenant_id:
            params["tenant_id"] = tenant_id
        if state:
            params["state"] = state
        return self._request("GET", "/v1/approvals/export", params=params)  # type: ignore

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    def create_webhook(self, url: str, secret: str, events: list[str], tenant_id: str = "tenant_demo") -> dict[str, Any]:
        return self._request("POST", "/v1/webhooks/subscriptions", json={"url": url, "secret": secret, "events": events, "tenant_id": tenant_id})  # type: ignore

    def list_webhooks(self, tenant_id: str | None = None) -> dict[str, Any]:
        params = {"tenant_id": tenant_id} if tenant_id else {}
        return self._request("GET", "/v1/webhooks/subscriptions", params=params)  # type: ignore

    # ------------------------------------------------------------------
    # Ops
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/healthz")  # type: ignore

    def readyz(self) -> dict[str, Any]:
        return self._request("GET", "/readyz")  # type: ignore

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
