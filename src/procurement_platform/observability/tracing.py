"""OTEL tracing F5-1 — TracerProvider + auto-instrumentation lazy.

- Si PROCUREMENT_OTEL_EXPORTER=none (default) usa NoOp tracer (no export, no overhead).
- Si otlp/otlp+http, intenta BatchSpanProcessor + OTLPSpanExporter (lazy import).
- Si console, usa ConsoleSpanExporter.
- Correlaciona trace_id/span_id en contextvars para logging.
- Instrumenta FastAPI, SQLAlchemy, Redis de forma lazy (si libs disponibles).
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

# contextvars for logging correlation
span_id_ctx: ContextVar[str | None] = ContextVar("span_id", default=None)

_tracer_provider_set = False
_tracer = None


def _get_exporter(settings_exporter: str | None = None):
    exp = (settings_exporter or os.getenv("PROCUREMENT_OTEL_EXPORTER", "none")).lower()
    if exp in ("none", "", "off", "disabled"):
        return None, "none"
    if exp in ("console", "stdout"):
        try:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter  # type: ignore

            return ConsoleSpanExporter(), "console"
        except Exception:
            return None, "none"
    if exp in ("otlp", "otlp_grpc", "otlp_http", "otlp+http"):
        # try grpc first, then http
        for mod_path, cls_name in [
            ("opentelemetry.exporter.otlp.proto.grpc.trace_exporter", "OTLPSpanExporter"),
            ("opentelemetry.exporter.otlp.proto.http.trace_exporter", "OTLPSpanExporter"),
        ]:
            try:
                mod = __import__(mod_path, fromlist=[cls_name])
                exporter_cls = getattr(mod, cls_name)
                # allow endpoint via OTEL_EXPORTER_OTLP_ENDPOINT or settings
                endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
                    "PROCUREMENT_OTEL_ENDPOINT"
                )
                if endpoint:
                    return exporter_cls(endpoint=endpoint), "otlp"  # type: ignore
                return exporter_cls(), "otlp"  # type: ignore
            except Exception:
                continue
        return None, "none"
    return None, "none"


def setup_tracing(app=None, settings_exporter: str | None = None) -> Any:
    """Inicializa TracerProvider global. Llamar al startup de FastAPI.

    - Idempotente.
    - Si app provisto y libs instrumentación disponibles, instrumenta FastAPI/SQLAlchemy/Redis.
    """
    global _tracer_provider_set, _tracer
    if _tracer_provider_set:
        return _tracer

    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.semconv.resource import ResourceAttributes  # type: ignore
    except Exception:
        # opentelemetry not fully available, fallback no-op
        _tracer_provider_set = True
        return None

    # already has provider? don't override if set by tests
    try:
        from opentelemetry import trace as _trace

        if isinstance(_trace.get_tracer_provider(), TracerProvider):  # type: ignore
            _tracer_provider_set = True
            _tracer = _trace.get_tracer("procurement_platform")  # type: ignore
            return _tracer
    except Exception:
        pass

    exporter, kind = _get_exporter(settings_exporter)
    try:
        resource = Resource.create({ResourceAttributes.SERVICE_NAME: "procurement-platform"})  # type: ignore
    except Exception:
        resource = Resource.create({})  # type: ignore

    provider = TracerProvider(resource=resource)  # type: ignore

    if exporter is not None:
        try:
            from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore

            provider.add_span_processor(BatchSpanProcessor(exporter))  # type: ignore
        except Exception:
            # fallback simple
            try:
                from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # type: ignore

                provider.add_span_processor(SimpleSpanProcessor(exporter))  # type: ignore
            except Exception:
                pass

    try:
        from opentelemetry import trace as otel_trace

        otel_trace.set_tracer_provider(provider)  # type: ignore
    except Exception:
        pass

    try:
        _tracer = trace.get_tracer("procurement_platform")  # type: ignore
    except Exception:
        _tracer = None

    # auto-instrumentations lazy
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore

            FastAPIInstrumentor.instrument_app(app)  # type: ignore
        except Exception:
            pass
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # type: ignore
            from procurement_platform.persistence.database import get_engine

            SQLAlchemyInstrumentor().instrument(
                engine=get_engine().sync_engine
                if hasattr(get_engine(), "sync_engine")
                else get_engine()
            )  # type: ignore
        except Exception:
            pass
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor  # type: ignore

            RedisInstrumentor().instrument()  # type: ignore
        except Exception:
            pass

    _tracer_provider_set = True
    return _tracer


def get_tracer(name: str = "procurement_platform"):
    try:
        from opentelemetry import trace  # type: ignore

        return trace.get_tracer(name)  # type: ignore
    except Exception:
        return None


def get_current_span_context() -> tuple[str | None, str | None]:
    """Retorna (trace_id hex, span_id hex) del span actual si hay, else None."""
    try:
        from opentelemetry import trace  # type: ignore

        span = trace.get_current_span()  # type: ignore
        ctx = span.get_span_context()  # type: ignore
        if ctx and ctx.is_valid:  # type: ignore
            trace_id = format(ctx.trace_id, "032x")  # type: ignore
            span_id = format(ctx.span_id, "016x")  # type: ignore
            return trace_id, span_id
    except Exception:
        pass
    # fallback to contextvars set by middleware
    try:
        from procurement_platform.observability.logging import trace_id_ctx

        tid = trace_id_ctx.get()
        sid = span_id_ctx.get()
        return tid, sid
    except Exception:
        return None, None


def set_span_context(trace_id: str | None, span_id: str | None) -> None:
    try:
        from procurement_platform.observability.logging import trace_id_ctx

        if trace_id:
            trace_id_ctx.set(trace_id)
        if span_id:
            span_id_ctx.set(span_id)
    except Exception:
        pass


def reset_tracing() -> None:
    global _tracer_provider_set, _tracer
    _tracer_provider_set = False
    _tracer = None
    # Note: OTEL global provider cannot be easily unset; we just reset flag
