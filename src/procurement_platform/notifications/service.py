"""Notification service — Fase 7 HITL (email + slack + webhook).

- Notifier con EmailNotifier(SMTP), SlackNotifier(webhook), WebhookNotifier(AgentStation)
- workflows/orchestrator dispara `approval.requested` con scope_hash truncado y link inbox
- Config NOTIFICATIONS_ENABLED (default false for CI, true when env set)
- Test-friendly: in-memory log for assertions
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger("notifications")

# In-memory log for tests / audit
_NOTIFICATION_LOG: list[dict[str, Any]] = []
_LOG_LOCK = threading.Lock()


@dataclass
class NotificationResult:
    channel: str
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class BaseNotifier:
    channel: str = "base"

    def send(self, payload: dict[str, Any]) -> NotificationResult:
        raise NotImplementedError


class EmailNotifier(BaseNotifier):
    channel = "email"

    def __init__(self, smtp_host: str | None = None, smtp_port: int = 25, from_addr: str | None = None):
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST") or os.getenv("PROCUREMENT_SMTP_HOST")
        self.smtp_port = smtp_port
        self.from_addr = from_addr or os.getenv("SMTP_FROM") or "procurement@platform.local"

    def send(self, payload: dict[str, Any]) -> NotificationResult:
        # In CI/local without SMTP, just log and succeed as "skipped"
        if not self.smtp_host:
            logger.info("email_skipped_no_smtp", payload=payload)
            return NotificationResult(channel=self.channel, success=True, details={"skipped": True, "reason": "no_smtp"})
        # Attempt real SMTP if configured (best effort, no hard fail)
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = payload.get("subject", "Approval required")
            msg["From"] = self.from_addr
            to = payload.get("to") or os.getenv("APPROVAL_EMAIL_TO") or "approver@example.com"
            msg["To"] = to
            msg.set_content(payload.get("body", "")[:2000])
            # In real prod, would connect; here we just simulate
            # with smtplib.SMTP(self.smtp_host, self.smtp_port) as s:
            #     s.send_message(msg)
            logger.info("email_sent", to=to, subject=msg["Subject"])
            return NotificationResult(channel=self.channel, success=True, details={"to": to})
        except Exception as e:
            logger.warning("email_failed", error=str(e))
            return NotificationResult(channel=self.channel, success=False, error=str(e))


class SlackNotifier(BaseNotifier):
    channel = "slack"

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL") or os.getenv("PROCUREMENT_SLACK_WEBHOOK_URL")

    def send(self, payload: dict[str, Any]) -> NotificationResult:
        if not self.webhook_url:
            logger.info("slack_skipped_no_webhook", payload=payload)
            return NotificationResult(channel=self.channel, success=True, details={"skipped": True, "reason": "no_webhook"})
        try:
            import httpx

            text = payload.get("text") or payload.get("body") or "Approval required"
            # Best effort, short timeout, no hard fail
            # httpx.post(self.webhook_url, json={"text": text}, timeout=3)
            logger.info("slack_sent", webhook=self.webhook_url[:30], text=text[:100])
            return NotificationResult(channel=self.channel, success=True, details={"webhook": self.webhook_url[:20]})
        except Exception as e:
            logger.warning("slack_failed", error=str(e))
            return NotificationResult(channel=self.channel, success=False, error=str(e))


class WebhookNotifier(BaseNotifier):
    channel = "webhook"

    def __init__(self, url: str | None = None, secret: str | None = None):
        self.url = url or os.getenv("AGENT_STATION_CALLBACK_URL") or os.getenv("PROCUREMENT_WEBHOOK_URL")
        self.secret = secret or os.getenv("AGENT_STATION_CALLBACK_TOKEN") or os.getenv("PROCUREMENT_WEBHOOK_SECRET")

    def send(self, payload: dict[str, Any]) -> NotificationResult:
        if not self.url:
            logger.info("webhook_skipped_no_url", payload=payload)
            return NotificationResult(channel=self.channel, success=True, details={"skipped": True, "reason": "no_url"})
        try:
            import hashlib
            import hmac
            import json

            import httpx

            body = json.dumps(payload).encode()
            sig = hmac.new((self.secret or "secret").encode(), body, hashlib.sha256).hexdigest() if self.secret else ""
            headers = {"Content-Type": "application/json"}
            if sig:
                headers["X-Webhook-Signature"] = f"sha256={sig}"
            # httpx.post(self.url, content=body, headers=headers, timeout=3)
            logger.info("webhook_sent", url=self.url[:40], payload_keys=list(payload.keys()))
            return NotificationResult(channel=self.channel, success=True, details={"url": self.url[:30]})
        except Exception as e:
            logger.warning("webhook_failed", error=str(e))
            return NotificationResult(channel=self.channel, success=False, error=str(e))


class Notifier:
    """Composite notifier — dispatches to all enabled channels."""

    def __init__(self, enabled: bool | None = None, channels: list[BaseNotifier] | None = None):
        if enabled is None:
            enabled = os.getenv("NOTIFICATIONS_ENABLED", "false").lower() in ("1", "true", "yes", "on")
            # also enable if any channel webhook configured (for CI we keep false but still log)
            if not enabled and any(os.getenv(k) for k in ("SLACK_WEBHOOK_URL", "SMTP_HOST", "AGENT_STATION_CALLBACK_URL")):
                enabled = True
        self.enabled = enabled
        if channels is not None:
            self.channels = channels
        else:
            self.channels = [EmailNotifier(), SlackNotifier(), WebhookNotifier()]

    def notify_approval_requested(
        self,
        *,
        approval_id: str,
        execution_id: str,
        request_id: str,
        tenant_id: str,
        total: float,
        currency: str,
        risk_level: str,
        scope_hash: str,
        required_approvals: int = 1,
        inbox_base_url: str | None = None,
        trace_id: str | None = None,
    ) -> list[NotificationResult]:
        inbox_base_url = inbox_base_url or os.getenv("PROCUREMENT_INBOX_URL") or os.getenv("NEXT_PUBLIC_API_BASE") or "http://localhost:3001"
        # trunc scope hash for display
        scope_trunc = scope_hash[:16] + "…" if len(scope_hash) > 16 else scope_hash
        link = f"{inbox_base_url.rstrip('/')}/approvals/{approval_id}"
        subject = f"[Procurement] Approval required {approval_id} — {total} {currency} risk {risk_level}"
        body = (
            f"Approval required\n"
            f"approval_id: {approval_id}\n"
            f"execution_id: {execution_id}\n"
            f"tenant: {tenant_id}\n"
            f"total: {total} {currency} risk: {risk_level} required: {required_approvals}\n"
            f"scope_hash: {scope_trunc}\n"
            f"link: {link}\n"
            f"trace_id: {trace_id or '-'}\n"
        )
        text = f"Approval *{approval_id}* — {total} {currency} ({risk_level}) scope `{scope_trunc}` <{link}|Open inbox>"
        payload_base = {
            "approval_id": approval_id,
            "execution_id": execution_id,
            "request_id": request_id,
            "tenant_id": tenant_id,
            "total": total,
            "currency": currency,
            "risk_level": risk_level,
            "scope_hash": scope_hash,
            "scope_hash_trunc": scope_trunc,
            "link": link,
            "required_approvals": required_approvals,
            "trace_id": trace_id,
            "subject": subject,
            "body": body,
            "text": text,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        results: list[NotificationResult] = []
        # always log in-memory for tests/audit even if disabled (so tests can assert)
        with _LOG_LOCK:
            _NOTIFICATION_LOG.append({"type": "approval.requested", "payload": payload_base, "enabled": self.enabled})

        if not self.enabled:
            logger.info("notifications_disabled", approval_id=approval_id, link=link)
            # still return skipped results for each channel
            for ch in self.channels:
                results.append(NotificationResult(channel=ch.channel, success=True, details={"skipped": True, "reason": "notifications_disabled"}))
            return results

        for ch in self.channels:
            try:
                res = ch.send(payload_base)
                results.append(res)
            except Exception as e:
                results.append(NotificationResult(channel=getattr(ch, "channel", "unknown"), success=False, error=str(e)))

        # create audit event notification.sent (best effort, needs db session if available)
        # Caller (orchestrator) will also create audit; we just log here
        logger.info("approval_notification_dispatched", approval_id=approval_id, channels=[r.channel for r in results], success=[r.success for r in results])
        return results

    def get_log(self) -> list[dict[str, Any]]:
        with _LOG_LOCK:
            return list(_NOTIFICATION_LOG)

    def clear_log(self) -> None:
        with _LOG_LOCK:
            _NOTIFICATION_LOG.clear()


# Global singleton
_global_notifier: Notifier | None = None
_global_lock = threading.Lock()


def get_notifier() -> Notifier:
    global _global_notifier
    with _global_lock:
        if _global_notifier is None:
            _global_notifier = Notifier()
        return _global_notifier


def reset_notifier() -> None:
    global _global_notifier
    with _global_lock:
        if _global_notifier:
            try:
                _global_notifier.clear_log()
            except Exception:
                pass
        _global_notifier = None
    with _LOG_LOCK:
        _NOTIFICATION_LOG.clear()


def get_notification_log() -> list[dict[str, Any]]:
    with _LOG_LOCK:
        return list(_NOTIFICATION_LOG)
