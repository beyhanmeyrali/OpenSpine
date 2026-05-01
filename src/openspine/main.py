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

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openspine import __version__
from openspine.config import get_settings
from openspine.core.errors import OpenSpineError
from openspine.core.hooks import registered_hooks
from openspine.core.logging import configure_logging

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("openspine.startup", version=__version__, env=settings.env)
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
