"""Unit tests for the readiness orchestrator.

The probes themselves can't be unit-tested without their target
services. The orchestrator's logic — required vs optional, overall
status, payload shape — is testable with stubbed probes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openspine.config import get_settings
from openspine.core import readiness as r


def _stub(status: str, detail: str | None = None) -> r.ProbeResult:
    # Use cast-style typing-friendly construction.
    return r.ProbeResult(status, detail)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_check_all_ready_when_required_deps_ok() -> None:
    settings = get_settings()
    with (
        patch.object(r, "check_postgres", return_value=_stub("ok")),
        patch.object(r, "check_redis", return_value=_stub("ok")),
        patch.object(r, "check_qdrant", return_value=_stub("ok")),
        patch.object(r, "check_ollama", return_value=_stub("ok")),
    ):
        status, deps = await r.check_all(settings)
    assert status == "ready"
    assert deps["postgres"] == {"status": "ok"}
    assert set(deps.keys()) == {"postgres", "redis", "qdrant", "ollama"}


@pytest.mark.asyncio
async def test_check_all_not_ready_when_postgres_down() -> None:
    settings = get_settings()
    with (
        patch.object(r, "check_postgres", return_value=_stub("down", "timeout")),
        patch.object(r, "check_redis", return_value=_stub("ok")),
        patch.object(r, "check_qdrant", return_value=_stub("ok")),
        patch.object(r, "check_ollama", return_value=_stub("ok")),
    ):
        status, deps = await r.check_all(settings)
    assert status == "not_ready"
    assert deps["postgres"]["status"] == "down"
    assert deps["postgres"]["detail"] == "timeout"


@pytest.mark.asyncio
async def test_check_all_ready_when_only_optional_deps_down() -> None:
    """Qdrant + Ollama down should NOT flip overall status."""
    settings = get_settings()
    with (
        patch.object(r, "check_postgres", return_value=_stub("ok")),
        patch.object(r, "check_redis", return_value=_stub("ok")),
        patch.object(r, "check_qdrant", return_value=_stub("down", "timeout")),
        patch.object(r, "check_ollama", return_value=_stub("down", "timeout")),
    ):
        status, deps = await r.check_all(settings)
    assert status == "ready"
    assert deps["qdrant"]["status"] == "down"
    assert deps["ollama"]["status"] == "down"


@pytest.mark.asyncio
async def test_check_all_not_ready_when_redis_down() -> None:
    settings = get_settings()
    with (
        patch.object(r, "check_postgres", return_value=_stub("ok")),
        patch.object(r, "check_redis", return_value=_stub("down", "ConnectionError")),
        patch.object(r, "check_qdrant", return_value=_stub("ok")),
        patch.object(r, "check_ollama", return_value=_stub("ok")),
    ):
        status, _deps = await r.check_all(settings)
    assert status == "not_ready"


@pytest.mark.asyncio
async def test_degraded_required_dep_counts_as_not_ready() -> None:
    settings = get_settings()
    with (
        patch.object(r, "check_postgres", return_value=_stub("degraded", "slow")),
        patch.object(r, "check_redis", return_value=_stub("ok")),
        patch.object(r, "check_qdrant", return_value=_stub("ok")),
        patch.object(r, "check_ollama", return_value=_stub("ok")),
    ):
        status, _deps = await r.check_all(settings)
    assert status == "not_ready"
