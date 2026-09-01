"""Auth — JWT/OIDC + API key fallback (F3-1).

- Bearer JWT (HS256) verified via PROCUREMENT_JWT_SECRET (local/ci), or RS256 JWKS for prod (future).
- X-API-Key fallback for local/legacy.
- Missing auth -> anonymous principal (tenant_demo/requester) for backward compat ci/local.
- Returns Principal with tenant_id, roles, sub.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Literal

from fastapi import Header, HTTPException, Request


@dataclass
class Principal:
    sub: str
    tenant_id: str
    roles: list[str]
    auth_method: Literal["jwt", "api_key", "anonymous"]


def _decode_jwt_payload(token: str) -> dict | None:
    try:
        # JWT: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload_json = base64.urlsafe_b64decode(payload_b64.encode()).decode()
        return json.loads(payload_json)
    except Exception:
        return None


def _verify_jwt(token: str, secret: str | None = None) -> dict | None:
    # Simplified verification: for ci/local, accept any JWT if secret not set (just decode)
    # If secret set, verify signature HS256 (manual)
    payload = _decode_jwt_payload(token)
    if payload is None:
        return None
    if not secret:
        return payload
    # verify HS256 signature if secret provided
    try:
        import hashlib
        import hmac

        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = (
            base64.urlsafe_b64encode(
                hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        if sig_b64.rstrip("=") != expected_sig:
            return None
        return payload
    except Exception:
        return None


def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """FastAPI dependency — returns Principal, never 401 in ci/local without token (anonymous)."""
    from procurement_platform.config.settings import get_settings

    settings = get_settings()
    jwt_secret = getattr(settings, "jwt_secret", None)  # optional
    # Try Bearer
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        payload = _verify_jwt(token, secret=jwt_secret)
        if payload:
            # expect sub, tenant_id, roles
            sub = payload.get("sub", "user_jwt")
            tenant_id = payload.get("tenant_id") or payload.get("tid") or "tenant_demo"
            roles = payload.get("roles") or payload.get("role") or ["requester"]
            if isinstance(roles, str):
                roles = [roles]
            return Principal(
                sub=sub, tenant_id=str(tenant_id), roles=list(roles), auth_method="jwt"
            )
        # invalid jwt -> 401 if not ci
        if settings.app_env not in ("ci", "local"):
            raise HTTPException(
                status_code=401, detail={"code": "invalid_token", "message": "invalid JWT"}
            )
        # ci/local fallback to anonymous if invalid
    if x_api_key:
        # simple api key -> map to principal (demo: key == tenant)
        # In prod, verify via Secret Manager
        tenant = x_api_key.split("_")[0] if "_" in x_api_key else "tenant_demo"
        return Principal(
            sub=f"apikey_{x_api_key[:6]}",
            tenant_id=tenant,
            roles=["requester", "approver"],
            auth_method="api_key",
        )
    # anonymous for backward compat (ci/local)
    if settings.app_env in ("ci", "local"):
        return Principal(
            sub="anonymous",
            tenant_id="tenant_demo",
            roles=["requester", "approver"],
            auth_method="anonymous",
        )
    raise HTTPException(
        status_code=401, detail={"code": "unauthorized", "message": "missing credentials"}
    )
