"""Identity ORM models (`id_*` tables).

Implements the v0.1 §4.2 schema described in:

- `docs/identity/tenancy.md` — tenant + tenant settings
- `docs/identity/users.md` — principal + human/agent profiles
- `docs/identity/authentication.md` — credential, session, token, federated identity
- `docs/identity/README.md` — audit topology

The strategic shape of these tables (column types, FK direction,
nullability, CHECK constraints, RLS) is deliberate and was the focus of
the §4.2 council pass. Notable choices:

1. **`id_tenant` is the global registry** — no `tenant_id` column, no
   RLS policy. Listing tenants is itself a permission-checked action,
   gated at the service layer (`system.tenant:read_all` in §4.3).
2. **All other `id_*` tables are tenant-scoped** with RLS on `tenant_id`.
3. **`id_token` is single-table by design** — `kind` ∈
   {`user_api`, `agent`, `service`} discriminates. Agent-token invariants
   (must have `expires_at`, `provisioner_principal_id`, `reason`) are
   enforced by a CHECK constraint added in the migration so the database
   itself rejects malformed agent tokens, not just the service layer.
4. **`id_audit_event` is append-only** — no `updated_*`, no `version`,
   no UPDATE trigger. Reversal of an action is a new event, never a
   mutation of the original.
5. **The bootstrap-cycle FKs** (`id_tenant.created_by` →
   `id_principal.id`, `id_principal.tenant_id` → `id_tenant.id`,
   self-FK on `id_principal.created_by`) are `DEFERRABLE INITIALLY
   DEFERRED` via the audit/tenant mixins. The bootstrap CLI inserts
   tenant + admin principal + initial credential in one transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openspine.core.database import AuditMixin, Base, BusinessTableMixin

# ---------------------------------------------------------------------------
# Status / kind vocabularies
#
# These live as CHECK-constrained TEXT columns rather than lookup tables
# because the values are core-owned, fixed, and not plugin-extensible.
# (Currency / UoM / role catalogues are lookup tables — those *are*
# plugin-extensible.) Keeping them as constants here means the migration
# and the application share one source of truth.
# ---------------------------------------------------------------------------

TENANT_STATUSES = ("active", "suspended", "archived")
PRINCIPAL_KINDS = ("human", "agent", "technical")
PRINCIPAL_STATUSES = ("active", "suspended", "deleted")
CREDENTIAL_KINDS = ("password", "totp_secret", "sso_federation", "token_indirection")
CREDENTIAL_STATUSES = ("active", "expired", "revoked")
TOKEN_KINDS = ("user_api", "agent", "service")
SESSION_STATUSES = ("active", "expired", "revoked")
AUDIT_OUTCOMES = ("success", "failure")


def _enum_check(column: str, allowed: tuple[str, ...]) -> str:
    """Render a SQL CHECK clause `column IN (...)` from a value tuple."""
    inside = ", ".join(f"'{v}'" for v in allowed)
    return f"{column} IN ({inside})"


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


class IdTenant(AuditMixin, Base):
    """The global tenant registry.

    Not RLS-protected — listing tenants requires `system.tenant:read_all`
    at the service layer. Self-hosted deployments typically have one row
    here; SaaS hosters have many.
    """

    __tablename__ = "id_tenant"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_id_tenant_slug"),
        CheckConstraint(_enum_check("status", TENANT_STATUSES), name="ck_id_tenant_status"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    deployment_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )


class IdTenantSetting(BusinessTableMixin, Base):
    """Per-tenant configuration as `(key, value)` rows.

    Rows are RLS-isolated. Plugins read tenant settings via the service
    layer; they never touch this table directly.
    """

    __tablename__ = "id_tenant_setting"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_id_tenant_setting_key"),)

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


# ---------------------------------------------------------------------------
# Principals
# ---------------------------------------------------------------------------


class IdPrincipal(BusinessTableMixin, Base):
    """The unified principal table — humans, agents, technical accounts.

    `kind` discriminates. The 1-1 profile tables (`id_human_profile`,
    `id_agent_profile`) carry the kind-specific fields. Technical accounts
    have no profile table — the principal row plus their tokens is the
    full record.
    """

    __tablename__ = "id_principal"
    __table_args__ = (
        CheckConstraint(_enum_check("kind", PRINCIPAL_KINDS), name="ck_id_principal_kind"),
        CheckConstraint(_enum_check("status", PRINCIPAL_STATUSES), name="ck_id_principal_status"),
        UniqueConstraint("tenant_id", "username", name="uq_id_principal_username"),
        Index("ix_id_principal_tenant_kind", "tenant_id", "kind"),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))


class IdHumanProfile(BusinessTableMixin, Base):
    """Human-specific principal fields.

    1-1 with `id_principal` where `kind = 'human'`. Email is stored
    verbatim per `data-model.md` (no lower-casing at insert).
    """

    __tablename__ = "id_human_profile"
    __table_args__ = (
        UniqueConstraint("principal_id", name="uq_id_human_profile_principal_id"),
        UniqueConstraint("tenant_id", "email", name="uq_id_human_profile_email"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="RESTRICT"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en'"))
    time_zone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UTC'"))
    # employee_bp_id deferred — md_business_partner lands in §4.4. The link
    # back to a Business Partner is added by an alter-table migration in §4.4
    # rather than created NULL here, to keep the FK direction honest.
    manager_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="SET NULL"),
        nullable=True,
    )


class IdAgentProfile(BusinessTableMixin, Base):
    """Agent-specific principal fields.

    Captures the *provenance chain* (which human provisioned this agent)
    and the *purpose* (free-form rationale). Token-level scope lives on
    `id_token`; this row is identity-level metadata.
    """

    __tablename__ = "id_agent_profile"
    __table_args__ = (UniqueConstraint("principal_id", name="uq_id_agent_profile_principal_id"),)

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    provisioner_principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="RESTRICT"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    constraint_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class IdCredential(BusinessTableMixin, Base):
    """One row per credential held by a principal.

    `kind = 'password'` rows store an argon2id-encoded hash in
    `secret_hash`. `kind = 'totp_secret'` rows store the base32-encoded
    TOTP shared secret. `kind = 'sso_federation'` rows store no secret
    (the federation table holds the issuer/subject pair). `kind =
    'token_indirection'` is an internal pointer used when a credential
    holder wants tokens issued under their identity but the secret lives
    elsewhere — reserved for §4.7 and beyond.
    """

    __tablename__ = "id_credential"
    __table_args__ = (
        CheckConstraint(_enum_check("kind", CREDENTIAL_KINDS), name="ck_id_credential_kind"),
        CheckConstraint(_enum_check("status", CREDENTIAL_STATUSES), name="ck_id_credential_status"),
        Index("ix_id_credential_principal_kind", "principal_id", "kind"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdSession(BusinessTableMixin, Base):
    """Server-side session row for human principals.

    The opaque session id (sent in the HttpOnly cookie) is stored as a
    SHA-256 hash so a stolen DB cannot be turned into a stolen session.
    Bot principals (agent/technical) are stateless — they don't write
    session rows; every request is authenticated via token.
    """

    __tablename__ = "id_session"
    __table_args__ = (
        UniqueConstraint("session_hash", name="uq_id_session_session_hash"),
        CheckConstraint(_enum_check("status", SESSION_STATUSES), name="ck_id_session_status"),
        Index("ix_id_session_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdToken(BusinessTableMixin, Base):
    """API and agent tokens.

    See `docs/identity/authentication.md`. Tokens are 256-bit random
    secrets stored as SHA-256 hashes. The `prefix` column is the visible
    portion shown to humans for token identification (the secret is shown
    once at creation and never again).

    The CHECK constraint enforces agent-specific invariants at the
    database level — agent tokens must have an expiry, a provisioner,
    and a stated reason. This makes "you cannot accidentally issue a
    forever, anonymous, undocumented agent token" a database-level
    invariant, not a service-layer one.
    """

    __tablename__ = "id_token"
    __table_args__ = (
        UniqueConstraint("secret_hash", name="uq_id_token_secret_hash"),
        CheckConstraint(_enum_check("kind", TOKEN_KINDS), name="ck_id_token_kind"),
        CheckConstraint(
            "kind <> 'agent' OR ("
            "expires_at IS NOT NULL"
            " AND provisioner_principal_id IS NOT NULL"
            " AND reason IS NOT NULL)",
            name="ck_id_token_agent_invariants",
        ),
        Index("ix_id_token_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    provisioner_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="RESTRICT"),
        nullable=True,
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdFederatedIdentity(BusinessTableMixin, Base):
    """Stub for SSO/OIDC federation. Wired by §v0.2 SSO work.

    Lands in v0.1 §4.2 because the table belongs to the identity surface
    and adding it later means an alter-table on a populated database. The
    issuer/subject pair uniquely identifies an external identity; one
    principal can hold multiple federated identities.
    """

    __tablename__ = "id_federated_identity"
    __table_args__ = (
        UniqueConstraint("tenant_id", "issuer", "subject", name="uq_id_federated_identity_iss_sub"),
        Index("ix_id_federated_identity_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="CASCADE"),
        nullable=False,
    )
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Audit
#
# `id_audit_event` is append-only. It does NOT use BusinessTableMixin
# because:
# - There is no `updated_at`/`updated_by`/`version` (no updates).
# - `tenant_id`, `principal_id`, and `created_by` are all nullable: a
#   failed login against an unknown tenant slug, or an unauthenticated
#   request, still gets an audit row.
# Hand-rolled column declarations make the append-only nature explicit
# at read time.
# ---------------------------------------------------------------------------


class IdAuditEvent(Base):
    """Append-only auth and business-action audit log.

    Per `docs/identity/README.md` §"Audit topology", this is the "what
    happened" stream. Authorisation decisions go to `id_auth_decision_log`
    (lands §4.3). Agent reasoning goes to `id_agent_decision_trace`
    (lands §4.7).

    Nullable `tenant_id` accommodates events that occur before tenant
    context is known (failed login against unknown slug, system-wide
    bootstrap events). RLS still hides those from per-tenant queries —
    `WHERE tenant_id = :t` excludes NULL by SQL semantics.
    """

    __tablename__ = "id_audit_event"
    __table_args__ = (
        CheckConstraint(_enum_check("outcome", AUDIT_OUTCOMES), name="ck_id_audit_event_outcome"),
        Index("ix_id_audit_event_tenant_action", "tenant_id", "action"),
        Index("ix_id_audit_event_trace", "trace_id"),
        Index("ix_id_audit_event_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )


# ---------------------------------------------------------------------------
# Tables that should have an updated-audit trigger attached in the
# migration. Listed here so the migration code and tests can iterate
# without duplicating the list.
# ---------------------------------------------------------------------------


TABLES_WITH_UPDATE_TRIGGER: tuple[str, ...] = (
    IdTenant.__tablename__,
    IdTenantSetting.__tablename__,
    IdPrincipal.__tablename__,
    IdHumanProfile.__tablename__,
    IdAgentProfile.__tablename__,
    IdCredential.__tablename__,
    IdSession.__tablename__,
    IdToken.__tablename__,
    IdFederatedIdentity.__tablename__,
)


# Tables that get an RLS policy. `id_tenant` is intentionally omitted —
# it is the global registry, gated at the service layer.
TABLES_WITH_RLS: tuple[str, ...] = (
    IdTenantSetting.__tablename__,
    IdPrincipal.__tablename__,
    IdHumanProfile.__tablename__,
    IdAgentProfile.__tablename__,
    IdCredential.__tablename__,
    IdSession.__tablename__,
    IdToken.__tablename__,
    IdFederatedIdentity.__tablename__,
    IdAuditEvent.__tablename__,
)


__all__ = [
    "AUDIT_OUTCOMES",
    "CREDENTIAL_KINDS",
    "CREDENTIAL_STATUSES",
    "PRINCIPAL_KINDS",
    "PRINCIPAL_STATUSES",
    "SESSION_STATUSES",
    "TABLES_WITH_RLS",
    "TABLES_WITH_UPDATE_TRIGGER",
    "TENANT_STATUSES",
    "TOKEN_KINDS",
    "IdAgentProfile",
    "IdAuditEvent",
    "IdCredential",
    "IdFederatedIdentity",
    "IdHumanProfile",
    "IdPrincipal",
    "IdSession",
    "IdTenant",
    "IdTenantSetting",
    "IdToken",
]
