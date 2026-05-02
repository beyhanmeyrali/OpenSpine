"""FastAPI application entrypoint.

Run with `make run` or `uvicorn openspine.main:app --reload`.

This is intentionally thin in v0.1 — the heavy lifting goes into the domain
modules (identity, md, fi, co, mm, pp). The app's job here is to:

- configure logging
- create the FastAPI instance with module routers attached
- register the structured-error exception handler
- expose health, readiness, and meta endpoints
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from openspine import __version__
from openspine.config import get_settings
from openspine.core.errors import OpenSpineError
from openspine.core.hooks import registered_hooks
from openspine.core.logging import configure_logging
from openspine.core.observability import (
    configure_tracing,
    http_request_duration_seconds,
    http_requests_total,
    instrument_app,
    metrics_response_body,
)
from openspine.core.plugins import load_all as load_plugins
from openspine.core.plugins import loaded_plugins

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings)
    instrument_app(app)
    plugins = load_plugins(app=app)
    logger.info(
        "openspine.startup",
        version=__version__,
        env=settings.env,
        plugins_total=len(plugins),
        plugins_loaded=sum(1 for p in plugins if p.state == "loaded"),
    )
    yield
    logger.info("openspine.shutdown")


app = FastAPI(
    title="OpenSpine",
    version=__version__,
    description=(
        "Open-source, AI-native ERP. Every endpoint is designed for agent "
        "consumption first, human consumption second. See "
        "https://github.com/beyhanmeyrali/openspine"
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "system", "description": "Health, readiness, meta endpoints."},
        {"name": "identity", "description": "Principals, auth, roles, permissions."},
        {"name": "master-data", "description": "Tenant, Company Code, BP, Material, CoA."},
        {"name": "finance", "description": "Universal journal, GL, AP, AR. (v0.2)"},
        {"name": "controlling", "description": "Cost/profit centres, allocations. (v0.2.x)"},
        {"name": "materials", "description": "Procure-to-pay. (v0.3)"},
        {"name": "production", "description": "Plan-to-produce. (v0.4)"},
    ],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count and latency per (method, route, status).

    Uses the matched route template (e.g. `/system/health`) rather than the
    raw path so cardinality stays bounded. Unmatched paths are bucketed
    under `unmatched`.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        route_template = getattr(route, "path", None) or "unmatched"
        labels = {
            "method": request.method,
            "route": route_template,
            "status": str(response.status_code),
        }
        http_requests_total.labels(**labels).inc()
        http_request_duration_seconds.labels(**labels).observe(elapsed)
        return response


app.add_middleware(MetricsMiddleware)


@app.exception_handler(OpenSpineError)
async def openspine_error_handler(request: Request, exc: OpenSpineError) -> JSONResponse:
    """Serialise every domain error to the structured envelope.

    Per `docs/identity/permissions.md` §"Denial semantics" — errors carry
    enough context for an agent to reason about the failure.
    """
    principal_id = getattr(request.state, "principal_id", None)
    trace_id = getattr(request.state, "trace_id", None)
    payload = exc.to_response(principal_id=principal_id, trace_id=trace_id)
    return JSONResponse(
        status_code=exc.http_status,
        content=payload.model_dump(exclude_none=True),
    )


@app.get("/system/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 when the process is up."""
    return {"status": "ok", "version": __version__}


@app.get("/system/readiness", tags=["system"])
async def readiness() -> dict[str, Any]:
    """Readiness probe.

    Real implementation lands with the Postgres / Redis / Qdrant client wiring
    in §4.2 / §4.5. v0.1 stub returns the dependencies as `unknown` so the
    contract is in place from day one.
    """
    return {
        "status": "ok",
        "dependencies": {
            "postgres": "unknown",
            "redis": "unknown",
            "qdrant": "unknown",
            "ollama": "unknown",
        },
    }


@app.get("/system/hooks", tags=["system"])
async def list_hooks() -> dict[str, dict[str, int]]:
    """Introspect registered plugin hooks.

    Useful for debugging plugin loading and for agents that want to discover
    the available extension surface.
    """
    return registered_hooks()


@app.get("/system/plugins", tags=["system"])
async def list_plugins() -> list[dict[str, Any]]:
    """Plugins known to the host — loaded, skipped, or failed.

    Useful for operators verifying a deployment, and for agents that want
    to discover what extension surface a tenant has installed.
    """
    out: list[dict[str, Any]] = []
    for p in loaded_plugins():
        out.append(
            {
                "plugin_id": p.plugin_id,
                "package": p.package,
                "state": p.state,
                "reason": p.reason,
                "loaded_at": p.loaded_at.isoformat(),
                "version": p.manifest.version if p.manifest else None,
                "openspine_compatible": (p.manifest.openspine_compatible if p.manifest else None),
                "hooks": ([h.name for h in p.manifest.hooks] if p.manifest else []),
                "custom_fields": (
                    [f"{f.entity}.{f.field}" for f in p.manifest.custom_fields]
                    if p.manifest
                    else []
                ),
            }
        )
    return out


@app.get("/metrics", tags=["system"], include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition endpoint.

    Excluded from the OpenAPI schema because the wire format is text,
    not JSON, and clients consuming it (Prometheus scrapers) don't read
    OpenAPI.
    """
    body, content_type = metrics_response_body()
    return Response(content=body, media_type=content_type)
