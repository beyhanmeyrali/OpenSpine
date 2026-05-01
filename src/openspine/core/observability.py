"""OpenTelemetry + Prometheus wiring (v0.1 §4.8).

Two surfaces:

1. **OpenTelemetry tracing.** `configure_tracing()` sets up a tracer
   provider with an OTLP exporter pointing at the configured endpoint.
   `instrument_app(app)` wires FastAPI auto-instrumentation. SQLAlchemy
   instrumentation lives next to the engine in `db.py` once that module
   adopts it. Future event-bus producers/consumers propagate context
   manually via the W3C trace-context spec.

2. **Prometheus metrics.** A small registry of OpenSpine-specific
   counters/histograms. The `/metrics` endpoint exposes them in
   Prometheus text format.

Both are no-op-friendly: if OTel/Prom imports fail (lean dev installs),
the module still imports and instrumentation calls become no-ops.
"""

from __future__ import annotations

import logging

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from openspine.config import Settings

logger = structlog.get_logger(__name__)


# A dedicated registry so OpenSpine's metrics don't collide with library
# defaults if multiple apps share a process (rare but cheap to do right).
metrics_registry = CollectorRegistry()


# ---- Domain-shaped Prometheus metrics --------------------------------------

http_requests_total = Counter(
    "openspine_http_requests_total",
    "Total HTTP requests handled by the application.",
    labelnames=("method", "route", "status"),
    registry=metrics_registry,
)

http_request_duration_seconds = Histogram(
    "openspine_http_request_duration_seconds",
    "Histogram of request handling duration in seconds.",
    labelnames=("method", "route", "status"),
    registry=metrics_registry,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

hook_dispatch_duration_seconds = Histogram(
    "openspine_hook_dispatch_duration_seconds",
    "Time spent dispatching a single hook handler.",
    labelnames=("hook", "kind"),  # kind in {pre, post}
    registry=metrics_registry,
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

events_published_total = Counter(
    "openspine_events_published_total",
    "Events published to the event bus.",
    labelnames=("stream",),
    registry=metrics_registry,
)

events_consumed_total = Counter(
    "openspine_events_consumed_total",
    "Events consumed from the event bus.",
    labelnames=("stream", "consumer", "outcome"),  # outcome in {ok, error, retried}
    registry=metrics_registry,
)

embedding_indexed_total = Counter(
    "openspine_embedding_indexed_total",
    "Documents successfully indexed into Qdrant.",
    labelnames=("collection",),
    registry=metrics_registry,
)

embedding_index_lag_seconds = Histogram(
    "openspine_embedding_index_lag_seconds",
    "Time from event publication to successful Qdrant upsert.",
    registry=metrics_registry,
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0),
)

auth_decisions_total = Counter(
    "openspine_auth_decisions_total",
    "Authorisation decisions made by the auth-object engine.",
    labelnames=("domain", "action", "decision"),  # decision in {allow, deny, sod_block}
    registry=metrics_registry,
)


def configure_tracing(settings: Settings) -> None:
    """Set up an OTel tracer provider with OTLP gRPC export.

    Idempotent — calling twice is a no-op (the second TracerProvider is
    silently ignored by OTel).
    """
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": _safe_version(),
            "deployment.environment": settings.env,
        }
    )
    provider = TracerProvider(resource=resource)
    try:
        exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint, insecure=True
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:  # pragma: no cover — best-effort in local dev
        logger.warning(
            "otel.exporter.disabled",
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
    trace.set_tracer_provider(provider)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)


def instrument_app(app: object) -> None:
    """Attach FastAPI auto-instrumentation to the running app.

    Called from the lifespan handler in main.py.
    """
    try:
        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover — auto-instrumentation is best-effort
        logger.exception("otel.fastapi.instrumentation_failed")


def metrics_response_body() -> tuple[bytes, str]:
    """Render the Prometheus exposition format for the OpenSpine registry."""
    return generate_latest(metrics_registry), CONTENT_TYPE_LATEST


def _safe_version() -> str:
    try:
        from openspine import __version__

        return __version__
    except Exception:  # pragma: no cover
        return "unknown"
