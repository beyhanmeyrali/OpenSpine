"""Integration test for /system/readiness against the live stack.

Requires `docker compose up -d` (Postgres, Redis, Qdrant, Ollama).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from openspine.main import app

pytestmark = pytest.mark.integration


async def test_readiness_returns_200_when_stack_is_up() -> None:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/system/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["postgres"]["status"] == "ok"
    assert body["dependencies"]["redis"]["status"] == "ok"
    # Qdrant and Ollama may be `ok` or `degraded`/`down` depending on
    # whether the dev pulled the embedding model. The test just checks
    # they didn't break the overall status.
    assert body["dependencies"]["qdrant"]["status"] in ("ok", "degraded", "down")
    assert body["dependencies"]["ollama"]["status"] in ("ok", "degraded", "down")
