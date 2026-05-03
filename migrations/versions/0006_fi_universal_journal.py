"""fi universal journal — fin_* + co_cost_centre

Revision ID: 0006_fi_universal_journal
Revises: 0005_agent_decision_trace
Create Date: 2026-05-03

Lands the v0.2 universal-journal core per ADR 0003 + fi-finance.md.
Five tables:

- fin_ledger (mutable; trigger + RLS)
- fin_document_type (mutable; trigger + RLS)
- co_cost_centre (mutable; trigger + RLS)
- fin_document_header (append-only; RLS, no trigger)
- fin_document_line (append-only; RLS, no trigger)

Append-only tables get hand-rolled created_at / created_by columns
(no created_by FK indexing; no updated_*; no version). Per
data-model.md, fin_document_* is reversal-by-new-row, never updated.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006_fi_universal_journal"
down_revision: str | Sequence[str] | None = "0005_agent_decision_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRIGGER_TABLES: tuple[str, ...] = (
    "co_cost_centre",
    "fin_ledger",
    "fin_document_type",
)

_RLS_TABLES: tuple[str, ...] = (
    *_TRIGGER_TABLES,
    "fin_document_header",
    "fin_document_line",
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
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
    # 1. co_cost_centre
    op.create_table(
        "co_cost_centre",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "controlling_area_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_controlling_area.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("responsible_person", sa.Text(), nullable=True),
        sa.Column(
            "blocked_for_posting",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "controlling_area_id",
            "code",
            name="uq_co_cost_centre_code",
        ),
    )
    op.create_index(
        "ix_co_cost_centre_validity",
        "co_cost_centre",
        ["tenant_id", "controlling_area_id", "valid_from", "valid_to"],
    )

    # 2. fin_ledger
    op.create_table(
        "fin_ledger",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "is_leading", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("system_key", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fin_ledger_code"),
    )

    # 3. fin_document_type
    op.create_table(
        "fin_document_type",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("number_range_object", sa.Text(), nullable=True),
        sa.Column(
            "is_reversal", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("system_key", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fin_document_type_code"),
    )

    # 4. fin_document_header (append-only)
    op.create_table(
        "fin_document_header",
        _id_pk(),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "company_code_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_company_code.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_type_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("fin_document_type.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("document_number", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column(
            "entry_date",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("header_text", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'posted'")
        ),
        sa.Column(
            "reversal_of_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("fin_document_header.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "reversed_by_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("fin_document_header.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_code_id",
            "fiscal_year",
            "document_type_id",
            "document_number",
            name="uq_fin_document_header_number",
        ),
        sa.CheckConstraint(
            "status IN ('posted', 'reversed')",
            name="ck_fin_document_header_status",
        ),
    )
    op.create_index(
        "ix_fin_document_header_company_period",
        "fin_document_header",
        ["tenant_id", "company_code_id", "fiscal_year", "period"],
    )
    op.create_index(
        "ix_fin_document_header_posting_date",
        "fin_document_header",
        ["tenant_id", "posting_date"],
    )

    # 5. fin_document_line (append-only)
    op.create_table(
        "fin_document_line",
        _id_pk(),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_header_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("fin_document_header.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column(
            "company_code_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_company_code.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column(
            "gl_account_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_gl_account.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "ledger_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("fin_ledger.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("debit_credit", sa.Text(), nullable=False),
        sa.Column("amount_local", sa.Numeric(19, 4), nullable=False),
        sa.Column(
            "local_currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_document", sa.Numeric(19, 4), nullable=True),
        sa.Column(
            "document_currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("amount_group", sa.Numeric(19, 4), nullable=True),
        sa.Column(
            "group_currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "business_partner_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_business_partner.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "cost_centre_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("co_cost_centre.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("profit_centre_code", sa.Text(), nullable=True),
        sa.Column("internal_order_code", sa.Text(), nullable=True),
        sa.Column("segment_code", sa.Text(), nullable=True),
        sa.Column("project_code", sa.Text(), nullable=True),
        sa.Column("tax_code", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(19, 4), nullable=True),
        sa.Column(
            "quantity_uom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_uom.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("line_text", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.UniqueConstraint(
            "document_header_id", "line_number", name="uq_fin_document_line_number"
        ),
        sa.CheckConstraint(
            "debit_credit IN ('D', 'C')", name="ck_fin_document_line_debit_credit"
        ),
        sa.CheckConstraint(
            "amount_local >= 0", name="ck_fin_document_line_amount_positive"
        ),
    )
    op.create_index(
        "ix_fin_document_line_gl_account",
        "fin_document_line",
        ["tenant_id", "gl_account_id", "posting_date"],
    )
    op.create_index(
        "ix_fin_document_line_cost_centre",
        "fin_document_line",
        ["tenant_id", "cost_centre_id", "posting_date"],
    )
    op.create_index(
        "ix_fin_document_line_company_period",
        "fin_document_line",
        ["tenant_id", "company_code_id", "fiscal_year", "period"],
    )
    op.create_index(
        "ix_fin_document_line_business_partner",
        "fin_document_line",
        ["tenant_id", "business_partner_id"],
    )

    # 6. Triggers + RLS
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
    op.drop_table("fin_document_line")
    op.drop_table("fin_document_header")
    op.drop_table("fin_document_type")
    op.drop_table("fin_ledger")
    op.drop_table("co_cost_centre")
