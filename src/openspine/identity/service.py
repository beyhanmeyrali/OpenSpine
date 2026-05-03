"""Identity service layer.

Pure business logic — no HTTP, no FastAPI. The router in
`openspine.identity.router` translates HTTP shapes to/from these
calls; the bootstrap CLI in `openspine.identity.cli` calls them
directly.

Keeping HTTP and business logic separate makes the operations
testable without spinning up an ASGI app, and gives the CLI a
first-class path that doesn't have to fake HTTP.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from openspine.identity.audit import write_audit_event
from openspine.identity.models import (
    PRINCIPAL_KINDS,
    TOKEN_KINDS,
    IdCredential,
    IdPrincipal,
    IdSession,
    IdTenant,
    IdToken,
)
from openspine.identity.security import (
    GeneratedToken,
    hash_password,
    issue_session_id,
    issue_token,
    new_totp_secret,
    password_needs_rehash,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)

# ---------------------------------------------------------------------------
# Tenant lookup
# ---------------------------------------------------------------------------


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> IdTenant:
    stmt = select(IdTenant).where(IdTenant.slug == slug)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        # Same generic shape as a wrong-password failure so callers
        # cannot enumerate tenant slugs.
        raise AuthenticationError(
            "authentication failed",
            domain="auth",
            action="login",
            reason="tenant_not_found_or_credentials_invalid",
        )
    if row.status != "active":
        raise AuthenticationError(
            "authentication failed",
            domain="auth",
            action="login",
            reason="tenant_not_active",
        )
    return row


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


class LoginResult:
    """Result of a successful login.

    `session_plaintext` is the cookie value to send back to the
    caller. `principal` is the loaded principal row. `requires_totp`
    is True when the principal has a TOTP credential and one wasn't
    provided in the request — the caller should retry with a TOTP
    code (and no session is issued in this case).
    """

    __slots__ = ("principal", "requires_totp", "session_plaintext", "session_row")

    def __init__(
        self,
        *,
        principal: IdPrincipal | None,
        session_row: IdSession | None,
        session_plaintext: str | None,
        requires_totp: bool,
    ) -> None:
        self.principal = principal
        self.session_row = session_row
        self.session_plaintext = session_plaintext
        self.requires_totp = requires_totp


async def login_password(
    session: AsyncSession,
    *,
    tenant_slug: str,
    username: str,
    password: str,
    totp_code: str | None,
    user_agent: str | None,
    ip_address: str | None,
    idle_minutes: int,
    absolute_hours: int,
    trace_id: uuid.UUID,
) -> LoginResult:
    """Authenticate a human principal with password (+ TOTP if enrolled).

    Writes an `auth.login.success` or `auth.login.failure` audit event.
    Returns a `LoginResult` with a fresh session id on success.
    """
    tenant = await get_tenant_by_slug(session, tenant_slug)
    # RLS would now apply if we set the GUC; do so so the principal
    # lookup respects tenant isolation. (The login endpoint runs
    # outside the middleware's authenticated branch, so the GUC isn't
    # set automatically.)
    await _set_tenant_guc(session, tenant.id)

    principal_row = await _load_principal_by_username(
        session, tenant_id=tenant.id, username=username
    )
    if principal_row is None or principal_row.status != "active":
        await write_audit_event(
            session,
            action="auth.login.failure",
            outcome="failure",
            trace_id=trace_id,
            tenant_id=tenant.id,
            reason="unknown_or_inactive_principal",
            event_metadata={"user_agent": user_agent, "ip_address": ip_address},
        )
        raise AuthenticationError(
            "authentication failed",
            domain="auth",
            action="login",
            reason="invalid_credentials",
        )

    password_cred = await _load_credential(session, principal_id=principal_row.id, kind="password")
    if password_cred is None or password_cred.secret_hash is None:
        await write_audit_event(
            session,
            action="auth.login.failure",
            outcome="failure",
            trace_id=trace_id,
            tenant_id=tenant.id,
            principal_id=principal_row.id,
            reason="no_password_credential",
        )
        raise AuthenticationError(
            "authentication failed",
            domain="auth",
            action="login",
            reason="invalid_credentials",
        )

    if not verify_password(password, password_cred.secret_hash):
        await write_audit_event(
            session,
            action="auth.login.failure",
            outcome="failure",
            trace_id=trace_id,
            tenant_id=tenant.id,
            principal_id=principal_row.id,
            reason="wrong_password",
        )
        raise AuthenticationError(
            "authentication failed",
            domain="auth",
            action="login",
            reason="invalid_credentials",
        )

    # Password ok. Re-hash if parameters have moved.
    if password_needs_rehash(password_cred.secret_hash):
        password_cred.secret_hash = hash_password(password)

    # TOTP step if enrolled.
    totp_cred = await _load_credential(session, principal_id=principal_row.id, kind="totp_secret")
    if totp_cred is not None and totp_cred.status == "active":
        if not totp_code:
            await write_audit_event(
                session,
                action="auth.login.failure",
                outcome="failure",
                trace_id=trace_id,
                tenant_id=tenant.id,
                principal_id=principal_row.id,
                reason="totp_required",
            )
            return LoginResult(
                principal=principal_row,
                session_row=None,
                session_plaintext=None,
                requires_totp=True,
            )
        if totp_cred.secret_hash is None or not verify_totp(totp_cred.secret_hash, totp_code):
            await write_audit_event(
                session,
                action="auth.login.failure",
                outcome="failure",
                trace_id=trace_id,
                tenant_id=tenant.id,
                principal_id=principal_row.id,
                reason="wrong_totp",
            )
            raise AuthenticationError(
                "authentication failed",
                domain="auth",
                action="login",
                reason="invalid_credentials",
            )

    # Issue session.
    plaintext, hashed = issue_session_id()
    now = datetime.now(UTC)
    sess_row = IdSession(
        tenant_id=tenant.id,
        principal_id=principal_row.id,
        session_hash=hashed,
        status="active",
        issued_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=idle_minutes),
        absolute_expires_at=now + timedelta(hours=absolute_hours),
        user_agent=user_agent,
        ip_address=ip_address,
        created_by=principal_row.id,
        updated_by=principal_row.id,
    )
    session.add(sess_row)
    await session.flush()

    await write_audit_event(
        session,
        action="auth.login.success",
        outcome="success",
        trace_id=trace_id,
        tenant_id=tenant.id,
        principal_id=principal_row.id,
        target_kind="session",
        target_id=sess_row.id,
        event_metadata={"user_agent": user_agent, "ip_address": ip_address},
    )
    return LoginResult(
        principal=principal_row,
        session_row=sess_row,
        session_plaintext=plaintext,
        requires_totp=False,
    )


async def revoke_session(
    session: AsyncSession,
    *,
    session_row: IdSession,
    trace_id: uuid.UUID,
) -> None:
    now = datetime.now(UTC)
    session_row.status = "revoked"
    session_row.revoked_at = now
    session_row.updated_by = session_row.principal_id
    await write_audit_event(
        session,
        action="auth.session.revoked",
        outcome="success",
        trace_id=trace_id,
        tenant_id=session_row.tenant_id,
        principal_id=session_row.principal_id,
        target_kind="session",
        target_id=session_row.id,
    )


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class IssuedTokenResult:
    __slots__ = ("plaintext", "row")

    def __init__(self, *, row: IdToken, plaintext: str) -> None:
        self.row = row
        self.plaintext = plaintext


async def issue_principal_token(
    session: AsyncSession,
    *,
    issuer_principal: IdPrincipal,
    target_principal_id: uuid.UUID,
    kind: str,
    scope: dict[str, Any],
    reason: str | None,
    expires_at: datetime | None,
    trace_id: uuid.UUID,
) -> IssuedTokenResult:
    """Issue a token. Enforces the agent-token CHECK invariants.

    Self-issuance: a human can issue `user_api` or `agent` tokens for
    themselves or for a principal in their tenant they have authority
    over (auth-object enforcement lands §4.3; v0.1 trusts the caller).

    Cross-kind rules:
    - `user_api` tokens — target must be the issuer (humans manage
      their own API tokens). `expires_at` optional.
    - `agent` tokens — `expires_at`, `reason`, and `provisioner`
      (= issuer) all required by the DB CHECK.
    - `service` tokens — for technical accounts; provisioner recorded
      but not required by the CHECK.
    """
    if kind not in TOKEN_KINDS:
        raise ValidationError(
            f"unknown token kind {kind!r}",
            domain="auth.token",
            action="issue",
            reason="invalid_kind",
            allowed={"kinds": list(TOKEN_KINDS)},
        )
    if kind == "agent":
        if expires_at is None:
            raise ValidationError(
                "agent tokens require expires_at",
                domain="auth.token",
                action="issue",
                reason="agent_expiry_required",
            )
        if not reason:
            raise ValidationError(
                "agent tokens require a reason",
                domain="auth.token",
                action="issue",
                reason="agent_reason_required",
            )
    if kind == "user_api" and target_principal_id != issuer_principal.id:
        raise ValidationError(
            "user_api tokens may only be issued for the requester",
            domain="auth.token",
            action="issue",
            reason="cross_principal_user_token",
        )

    target = await session.get(IdPrincipal, target_principal_id)
    if target is None or target.tenant_id != issuer_principal.tenant_id:
        raise NotFoundError(
            "target principal not found in this tenant",
            domain="auth.token",
            action="issue",
            reason="target_principal_not_in_tenant",
        )

    issued: GeneratedToken = issue_token(kind)
    row = IdToken(
        tenant_id=issuer_principal.tenant_id,
        principal_id=target_principal_id,
        kind=kind,
        prefix=issued.prefix,
        secret_hash=issued.secret_hash,
        scope=scope or {},
        provisioner_principal_id=issuer_principal.id,
        reason=reason,
        expires_at=expires_at,
        created_by=issuer_principal.id,
        updated_by=issuer_principal.id,
    )
    session.add(row)
    await session.flush()

    await write_audit_event(
        session,
        action="auth.token.issued",
        outcome="success",
        trace_id=trace_id,
        tenant_id=issuer_principal.tenant_id,
        principal_id=issuer_principal.id,
        target_kind="token",
        target_id=row.id,
        event_metadata={
            "kind": kind,
            "target_principal_id": str(target_principal_id),
            "reason": reason,
        },
    )
    return IssuedTokenResult(row=row, plaintext=issued.plaintext)


async def revoke_token(
    session: AsyncSession,
    *,
    revoker: IdPrincipal,
    token_id: uuid.UUID,
    revocation_reason: str | None,
    trace_id: uuid.UUID,
) -> IdToken:
    row = await session.get(IdToken, token_id)
    if row is None or row.tenant_id != revoker.tenant_id:
        raise NotFoundError(
            "token not found",
            domain="auth.token",
            action="revoke",
            reason="token_not_in_tenant",
        )
    if row.revoked_at is not None:
        raise ConflictError(
            "token already revoked",
            domain="auth.token",
            action="revoke",
            reason="already_revoked",
        )
    now = datetime.now(UTC)
    row.revoked_at = now
    row.revoked_by_principal_id = revoker.id
    row.revocation_reason = revocation_reason
    row.updated_by = revoker.id
    await write_audit_event(
        session,
        action="auth.token.revoked",
        outcome="success",
        trace_id=trace_id,
        tenant_id=revoker.tenant_id,
        principal_id=revoker.id,
        target_kind="token",
        target_id=row.id,
        reason=revocation_reason,
    )
    return row


# ---------------------------------------------------------------------------
# TOTP enrolment
# ---------------------------------------------------------------------------


async def enrol_totp(
    session: AsyncSession,
    *,
    principal: IdPrincipal,
    issuer: str,
    trace_id: uuid.UUID,
) -> tuple[str, str]:
    """Generate a TOTP secret for `principal` and return (secret, uri).

    The credential row is created in `pending` status — actually
    `expired` since `pending` isn't a valid value, we store it with
    `status='active'` immediately because the next call to
    `verify_totp_enrolment` either confirms or aborts.

    Reasoning: keeping the secret in the credential row from the
    moment of generation means a verify call can find it without
    threading state through the client. If verification never
    happens, an admin can revoke the credential.
    """
    existing = await _load_credential(session, principal_id=principal.id, kind="totp_secret")
    if existing is not None and existing.status == "active":
        raise ConflictError(
            "principal already has a TOTP credential",
            domain="auth.totp",
            action="enrol",
            reason="already_enrolled",
        )

    secret = new_totp_secret()
    cred = IdCredential(
        tenant_id=principal.tenant_id,
        principal_id=principal.id,
        kind="totp_secret",
        secret_hash=secret,
        status="active",
        created_by=principal.id,
        updated_by=principal.id,
    )
    session.add(cred)
    await session.flush()
    uri = totp_provisioning_uri(secret, account_name=principal.username, issuer=issuer)
    await write_audit_event(
        session,
        action="auth.totp.enrolled",
        outcome="success",
        trace_id=trace_id,
        tenant_id=principal.tenant_id,
        principal_id=principal.id,
        target_kind="credential",
        target_id=cred.id,
    )
    return secret, uri


async def verify_totp_enrolment(
    session: AsyncSession,
    *,
    principal: IdPrincipal,
    code: str,
    trace_id: uuid.UUID,
) -> bool:
    cred = await _load_credential(session, principal_id=principal.id, kind="totp_secret")
    if cred is None or cred.secret_hash is None or cred.status != "active":
        raise NotFoundError(
            "no pending TOTP enrolment",
            domain="auth.totp",
            action="verify",
            reason="not_enrolled",
        )
    ok = verify_totp(cred.secret_hash, code)
    await write_audit_event(
        session,
        action="auth.totp.verified" if ok else "auth.totp.verify_failed",
        outcome="success" if ok else "failure",
        trace_id=trace_id,
        tenant_id=principal.tenant_id,
        principal_id=principal.id,
        target_kind="credential",
        target_id=cred.id,
    )
    return ok


# ---------------------------------------------------------------------------
# Bootstrap (used by the management CLI)
# ---------------------------------------------------------------------------


async def bootstrap_tenant_and_admin(
    session: AsyncSession,
    *,
    tenant_name: str,
    tenant_slug: str,
    admin_username: str,
    admin_display_name: str,
    admin_email: str,
    admin_password: str,
) -> tuple[IdTenant, IdPrincipal]:
    """Atomic create of (tenant, admin principal, password credential).

    Relies on the DEFERRABLE INITIALLY DEFERRED FK constraints from
    the audit-mixin. Within one transaction:

    1. INSERT id_tenant — `created_by` references a principal that
       doesn't exist yet. Constraint deferred.
    2. INSERT id_principal — `tenant_id` references the tenant just
       inserted; `created_by` references *self*. Constraints
       deferred.
    3. UPDATE id_tenant — set `created_by` and `updated_by` to the
       new principal id.
    4. INSERT id_credential — password hash for the admin.

    All deferred constraints validate at COMMIT.
    """
    if await get_tenant_by_slug_or_none(session, tenant_slug) is not None:
        raise ConflictError(
            f"tenant slug {tenant_slug!r} already exists",
            domain="system.tenant",
            action="create",
            reason="slug_collision",
        )

    placeholder = uuid.uuid4()
    tenant = IdTenant(
        name=tenant_name,
        slug=tenant_slug,
        status="active",
        deployment_metadata={},
        # Reference a placeholder UUID; we patch after the principal
        # exists. Constraint is DEFERRABLE INITIALLY DEFERRED.
        created_by=placeholder,
        updated_by=placeholder,
    )
    session.add(tenant)
    await session.flush()

    admin = IdPrincipal(
        tenant_id=tenant.id,
        kind="human",
        username=admin_username,
        display_name=admin_display_name,
        status="active",
        created_by=placeholder,
        updated_by=placeholder,
    )
    session.add(admin)
    await session.flush()

    # Patch self-references now that admin.id is known.
    admin.created_by = admin.id
    admin.updated_by = admin.id
    tenant.created_by = admin.id
    tenant.updated_by = admin.id

    # Password credential.
    pw = IdCredential(
        tenant_id=tenant.id,
        principal_id=admin.id,
        kind="password",
        secret_hash=hash_password(admin_password),
        status="active",
        created_by=admin.id,
        updated_by=admin.id,
    )
    session.add(pw)

    # Audit the creation. `created_by` on the audit row is the admin
    # themselves — there is no other principal to attribute it to.
    bootstrap_trace = uuid.uuid4()
    await write_audit_event(
        session,
        action="system.tenant.created",
        outcome="success",
        trace_id=bootstrap_trace,
        tenant_id=tenant.id,
        principal_id=admin.id,
        target_kind="tenant",
        target_id=tenant.id,
        created_by=admin.id,
    )
    await write_audit_event(
        session,
        action="system.principal.created",
        outcome="success",
        trace_id=bootstrap_trace,
        tenant_id=tenant.id,
        principal_id=admin.id,
        target_kind="principal",
        target_id=admin.id,
        created_by=admin.id,
    )

    # Seed the system catalogue (auth objects, roles, SoD baseline) and
    # grant the SYSTEM_TENANT_ADMIN composite role to the admin so they
    # can actually do things on day one.
    from sqlalchemy import select

    from openspine.identity.rbac_models import IdPrincipalRole, IdRoleComposite
    from openspine.identity.seed import seed_system_catalogue

    await seed_system_catalogue(session, tenant_id=tenant.id, actor_principal_id=admin.id)
    # MD global catalogues (currencies, rate types, UoMs) are
    # tenant-independent — seeded once per installation. Running
    # again is a no-op.
    from openspine.md.global_seed import seed_md_globals

    await seed_md_globals(session, actor_principal_id=admin.id)
    # FI configuration: leading ledger + GL/reversal document types
    # + the default fi_document number range. Idempotent.
    from openspine.fi.seed import seed_fi_configuration

    await seed_fi_configuration(session, tenant_id=tenant.id, actor_principal_id=admin.id)
    # The bootstrap admin needs full system admin, full MD admin, and
    # FI GL accountant so the day-one operator can do every operation
    # the happy-path acceptance tests exercise (tenant config, role
    # assignment, CC/CoA/BP/material/FX/posting-period creation, AND
    # GL document posting).
    for composite_key in ("SYSTEM_TENANT_ADMIN", "MD_ADMIN", "FI_GL_ACCOUNTANT"):
        composite = (
            await session.execute(
                select(IdRoleComposite).where(
                    IdRoleComposite.tenant_id == tenant.id,
                    IdRoleComposite.system_key == composite_key,
                )
            )
        ).scalar_one()
        session.add(
            IdPrincipalRole(
                tenant_id=tenant.id,
                principal_id=admin.id,
                role_composite_id=composite.id,
                created_by=admin.id,
                updated_by=admin.id,
            )
        )
    await session.flush()
    return tenant, admin


async def get_tenant_by_slug_or_none(session: AsyncSession, slug: str) -> IdTenant | None:
    stmt = select(IdTenant).where(IdTenant.slug == slug)
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_principal_by_username(
    session: AsyncSession, *, tenant_id: uuid.UUID, username: str
) -> IdPrincipal | None:
    stmt = select(IdPrincipal).where(
        IdPrincipal.tenant_id == tenant_id,
        IdPrincipal.username == username,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _load_credential(
    session: AsyncSession, *, principal_id: uuid.UUID, kind: str
) -> IdCredential | None:
    stmt = (
        select(IdCredential)
        .where(IdCredential.principal_id == principal_id, IdCredential.kind == kind)
        .order_by(IdCredential.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _set_tenant_guc(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import text

    # SET LOCAL doesn't accept bind parameters; use set_config() which does.
    await session.execute(
        text("SELECT set_config('openspine.tenant_id', :tenant_id, true)").bindparams(
            tenant_id=str(tenant_id)
        )
    )


__all__ = [
    "PRINCIPAL_KINDS",
    "IssuedTokenResult",
    "LoginResult",
    "bootstrap_tenant_and_admin",
    "enrol_totp",
    "get_tenant_by_slug",
    "get_tenant_by_slug_or_none",
    "issue_principal_token",
    "login_password",
    "revoke_session",
    "revoke_token",
    "verify_totp_enrolment",
]
