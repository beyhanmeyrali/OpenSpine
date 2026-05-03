"""rbac core — auth-objects, roles, permissions, SoD, decision log

Revision ID: 0003_rbac_core
Revises: 0002_identity_core
Create Date: 2026-05-03

Lands the v0.1 §4.3 schema:

- auth-object catalogue: id_auth_object, _action, _qualifier
- two-tier roles: id_role_single, id_role_composite, _composite_member
- permissions: id_permission (role → auth_object + action + qualifier values)
- assignment: id_principal_role
- SoD: id_sod_rule, _clause, _override
- audit: id_auth_decision_log (append-only)

Same pattern as 0002: BEFORE UPDATE trigger attached per-table for the
mutable tables; RLS enabled with `tenant_isolation` policy on every
tenant-scoped table; CHECK constraints inline.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003_rbac_core"
down_revision: str | Sequence[str] | None = "0002_identity_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRIGGER_TABLES: tuple[str, ...] = (
    "id_auth_object",
    "id_auth_object_action",
    "id_auth_object_qualifier",
    "id_role_single",
    "id_role_composite",
    "id_role_composite_member",
    "id_permission",
    "id_principal_role",
    "id_sod_rule",
    "id_sod_rule_clause",
    "id_sod_override",
)

_RLS_TABLES: tuple[str, ...] = (
    *_TRIGGER_TABLES,
    "id_auth_decision_log",
)


def _audit_columns_no_pk() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    ]


def _id_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        pg.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _tenant_fk() -> sa.Column[object]:
    return sa.Column(
        "tenant_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
        index=True,
    )


def upgrade() -> None:
    # 1. id_auth_object
    op.create_table(
        "id_auth_object",
        _id_pk(),
        _tenant_fk(),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("plugin_id", sa.Text(), nullable=True),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("system_key", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "domain", name="uq_id_auth_object_domain"),
    )

    # 2. id_auth_object_action
    op.create_table(
        "id_auth_object_action",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "auth_object_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_auth_object.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action_code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "auth_object_id", "action_code", name="uq_id_auth_object_action_code"
        ),
    )

    # 3. id_auth_object_qualifier
    op.create_table(
        "id_auth_object_qualifier",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "auth_object_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_auth_object.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("qualifier_code", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "auth_object_id",
            "qualifier_code",
            name="uq_id_auth_object_qualifier_code",
        ),
        sa.CheckConstraint(
            "data_type IN ('string_list', 'numeric_range', 'amount_range', 'wildcard')",
            name="ck_id_auth_object_qualifier_data_type",
        ),
    )

    # 4. id_role_single
    op.create_table(
        "id_role_single",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("module", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("system_key", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_id_role_single_code"),
    )

    # 5. id_role_composite
    op.create_table(
        "id_role_composite",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("system_key", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_id_role_composite_code"),
    )

    # 6. id_role_composite_member
    op.create_table(
        "id_role_composite_member",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "composite_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_role_composite.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "single_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_role_single.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "composite_id", "single_id", name="uq_id_role_composite_member"
        ),
    )

    # 7. id_permission
    op.create_table(
        "id_permission",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "role_single_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_role_single.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "auth_object_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_auth_object.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action_code", sa.Text(), nullable=False),
        sa.Column(
            "qualifier_values",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "role_single_id",
            "auth_object_id",
            "action_code",
            name="uq_id_permission_unique",
        ),
    )

    # 8. id_principal_role
    op.create_table(
        "id_principal_role",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "principal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_single_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_role_single.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "role_composite_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_role_composite.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "scope_qualifiers",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns_no_pk(),
        sa.CheckConstraint(
            "(role_single_id IS NOT NULL) <> (role_composite_id IS NOT NULL)",
            name="ck_id_principal_role_exactly_one",
        ),
    )
    op.create_index(
        "ix_id_principal_role_principal", "id_principal_role", ["principal_id", "tenant_id"]
    )

    # 9. id_sod_rule
    op.create_table(
        "id_sod_rule",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "severity", sa.Text(), nullable=False, server_default=sa.text("'block'")
        ),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("system_key", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_id_sod_rule_code"),
        sa.CheckConstraint(
            "severity IN ('block', 'warn')", name="ck_id_sod_rule_severity"
        ),
    )

    # 10. id_sod_rule_clause
    op.create_table(
        "id_sod_rule_clause",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "sod_rule_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_sod_rule.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "auth_object_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_auth_object.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action_code", sa.Text(), nullable=False),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "sod_rule_id",
            "auth_object_id",
            "action_code",
            name="uq_id_sod_rule_clause",
        ),
    )

    # 11. id_sod_override
    op.create_table(
        "id_sod_override",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "principal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sod_rule_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_sod_rule.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "approver_principal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns_no_pk(),
    )
    op.create_index(
        "ix_id_sod_override_principal_rule",
        "id_sod_override",
        ["principal_id", "sod_rule_id"],
    )

    # 12. id_auth_decision_log (append-only)
    op.create_table(
        "id_auth_decision_log",
        _id_pk(),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
            nullable=True,
        ),
        sa.Column("trace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("action_code", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "qualifier_values",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("matched_role_single_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("sod_rule_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "decision IN ('allow', 'deny', 'sod_block')",
            name="ck_id_auth_decision_log_decision",
        ),
    )
    op.create_index(
        "ix_id_auth_decision_log_tenant_principal",
        "id_auth_decision_log",
        ["tenant_id", "principal_id"],
    )
    op.create_index(
        "ix_id_auth_decision_log_principal", "id_auth_decision_log", ["principal_id"]
    )
    op.create_index(
        "ix_id_auth_decision_log_trace", "id_auth_decision_log", ["trace_id"]
    )
    op.create_index(
        "ix_id_auth_decision_log_evaluated_at",
        "id_auth_decision_log",
        ["evaluated_at"],
    )

    # Triggers + RLS
    for table in _TRIGGER_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_touch_updated_audit
              BEFORE UPDATE ON {table}
              FOR EACH ROW EXECUTE FUNCTION _id_touch_updated_audit();
            """
        )
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = current_setting('openspine.tenant_id', true)::uuid);
            """
        )


def downgrade() -> None:
    for table in reversed(_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    for table in reversed(_TRIGGER_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_touch_updated_audit ON {table};")

    op.drop_index(
        "ix_id_auth_decision_log_evaluated_at", table_name="id_auth_decision_log"
    )
    op.drop_index("ix_id_auth_decision_log_trace", table_name="id_auth_decision_log")
    op.drop_index(
        "ix_id_auth_decision_log_principal", table_name="id_auth_decision_log"
    )
    op.drop_index(
        "ix_id_auth_decision_log_tenant_principal", table_name="id_auth_decision_log"
    )
    op.drop_table("id_auth_decision_log")

    op.drop_index("ix_id_sod_override_principal_rule", table_name="id_sod_override")
    op.drop_table("id_sod_override")
    op.drop_table("id_sod_rule_clause")
    op.drop_table("id_sod_rule")
    op.drop_index("ix_id_principal_role_principal", table_name="id_principal_role")
    op.drop_table("id_principal_role")
    op.drop_table("id_permission")
    op.drop_table("id_role_composite_member")
    op.drop_table("id_role_composite")
    op.drop_table("id_role_single")
    op.drop_table("id_auth_object_qualifier")
    op.drop_table("id_auth_object_action")
    op.drop_table("id_auth_object")
