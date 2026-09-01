"""Structured logging (Fase 1) — JSON to stdout when PROCUREMENT_LOG_LEVEL.

Uses structlog for JSON rendering and correlation ids.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)
span_id_ctx: ContextVar[str | None] = ContextVar("span_id", default=None)


def _redact_pii_processor(logger, method_name, event_dict):  # type: ignore
    """Structlog processor Fase 7 — redacta PII en todos los logs."""
    try:
        from procurement_platform.security.pii import redact_pii

        for k, v in list(event_dict.items()):
            if isinstance(v, str) and len(v) > 5:
                redacted, det = redact_pii(v)
                if det["has_pii"]:
                    event_dict[k] = redacted
                    event_dict[f"{k}_pii_redacted"] = True
    except Exception:
        pass
    # también redactar secrets conocidos
    try:
        for k in list(event_dict.keys()):
            lk = k.lower()
            if any(s in lk for s in ("api_key", "token", "secret", "password")):
                if isinstance(event_dict[k], str) and event_dict[k]:
                    event_dict[k] = "[REDACTED_SECRET]"
    except Exception:
        pass
    return event_dict


def _otel_correlation_processor(logger, method_name, event_dict):  # type: ignore
    """F5-1: añade trace_id/span_id desde OTel o contextvars."""
    try:
        from procurement_platform.observability.tracing import get_current_span_context

        tid, sid = get_current_span_context()
        if tid and "trace_id" not in event_dict:
            event_dict["trace_id"] = tid
        if sid and "span_id" not in event_dict:
            event_dict["span_id"] = sid
    except Exception:
        pass
    # fallback to contextvars if still missing
    try:
        if "trace_id" not in event_dict:
            tid = trace_id_ctx.get()
            if tid:
                event_dict["trace_id"] = tid
        if "span_id" not in event_dict:
            sid = span_id_ctx.get()
            if sid:
                event_dict["span_id"] = sid
        if "request_id" not in event_dict:
            rid = request_id_ctx.get()
            if rid:
                event_dict["request_id"] = rid
    except Exception:
        pass
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _otel_correlation_processor,
            _redact_pii_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "procurement_platform"):
    return structlog.get_logger(name)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"
