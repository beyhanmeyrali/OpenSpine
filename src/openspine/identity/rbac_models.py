"""RBAC + auth-object ORM models (`id_auth_*`, `id_role_*`, `id_permission`,
`id_sod_*`, `id_auth_decision_log`).

Implements the v0.1 §4.3 schema described in:

- `docs/identity/permissions.md` — auth-object model + qualifier semantics
- `docs/identity/roles.md` — two-tier role model + SoD baseline
- `docs/identity/README.md` §"Audit topology" — id_auth_decision_log

Design notes (the §4.3 council pass synthesised these):

1. **System catalogue is per-tenant copy, not global.** Each tenant gets
   its own `fi.invoice`, `system.user`, etc. rows. Uniformity of RLS
   trumps the very small storage hit of duplication. Rows ship with
   `is_system = TRUE` and a stable `system_key` for idempotent upsert.
   Tenants can copy + rename to make their own; system rows themselves
   are immutable by service-layer policy (no RLS-level immutability — a
   superuser can override, intentionally).

2. **`id_permission` is the role → (auth_object, action, qualifier
   values)** linker. `qualifier_values` is JSONB so the qualifier
   schema can extend without DDL — e.g.,
   `{"company_code": ["DE01","DE02"], "amount_range": {"max": 10000, "currency": "EUR"}}`.

3. **`id_principal_role` references either a single or composite role**
   via two nullable FKs with a CHECK enforcing exactly one. Simpler than
   a polymorphic `(role_kind, role_id)` discriminator.

4. **`id_sod_rule` + `id_sod_rule_clause`.** A rule lists clauses; a
   violation requires all clauses to be granted to the same principal
   (with overlapping scope). Severity is `block` or `warn`; tenants
   choose per rule.

5. **`id_auth_decision_log` is append-only**, like `id_audit_event`.
   Decision log writes are async-batched at the service layer (the
   evaluator queues; a background flusher persists). Schema is the
   same shape regardless.
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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openspine.core.database import Base, BusinessTableMixin
from openspine.identity.models import _enum_check

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

QUALIFIER_DATA_TYPES = (
    "string_list",  # value is a list of allowed strings
    "numeric_range",  # value has min/max numeric bounds
    "amount_range",  # value has max + currency (FX-converted at check time)
    "wildcard",  # value of "*" means everything; otherwise a list of allowed strings
)
SOD_SEVERITIES = ("block", "warn")
DECISION_OUTCOMES = ("allow", "deny", "sod_block")
ROLE_KINDS = ("single", "composite")


# ---------------------------------------------------------------------------
# Auth-object catalogue
# ---------------------------------------------------------------------------


class IdAuthObject(BusinessTableMixin, Base):
    """An authorisation object: `(domain, action, qualifiers...)` shape.

    The `domain` is the dotted name (e.g., `fi.invoice`,
    `acme.batch_certificate`). Plugin-registered objects use the plugin
    id as their domain prefix so collisions are impossible by
    construction. `is_system = TRUE` rows are seeded by the catalogue
    loader and treated as immutable by the service layer.
    """

    __tablename__ = "id_auth_object"
    __table_args__ = (UniqueConstraint("tenant_id", "domain", name="uq_id_auth_object_domain"),)

    domain: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    system_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdAuthObjectAction(BusinessTableMixin, Base):
    """A specific verb against an auth-object (post, release, display, ...)."""

    __tablename__ = "id_auth_object_action"
    __table_args__ = (
        UniqueConstraint("auth_object_id", "action_code", name="uq_id_auth_object_action_code"),
    )

    auth_object_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_auth_object.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdAuthObjectQualifier(BusinessTableMixin, Base):
    """A qualifier dimension for an auth-object (company_code, amount_range, ...)."""

    __tablename__ = "id_auth_object_qualifier"
    __table_args__ = (
        UniqueConstraint(
            "auth_object_id", "qualifier_code", name="uq_id_auth_object_qualifier_code"
        ),
        CheckConstraint(
            _enum_check("data_type", QUALIFIER_DATA_TYPES),
            name="ck_id_auth_object_qualifier_data_type",
        ),
    )

    auth_object_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_auth_object.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    qualifier_code: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class IdRoleSingle(BusinessTableMixin, Base):
    """Single role — a cohesive bundle of authorisations for one job activity.

    `code` is the catalogue identifier (e.g., `FI_AP_INVOICE_POST`). System
    rows ship from the seeder with `is_system = TRUE` and a `system_key`
    matching `code` so idempotent upserts find them.
    """

    __tablename__ = "id_role_single"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_id_role_single_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    system_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdRoleComposite(BusinessTableMixin, Base):
    """Composite role — bundles several single roles into a job function."""

    __tablename__ = "id_role_composite"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_id_role_composite_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    system_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdRoleCompositeMember(BusinessTableMixin, Base):
    """Composite → single role membership."""

    __tablename__ = "id_role_composite_member"
    __table_args__ = (
        UniqueConstraint("composite_id", "single_id", name="uq_id_role_composite_member"),
    )

    composite_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_role_composite.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    single_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_role_single.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class IdPermission(BusinessTableMixin, Base):
    """A single role's grant: `(role) → (auth_object, action, qualifier_values)`.

    `qualifier_values` JSONB schema:

      {"company_code": ["DE01", "DE02"],
       "amount_range": {"max": "10000.00", "currency": "EUR"},
       ...}

    Missing qualifiers default to "no constraint" (allow any value). The
    evaluator interprets the shapes per `data_type` declared on the
    matching `id_auth_object_qualifier` row.
    """

    __tablename__ = "id_permission"
    __table_args__ = (
        UniqueConstraint(
            "role_single_id",
            "auth_object_id",
            "action_code",
            name="uq_id_permission_unique",
        ),
    )

    role_single_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_role_single.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    auth_object_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_auth_object.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_code: Mapped[str] = mapped_column(Text, nullable=False)
    qualifier_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


class IdPrincipalRole(BusinessTableMixin, Base):
    """A principal's role binding.

    Exactly one of `role_single_id` / `role_composite_id` is non-NULL.
    `scope_qualifiers` overlays the role's permission qualifiers — the
    binding's scope intersects with each granted permission's. `valid_*`
    bounds the assignment in time.
    """

    __tablename__ = "id_principal_role"
    __table_args__ = (
        CheckConstraint(
            "(role_single_id IS NOT NULL) <> (role_composite_id IS NOT NULL)",
            name="ck_id_principal_role_exactly_one",
        ),
        Index("ix_id_principal_role_principal", "principal_id", "tenant_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_single_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_role_single.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role_composite_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_role_composite.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope_qualifiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# SoD
# ---------------------------------------------------------------------------


class IdSodRule(BusinessTableMixin, Base):
    """A SoD rule. Severity `block` denies; `warn` audits and allows."""

    __tablename__ = "id_sod_rule"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_id_sod_rule_code"),
        CheckConstraint(_enum_check("severity", SOD_SEVERITIES), name="ck_id_sod_rule_severity"),
    )

    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'block'"))
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    system_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdSodRuleClause(BusinessTableMixin, Base):
    """A clause of a SoD rule — an `(auth_object, action)` that participates."""

    __tablename__ = "id_sod_rule_clause"
    __table_args__ = (
        UniqueConstraint(
            "sod_rule_id",
            "auth_object_id",
            "action_code",
            name="uq_id_sod_rule_clause",
        ),
    )

    sod_rule_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_sod_rule.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    auth_object_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_auth_object.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_code: Mapped[str] = mapped_column(Text, nullable=False)


class IdSodOverride(BusinessTableMixin, Base):
    """Audited override of a SoD `warn` rule (or temporary `block` waiver)."""

    __tablename__ = "id_sod_override"
    __table_args__ = (
        Index(
            "ix_id_sod_override_principal_rule",
            "principal_id",
            "sod_rule_id",
        ),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="CASCADE"),
        nullable=False,
    )
    sod_rule_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_sod_rule.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approver_principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Decision log (append-only)
# ---------------------------------------------------------------------------


class IdAuthDecisionLog(Base):
    """Every authorisation decision the evaluator makes.

    Append-only. High volume — the evaluator batches writes through a
    queue (see `openspine.identity.authz`). Schema is hand-rolled (no
    BusinessTableMixin) so updates are impossible at the schema level.
    """

    __tablename__ = "id_auth_decision_log"
    __table_args__ = (
        CheckConstraint(
            _enum_check("decision", DECISION_OUTCOMES),
            name="ck_id_auth_decision_log_decision",
        ),
        Index(
            "ix_id_auth_decision_log_tenant_principal",
            "tenant_id",
            "principal_id",
        ),
        Index("ix_id_auth_decision_log_principal", "principal_id"),
        Index("ix_id_auth_decision_log_trace", "trace_id"),
        Index("ix_id_auth_decision_log_evaluated_at", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    action_code: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualifier_values: Mapped[dict[str, Any]] = mapped_column(
        "qualifier_values",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    matched_role_single_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    sod_rule_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# ---------------------------------------------------------------------------
# Agent decision trace (append-only, distinct from id_audit_event +
# id_auth_decision_log per docs/identity/README.md §"Audit topology").
# ---------------------------------------------------------------------------


class IdAgentDecisionTrace(Base):
    """The "why" stream for agent actions.

    Per `docs/identity/README.md` §"Audit topology", this answers
    "why did the agent do what it did?". Joins to the corresponding
    `id_audit_event` row (the "what") and any `id_auth_decision_log`
    rows (the "allowed?") via `trace_id`.

    `candidates_considered` and `chosen_path` are JSONB so the
    embedding payload, candidate scores, and final selection all
    survive the round-trip. This is what reviewers use to reconstruct
    an agent's decision a year later.

    Append-only — like every audit-shaped table. No trigger, no
    `updated_*`, no `version`.
    """

    __tablename__ = "id_agent_decision_trace"
    __table_args__ = (
        Index(
            "ix_id_agent_decision_trace_tenant_principal",
            "tenant_id",
            "principal_id",
        ),
        Index("ix_id_agent_decision_trace_principal", "principal_id"),
        Index("ix_id_agent_decision_trace_trace", "trace_id"),
        Index("ix_id_agent_decision_trace_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action_summary: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    candidates_considered: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    chosen_path: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    related_audit_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_audit_event.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
        index=True,
    )
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# ---------------------------------------------------------------------------
# Registries (consumed by migration + schema-invariants test)
# ---------------------------------------------------------------------------


RBAC_TABLES_WITH_UPDATE_TRIGGER: tuple[str, ...] = (
    IdAuthObject.__tablename__,
    IdAuthObjectAction.__tablename__,
    IdAuthObjectQualifier.__tablename__,
    IdRoleSingle.__tablename__,
    IdRoleComposite.__tablename__,
    IdRoleCompositeMember.__tablename__,
    IdPermission.__tablename__,
    IdPrincipalRole.__tablename__,
    IdSodRule.__tablename__,
    IdSodRuleClause.__tablename__,
    IdSodOverride.__tablename__,
)

RBAC_TABLES_WITH_RLS: tuple[str, ...] = (
    *RBAC_TABLES_WITH_UPDATE_TRIGGER,
    IdAuthDecisionLog.__tablename__,
    IdAgentDecisionTrace.__tablename__,
)


__all__ = [
    "DECISION_OUTCOMES",
    "QUALIFIER_DATA_TYPES",
    "RBAC_TABLES_WITH_RLS",
    "RBAC_TABLES_WITH_UPDATE_TRIGGER",
    "ROLE_KINDS",
    "SOD_SEVERITIES",
    "IdAgentDecisionTrace",
    "IdAuthDecisionLog",
    "IdAuthObject",
    "IdAuthObjectAction",
    "IdAuthObjectQualifier",
    "IdPermission",
    "IdPrincipalRole",
    "IdRoleComposite",
    "IdRoleCompositeMember",
    "IdRoleSingle",
    "IdSodOverride",
    "IdSodRule",
    "IdSodRuleClause",
]
