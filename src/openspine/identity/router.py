"""Auth HTTP surface — login, logout, token CRUD, TOTP enrolment.

Thin layer over `openspine.identity.service`. The router validates
the request shape, delegates to the service layer, and wraps
results in the structured-response shape every OpenSpine endpoint
uses (`_meta` block per ARCHITECTURE.md §7).

Routes:

| Method | Path                       | Auth required | Notes |
|--------|----------------------------|---------------|-------|
| POST   | /auth/login                | no            | password (+ optional TOTP) |
| POST   | /auth/logout               | yes (session) | revokes the current session |
| GET    | /auth/me                   | yes           | the principal context |
| POST   | /auth/tokens               | yes           | issue api/agent/service token |
| DELETE | /auth/tokens/{token_id}    | yes           | revoke a token |
| POST   | /auth/totp/enrol           | yes (session) | generate secret + URI |
| POST   | /auth/totp/verify          | yes (session) | confirm first code |
| POST   | /auth/principals/{id}/roles| yes + ROLE_ASSIGN | assign role binding |
| DELETE | /auth/principals/.../{rid} | yes + ROLE_ASSIGN | revoke role binding |
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.config import get_settings
from openspine.core.errors import AuthenticationError, NotFoundError, ValidationError
from openspine.db import SessionFactory
from openspine.identity import service
from openspine.identity.context import PrincipalContext
from openspine.identity.middleware import SESSION_COOKIE_NAME, get_request_session
from openspine.identity.models import IdPrincipal, IdSession

router = APIRouter(prefix="/auth", tags=["identity"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    tenant_slug: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    totp_code: str | None = Field(default=None, min_length=6, max_length=6)


class LoginResponse(BaseModel):
    principal_id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str
    requires_totp: bool = False


class MeResponse(BaseModel):
    principal_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    principal_kind: str | None
    auth_method: str
    is_anonymous: bool


class IssueTokenRequest(BaseModel):
    kind: str = Field(description="user_api | agent | service")
    target_principal_id: uuid.UUID | None = Field(default=None)
    scope: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    expires_at: datetime | None = None


class IssueTokenResponse(BaseModel):
    token_id: uuid.UUID
    plaintext: str  # shown ONCE
    prefix: str
    kind: str
    expires_at: datetime | None


class RevokeTokenRequest(BaseModel):
    revocation_reason: str | None = None


class TotpEnrolResponse(BaseModel):
    secret: str  # shown ONCE; user must scan into authenticator
    provisioning_uri: str


class TotpVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TotpVerifyResponse(BaseModel):
    verified: bool


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _principal_context(request: Request) -> PrincipalContext:
    ctx: PrincipalContext = getattr(request.state, "principal_context", None) or (
        PrincipalContext.anonymous(trace_id=uuid.uuid4())
    )
    return ctx


def _require_principal(request: Request) -> tuple[PrincipalContext, AsyncSession]:
    """Return (context, session) for an authenticated request.

    The 401 short-circuits here without ever asking for a DB session,
    so anonymous calls don't trip `get_request_session`'s "no session"
    guard (the middleware fast-paths anonymous requests and never
    opens one).
    """
    ctx = _principal_context(request)
    if ctx.is_anonymous:
        raise AuthenticationError(
            "authentication required",
            domain="auth",
            action="access",
            reason="not_authenticated",
        )
    return ctx, get_request_session()


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Password (+ optional TOTP) login.

    The endpoint runs **anonymous-by-default** — the principal-context
    middleware skips the DB session for unauthenticated requests, so
    we open one explicitly here. Successful login sets the session
    cookie on `response`.
    """
    settings = get_settings()
    trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4()
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None

    async with SessionFactory() as db:
        try:
            result = await service.login_password(
                db,
                tenant_slug=payload.tenant_slug,
                username=payload.username,
                password=payload.password,
                totp_code=payload.totp_code,
                user_agent=user_agent,
                ip_address=ip,
                idle_minutes=settings.session_idle_minutes,
                absolute_hours=settings.session_absolute_hours,
                trace_id=trace_id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    if result.requires_totp:
        # No session issued; the caller must retry with `totp_code`.
        # We still surface the principal so the client can render
        # "now enter your TOTP code".
        assert result.principal is not None
        return LoginResponse(
            principal_id=result.principal.id,
            tenant_id=result.principal.tenant_id,
            display_name=result.principal.display_name,
            requires_totp=True,
        )

    assert result.principal is not None
    assert result.session_plaintext is not None
    response.set_cookie(
        SESSION_COOKIE_NAME,
        result.session_plaintext,
        httponly=True,
        secure=settings.env != "local",
        samesite="lax",
        max_age=settings.session_absolute_hours * 3600,
    )
    return LoginResponse(
        principal_id=result.principal.id,
        tenant_id=result.principal.tenant_id,
        display_name=result.principal.display_name,
    )


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie is None:
        # Idempotent — logging out without a session is a no-op.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    from openspine.identity.security import hash_token_plaintext

    cookie_hash = hash_token_plaintext(cookie)
    trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4()
    async with SessionFactory() as db:
        from sqlalchemy import select

        stmt = select(IdSession).where(IdSession.session_hash == cookie_hash)
        sess_row = (await db.execute(stmt)).scalar_one_or_none()
        if sess_row is not None and sess_row.status == "active":
            await service.revoke_session(db, session_row=sess_row, trace_id=trace_id)
            await db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    ctx = _principal_context(request)
    return MeResponse(
        principal_id=ctx.principal_id,
        tenant_id=ctx.tenant_id,
        principal_kind=ctx.principal_kind,
        auth_method=ctx.auth_method,
        is_anonymous=ctx.is_anonymous,
    )


# ---------------------------------------------------------------------------
# /auth/tokens
# ---------------------------------------------------------------------------


@router.post("/tokens", response_model=IssueTokenResponse, status_code=status.HTTP_201_CREATED)
async def issue_token_endpoint(
    payload: IssueTokenRequest,
    request: Request,
    auth: tuple[PrincipalContext, AsyncSession] = Depends(_require_principal),
) -> IssueTokenResponse:
    ctx, session = auth
    target_id = payload.target_principal_id or ctx.principal_id
    if target_id is None:
        raise ValidationError(
            "target_principal_id required",
            domain="auth.token",
            action="issue",
            reason="target_required",
        )
    issuer = await session.get(IdPrincipal, ctx.principal_id)
    if issuer is None:
        raise AuthenticationError(
            "issuer principal not found",
            domain="auth",
            action="access",
            reason="principal_missing",
        )
    result = await service.issue_principal_token(
        session,
        issuer_principal=issuer,
        target_principal_id=target_id,
        kind=payload.kind,
        scope=payload.scope,
        reason=payload.reason,
        expires_at=payload.expires_at,
        trace_id=ctx.trace_id,
    )
    return IssueTokenResponse(
        token_id=result.row.id,
        plaintext=result.plaintext,
        prefix=result.row.prefix,
        kind=result.row.kind,
        expires_at=result.row.expires_at,
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token_endpoint(
    token_id: uuid.UUID,
    payload: RevokeTokenRequest | None = None,
    auth: tuple[PrincipalContext, AsyncSession] = Depends(_require_principal),
) -> Response:
    ctx, session = auth
    revoker = await session.get(IdPrincipal, ctx.principal_id)
    if revoker is None:
        raise AuthenticationError(
            "revoker principal not found",
            domain="auth",
            action="access",
            reason="principal_missing",
        )
    await service.revoke_token(
        session,
        revoker=revoker,
        token_id=token_id,
        revocation_reason=(payload.revocation_reason if payload else None),
        trace_id=ctx.trace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# /auth/totp
# ---------------------------------------------------------------------------


@router.post("/totp/enrol", response_model=TotpEnrolResponse)
async def totp_enrol_endpoint(
    auth: tuple[PrincipalContext, AsyncSession] = Depends(_require_principal),
) -> TotpEnrolResponse:
    ctx, session = auth
    principal = await session.get(IdPrincipal, ctx.principal_id)
    if principal is None:
        raise AuthenticationError(
            "principal not found",
            domain="auth",
            action="access",
            reason="principal_missing",
        )
    settings = get_settings()
    secret, uri = await service.enrol_totp(
        session,
        principal=principal,
        issuer=settings.otel_service_name,
        trace_id=ctx.trace_id,
    )
    return TotpEnrolResponse(secret=secret, provisioning_uri=uri)


@router.post("/totp/verify", response_model=TotpVerifyResponse)
async def totp_verify_endpoint(
    payload: TotpVerifyRequest,
    auth: tuple[PrincipalContext, AsyncSession] = Depends(_require_principal),
) -> TotpVerifyResponse:
    ctx, session = auth
    principal = await session.get(IdPrincipal, ctx.principal_id)
    if principal is None:
        raise AuthenticationError(
            "principal not found",
            domain="auth",
            action="access",
            reason="principal_missing",
        )
    ok = await service.verify_totp_enrolment(
        session,
        principal=principal,
        code=payload.code,
        trace_id=ctx.trace_id,
    )
    return TotpVerifyResponse(verified=ok)


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------


class AssignRoleRequest(BaseModel):
    role_single_id: uuid.UUID | None = None
    role_composite_id: uuid.UUID | None = None
    scope_qualifiers: dict[str, Any] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class AssignRoleResponse(BaseModel):
    binding_id: uuid.UUID
    principal_id: uuid.UUID


@router.post(
    "/principals/{principal_id}/roles",
    response_model=AssignRoleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_role(
    principal_id: uuid.UUID,
    payload: AssignRoleRequest,
    request: Request,
) -> AssignRoleResponse:
    """Bind a single or composite role to a principal in the same tenant.

    Authority gate: `system.role:assign`. The admin gets this via
    SYSTEM_TENANT_ADMIN; tenants can grant ROLE_ASSIGN to other
    principals freely.
    """
    from openspine.identity.authz import enforce
    from openspine.identity.rbac_models import (
        IdPrincipalRole,
        IdRoleComposite,
        IdRoleSingle,
    )

    if (payload.role_single_id is None) == (payload.role_composite_id is None):
        raise ValidationError(
            "exactly one of role_single_id or role_composite_id is required",
            domain="auth.role",
            action="assign",
            reason="exactly_one_role_kind_required",
        )

    ctx: PrincipalContext = request.state.principal_context
    if ctx.is_anonymous:
        raise AuthenticationError(
            "authentication required",
            domain="auth",
            action="access",
            reason="not_authenticated",
        )
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="system.role", action="assign")

    target = await session.get(IdPrincipal, principal_id)
    if target is None or target.tenant_id != ctx.tenant_id:
        raise NotFoundError(
            "principal not in tenant",
            domain="auth.role",
            action="assign",
            reason="principal_not_in_tenant",
        )

    # Validate role exists in tenant.
    role_tenant_id: uuid.UUID | None = None
    if payload.role_single_id is not None:
        single = await session.get(IdRoleSingle, payload.role_single_id)
        role_tenant_id = single.tenant_id if single else None
    else:
        assert payload.role_composite_id is not None  # mutually-exclusive guard above
        composite = await session.get(IdRoleComposite, payload.role_composite_id)
        role_tenant_id = composite.tenant_id if composite else None
    if role_tenant_id is None or role_tenant_id != ctx.tenant_id:
        raise NotFoundError(
            "role not in tenant",
            domain="auth.role",
            action="assign",
            reason="role_not_in_tenant",
        )

    binding = IdPrincipalRole(
        tenant_id=ctx.tenant_id,
        principal_id=principal_id,
        role_single_id=payload.role_single_id,
        role_composite_id=payload.role_composite_id,
        scope_qualifiers=payload.scope_qualifiers,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    session.add(binding)
    await session.flush()

    from openspine.identity.audit import write_audit_event

    await write_audit_event(
        session,
        action="auth.role.assigned",
        outcome="success",
        trace_id=ctx.trace_id,
        tenant_id=ctx.tenant_id,
        principal_id=ctx.principal_id,
        target_kind="principal",
        target_id=principal_id,
        event_metadata={
            "role_single_id": str(payload.role_single_id) if payload.role_single_id else None,
            "role_composite_id": str(payload.role_composite_id)
            if payload.role_composite_id
            else None,
        },
    )
    return AssignRoleResponse(binding_id=binding.id, principal_id=principal_id)


@router.delete(
    "/principals/{principal_id}/roles/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_role(
    principal_id: uuid.UUID,
    binding_id: uuid.UUID,
    request: Request,
) -> Response:
    """Revoke a role binding. Authority gate: `system.role:revoke`."""
    from openspine.identity.authz import enforce
    from openspine.identity.rbac_models import IdPrincipalRole

    ctx: PrincipalContext = request.state.principal_context
    if ctx.is_anonymous:
        raise AuthenticationError(
            "authentication required",
            domain="auth",
            action="access",
            reason="not_authenticated",
        )
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="system.role", action="revoke")

    binding = await session.get(IdPrincipalRole, binding_id)
    if (
        binding is None
        or binding.tenant_id != ctx.tenant_id
        or binding.principal_id != principal_id
    ):
        raise NotFoundError(
            "role binding not found",
            domain="auth.role",
            action="revoke",
            reason="binding_not_found",
        )
    await session.delete(binding)
    await session.flush()

    from openspine.identity.audit import write_audit_event

    await write_audit_event(
        session,
        action="auth.role.revoked",
        outcome="success",
        trace_id=ctx.trace_id,
        tenant_id=ctx.tenant_id,
        principal_id=ctx.principal_id,
        target_kind="principal",
        target_id=principal_id,
        event_metadata={"binding_id": str(binding_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
