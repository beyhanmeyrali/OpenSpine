"""Principal-context middleware.

Runs on every incoming request. Resolves the caller's identity (if
present) and parks the result on `request.state.principal_context`.
Service-layer code reads from there.

Identity resolution order:

1. `Authorization: Bearer osp_*` header → look up `id_token` by
   sha256(plaintext), check expiry/revocation, load principal.
2. `openspine_session` cookie → look up `id_session` by sha256(value),
   check expiry, load principal.
3. Neither → `PrincipalContext.anonymous`.

The middleware does NOT enforce that protected routes have a
principal — the auth-object engine in §4.3 does that via the
`@requires_auth` decorator. v0.1 routes that need a principal check
`request.state.principal_context.is_anonymous` directly.

For tenant-scoped requests, the middleware also sets
`SET LOCAL openspine.tenant_id = '<uuid>'` on the database session
before the route runs, so RLS sees the correct tenant. This is the
*only* place that issues that statement; the rule "every query is
RLS-scoped" depends on it being correct here.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from openspine.db import SessionFactory
from openspine.identity.context import PrincipalContext
from openspine.identity.models import IdPrincipal, IdSession, IdToken
from openspine.identity.security import hash_token_plaintext

logger = structlog.get_logger(__name__)

SESSION_COOKIE_NAME = "openspine_session"
_BEARER_PREFIX = "Bearer "

# Per-request DB session, exposed for FastAPI dependencies. Lives in a
# ContextVar so the middleware can hand the request handler an already-
# scoped session without threading it through arguments.
_request_session: contextvars.ContextVar[AsyncSession | None] = contextvars.ContextVar(
    "openspine_request_session", default=None
)


def get_request_session() -> AsyncSession:
    """Dependency for routes that need the current request's DB session."""
    sess = _request_session.get()
    if sess is None:
        raise RuntimeError(
            "No request-scoped DB session. PrincipalContextMiddleware "
            "must be installed on the FastAPI app."
        )
    return sess


class PrincipalContextMiddleware(BaseHTTPMiddleware):
    """Resolve identity per request and set the RLS tenant.

    Runs on every request, including health/metrics. Anonymous calls
    are cheap: no DB lookup, no transaction.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = _ensure_trace_id(request)

        # Anonymous fast-path: no credential present, no DB session needed.
        bearer = _extract_bearer(request)
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if bearer is None and cookie is None:
            request.state.principal_context = PrincipalContext.anonymous(trace_id=trace_id)
            request.state.trace_id = trace_id
            request.state.principal_id = None
            return await call_next(request)

        # Authenticated path: open a session, resolve principal, set
        # RLS tenant, then run the request inside that session.
        async with SessionFactory() as session:
            ctx = await _resolve_principal(session, bearer=bearer, cookie=cookie, trace_id=trace_id)
            request.state.principal_context = ctx
            request.state.trace_id = ctx.trace_id
            request.state.principal_id = str(ctx.principal_id) if ctx.principal_id else None

            if ctx.tenant_id is not None:
                # RLS hook. The GUC name matches the policy in
                # `migrations/versions/0002_identity_core.py`. The
                # `true` second arg means "missing setting is OK"
                # (matches the policy's `current_setting(..., true)`).
                await session.execute(_SET_TENANT_SQL.bindparams(tenant_id=str(ctx.tenant_id)))

            token = _request_session.set(session)
            try:
                response = await call_next(request)
                # Touch last_seen / last_used outside the request handler
                # so route code doesn't have to remember it.
                await _touch_credential_last_seen(session, ctx)
                await session.commit()
                return response
            except Exception:
                await session.rollback()
                raise
            finally:
                _request_session.reset(token)


# ---------------------------------------------------------------------------


def _ensure_trace_id(request: Request) -> uuid.UUID:
    """Return the inbound trace id, or generate one.

    `traceparent` per W3C trace-context contains a 16-byte trace id at
    chars 3..35. We extract it for join-key consistency with OTel
    spans. If absent or malformed, generate a fresh UUID4.
    """
    header = request.headers.get("traceparent")
    if header:
        # traceparent: "00-<32 hex trace id>-<16 hex span id>-<flags>"
        parts = header.split("-")
        if len(parts) >= 2 and len(parts[1]) == 32:
            try:
                return uuid.UUID(parts[1])
            except ValueError:
                pass
    return uuid.uuid4()


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if not auth or not auth.startswith(_BEARER_PREFIX):
        return None
    candidate = auth[len(_BEARER_PREFIX) :].strip()
    return candidate or None


async def _resolve_principal(
    session: AsyncSession,
    *,
    bearer: str | None,
    cookie: str | None,
    trace_id: uuid.UUID,
) -> PrincipalContext:
    """Try bearer token first, then session cookie. Return anonymous on no match.

    Lookups use SHA-256 of the plaintext as the unique key (see
    `openspine.identity.security`). Both paths verify expiry and
    revocation.
    """
    now = datetime.now(UTC)

    if bearer:
        token_row = await _load_active_token(session, bearer, now=now)
        if token_row is not None:
            principal = await session.get(IdPrincipal, token_row.principal_id)
            if principal is not None and principal.status == "active":
                return PrincipalContext(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.id,
                    principal_kind=principal.kind,
                    auth_method="token",
                    trace_id=trace_id,
                )

    if cookie:
        session_row = await _load_active_session(session, cookie, now=now)
        if session_row is not None:
            principal = await session.get(IdPrincipal, session_row.principal_id)
            if principal is not None and principal.status == "active":
                return PrincipalContext(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.id,
                    principal_kind=principal.kind,
                    auth_method="session",
                    trace_id=trace_id,
                )

    return PrincipalContext.anonymous(trace_id=trace_id)


async def _load_active_token(
    session: AsyncSession, plaintext: str, *, now: datetime
) -> IdToken | None:
    """Look up a token by sha256(plaintext); return only if active.

    "Active" = not revoked, not expired. Caller updates `last_used_at`
    in the post-route hook so we don't write on every miss.
    """
    secret_hash = hash_token_plaintext(plaintext)
    stmt = select(IdToken).where(IdToken.secret_hash == secret_hash)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= now:
        return None
    return row


async def _load_active_session(
    session: AsyncSession, plaintext: str, *, now: datetime
) -> IdSession | None:
    secret_hash = hash_token_plaintext(plaintext)
    stmt = select(IdSession).where(IdSession.session_hash == secret_hash)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    if row.status != "active" or row.revoked_at is not None:
        return None
    if row.idle_expires_at <= now or row.absolute_expires_at <= now:
        return None
    return row


async def _touch_credential_last_seen(session: AsyncSession, ctx: PrincipalContext) -> None:
    """Update last_used_at / last_seen_at on the credential the request used.

    Best-effort: failures here must not fail the request. We swallow
    errors at this point because the request itself has already
    succeeded.
    """
    if ctx.is_anonymous:
        return
    now = datetime.now(UTC)
    try:
        if ctx.auth_method == "token":
            await session.execute(
                _UPDATE_TOKEN_LAST_USED.bindparams(
                    last_used=now, principal_id=str(ctx.principal_id)
                )
            )
        elif ctx.auth_method == "session":
            await session.execute(
                _UPDATE_SESSION_LAST_SEEN.bindparams(
                    last_seen=now, principal_id=str(ctx.principal_id)
                )
            )
    except Exception:  # pragma: no cover  (defensive — never observed)
        logger.warning("audit.credential_touch_failed", principal_id=str(ctx.principal_id))


# ---------------------------------------------------------------------------
# SQL constants (kept module-level so they aren't re-parsed per request)
# ---------------------------------------------------------------------------

from sqlalchemy import text  # noqa: E402

_SET_TENANT_SQL = text("SET LOCAL openspine.tenant_id = :tenant_id")

# Touch last_used on whichever active token this principal authed with.
# We don't know which token row the bearer was without re-hashing it
# here, so we touch all active tokens for the principal — there is
# typically only one in flight at a time. Simpler than threading the
# row through.
_UPDATE_TOKEN_LAST_USED = text(
    """
    UPDATE id_token
       SET last_used_at = :last_used
     WHERE principal_id = :principal_id
       AND revoked_at IS NULL
       AND (expires_at IS NULL OR expires_at > now())
    """
)

_UPDATE_SESSION_LAST_SEEN = text(
    """
    UPDATE id_session
       SET last_seen_at = :last_seen
     WHERE principal_id = :principal_id
       AND status = 'active'
       AND revoked_at IS NULL
    """
)


def _opaque_unused() -> Any:
    """Internal: keep this importable so unit tests can monkeypatch."""
    return None


__all__ = [
    "SESSION_COOKIE_NAME",
    "PrincipalContextMiddleware",
    "get_request_session",
]
