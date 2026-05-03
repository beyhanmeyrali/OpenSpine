"""Real `/system/readiness` checks.

Each dependency check returns one of:
- `"ok"` — reachable and responsive within the timeout
- `"degraded"` — reachable but slow/erroring (response inside the
  budget but the response itself is not what we wanted)
- `"down"` — not reachable inside the timeout

The orchestrator (`check_all`) runs every probe concurrently behind
a tight per-probe timeout (default 1 second) so a single slow
dependency cannot stall the readiness endpoint past the
load-balancer's tolerance.

Postgres + Redis are **required** — if either is `down`, overall
status is `not_ready` and the endpoint returns 503. Qdrant + Ollama
are **optional** — the system runs in degraded mode (semantic search
falls back to ILIKE, embedding worker queues without a target) but
serves the rest of the API.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Literal

import httpx
import redis.asyncio as aioredis
import structlog
from sqlalchemy import text

from openspine.config import Settings
from openspine.db import engine

logger = structlog.get_logger(__name__)

CheckStatus = Literal["ok", "degraded", "down"]
# A first call against a freshly-created engine can take a couple of
# hundred ms to open a connection. 2 seconds is generous for a steady-
# state probe and forgiving for the cold-start case.
_DEFAULT_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class ProbeResult:
    status: CheckStatus
    detail: str | None = None


async def check_postgres(*, timeout_s: float = _DEFAULT_TIMEOUT_S) -> ProbeResult:
    try:
        async with asyncio.timeout(timeout_s):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                row = result.scalar_one()
                if row != 1:
                    return ProbeResult("degraded", f"unexpected SELECT 1 result: {row!r}")
        return ProbeResult("ok")
    except TimeoutError:
        return ProbeResult("down", "timeout")
    except Exception as exc:
        return ProbeResult("down", type(exc).__name__)


async def check_redis(settings: Settings, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> ProbeResult:
    client: aioredis.Redis[bytes] | None = None
    try:
        async with asyncio.timeout(timeout_s):
            client = aioredis.from_url(str(settings.redis_url))
            # ping() returns True / b'PONG' / 'PONG' depending on
            # connection pool state. We only care that it didn't raise.
            await client.ping()
        return ProbeResult("ok")
    except TimeoutError:
        return ProbeResult("down", "timeout")
    except Exception as exc:
        return ProbeResult("down", type(exc).__name__)
    finally:
        if client is not None:
            # `aclose` exists from redis-py 5.0 onwards; older falls back
            # to `close`. Both are coroutine-safe.
            closer = getattr(client, "aclose", None) or getattr(client, "close", None)
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer()


async def check_qdrant(settings: Settings, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> ProbeResult:
    url = f"{settings.qdrant_url.rstrip('/')}/readyz"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
        if response.status_code == 200:
            return ProbeResult("ok")
        return ProbeResult("degraded", f"http {response.status_code}")
    except (TimeoutError, httpx.TimeoutException):
        return ProbeResult("down", "timeout")
    except Exception as exc:
        return ProbeResult("down", type(exc).__name__)


async def check_ollama(settings: Settings, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> ProbeResult:
    """Ollama doesn't expose a dedicated readiness path; /api/tags is
    the cheapest call that exercises the daemon end-to-end."""
    url = f"{settings.ollama_url.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
        if response.status_code == 200:
            return ProbeResult("ok")
        return ProbeResult("degraded", f"http {response.status_code}")
    except (TimeoutError, httpx.TimeoutException):
        return ProbeResult("down", "timeout")
    except Exception as exc:
        return ProbeResult("down", type(exc).__name__)


# Probes that gate overall readiness. Down on any of these → 503.
_REQUIRED = ("postgres", "redis")


async def check_all(
    settings: Settings, *, timeout_s: float = _DEFAULT_TIMEOUT_S
) -> tuple[str, dict[str, dict[str, str | None]]]:
    """Run every probe concurrently. Return overall_status, dep_map.

    `overall_status` is `"ready"` if every required dep is `"ok"`,
    otherwise `"not_ready"`. Optional deps degraded or down do not
    flip the overall status.
    """
    pg, rd, qd, ol = await asyncio.gather(
        check_postgres(timeout_s=timeout_s),
        check_redis(settings, timeout_s=timeout_s),
        check_qdrant(settings, timeout_s=timeout_s),
        check_ollama(settings, timeout_s=timeout_s),
    )
    deps: dict[str, ProbeResult] = {
        "postgres": pg,
        "redis": rd,
        "qdrant": qd,
        "ollama": ol,
    }
    overall: str = "ready"
    for name in _REQUIRED:
        if deps[name].status != "ok":
            overall = "not_ready"
            break
    out_deps: dict[str, dict[str, str | None]] = {}
    for name, r in deps.items():
        entry: dict[str, str | None] = {"status": r.status}
        if r.detail:
            entry["detail"] = r.detail
        out_deps[name] = entry
    return overall, out_deps


__all__ = [
    "ProbeResult",
    "check_all",
    "check_ollama",
    "check_postgres",
    "check_qdrant",
    "check_redis",
]
