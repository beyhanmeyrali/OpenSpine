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
from openspine.agents.router import router as agents_router
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
from openspine.core.readiness import check_all as readiness_check_all
from openspine.fi.router import router as fi_router
from openspine.identity.middleware import PrincipalContextMiddleware
from openspine.identity.router import router as identity_router
from openspine.md.router import router as md_router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    from openspine.workers.indexer import register_indexer

    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings)
    instrument_app(app)
    plugins = load_plugins(app=app)
    # Register the in-process embedding indexer against the bus. With
    # the InMemoryEventBus this means MD service publishes are
    # delivered synchronously into the indexer; with a future Redis
    # bus the worker would run as a separate process and consume the
    # same stream pattern.
    await register_indexer()
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


# Order matters: middlewares wrap in reverse-add order, so the
# PrincipalContextMiddleware (added second) runs FIRST per request.
# That makes principal_context available inside the metrics middleware
# (useful for per-tenant metric labelling once we add it).
app.add_middleware(MetricsMiddleware)
app.add_middleware(PrincipalContextMiddleware)
app.include_router(identity_router)
app.include_router(md_router)
app.include_router(fi_router)
app.include_router(agents_router)


@app.exception_handler(OpenSpineError)
async def openspine_error_handler(request: Request, exc: OpenSpineError) -> JSONResponse:
    """Serialise every domain error to the structured envelope.

    Per `docs/identity/permissions.md` §"Denial semantics" — errors carry
    enough context for an agent to reason about the failure.
    """
    principal_id = getattr(request.state, "principal_id", None)
    trace_id = getattr(request.state, "trace_id", None)
    payload = exc.to_response(
        principal_id=str(principal_id) if principal_id is not None else None,
        trace_id=str(trace_id) if trace_id is not None else None,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=payload.model_dump(exclude_none=True),
    )


@app.get("/system/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 when the process is up."""
    return {"status": "ok", "version": __version__}


@app.get("/system/readiness", tags=["system"])
async def readiness() -> Response:
    """Readiness probe with real dependency checks.

    Postgres + Redis are required (down → 503). Qdrant + Ollama are
    optional — degraded means semantic features fall back to ILIKE
    + queue, the rest of the API serves normally.
    """
    settings = get_settings()
    status_str, deps = await readiness_check_all(settings)
    body = {"status": status_str, "version": __version__, "dependencies": deps}
    http_status = 200 if status_str == "ready" else 503
    return JSONResponse(content=body, status_code=http_status)


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


@app.post("/system/reconcile-embeddings", tags=["system"])
async def reconcile_embeddings(request: Request) -> Response:
    """Re-index every MD entity in the caller's tenant into Qdrant.

    The v0.1 §3 #4 acceptance criterion: "Killing Qdrant and running
    reconciliation rebuilds the index without manual intervention."
    Authority gate: `system.tenant:configure` (the same gate that
    protects other tenant-wide admin operations).
    """
    import uuid as _uuid

    from openspine.core.errors import AuthenticationError
    from openspine.identity.authz import enforce
    from openspine.identity.context import PrincipalContext
    from openspine.identity.middleware import get_request_session
    from openspine.workers.indexer import reconcile_tenant

    ctx: PrincipalContext = getattr(request.state, "principal_context", None) or (
        PrincipalContext.anonymous(trace_id=_uuid.uuid4())
    )
    if ctx.is_anonymous or ctx.tenant_id is None:
        raise AuthenticationError(
            "authentication required",
            domain="auth",
            action="access",
            reason="not_authenticated",
        )
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="system.tenant", action="configure")
    counts = await reconcile_tenant(tenant_id=str(ctx.tenant_id), session=session)
    return JSONResponse(content={"status": "ok", "indexed": counts})


@app.get("/metrics", tags=["system"], include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition endpoint.

    Excluded from the OpenAPI schema because the wire format is text,
    not JSON, and clients consuming it (Prometheus scrapers) don't read
    OpenAPI.
    """
    body, content_type = metrics_response_body()
    return Response(content=body, media_type=content_type)
