"""Custom REST routes for the example plugin.

The plugin manifest declares `prefix: /example` and points at this module.
The plugin host's route-mounting machinery (lands with §4.6 hardening
beyond the v0.1 skeleton) imports `router` from here and mounts it.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/greeting")
async def greeting() -> dict[str, str]:
    """A trivial endpoint demonstrating that a plugin can expose new APIs.

    Real plugins would declare `requires_auth("example.greeting", "read")`
    on the route once §4.3 (auth-object engine) lands.
    """
    return {"message": "Hello from the OpenSpine example plugin."}
