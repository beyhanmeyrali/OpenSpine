"""master data — md_* tables, RLS, audit triggers

Revision ID: 0004_master_data
Revises: 0003_rbac_core
Create Date: 2026-05-03

Lands the v0.1 §4.4 schema. 27 tables, four of which are global
(no `tenant_id`, no RLS): `md_currency`, `md_exchange_rate_type`,
`md_uom`, `md_uom_conversion`. Remaining 23 are tenant-scoped with
the same `tenant_isolation` policy used in 0002 / 0003.

Order of CREATE TABLE matters: every FK target must already exist.
The block below sequences accordingly. `md_company_code` carries
several non-deferred FKs into other md_* tables (CoA, fiscal year
variant, controlling area), so those tables come first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004_master_data"
down_revision: str | Sequence[str] | None = "0003_rbac_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GLOBAL_TABLES: tuple[str, ...] = (
    "md_currency",
    "md_exchange_rate_type",
    "md_uom",
    "md_uom_conversion",
)

_TENANT_TABLES: tuple[str, ...] = (
    "md_controlling_area",
    "md_fiscal_year_variant",
    "md_factory_calendar",
    "md_chart_of_accounts",
    "md_account_group",
    "md_gl_account",
    "md_gl_account_company",
    "md_company_code",
    "md_plant",
    "md_storage_location",
    "md_purchasing_org",
    "md_purchasing_group",
    "md_posting_period",
    "md_number_range",
    "md_fx_rate",
    "md_business_partner",
    "md_bp_role",
    "md_bp_address",
    "md_bp_bank",
    "md_material",
    "md_material_plant",
    "md_material_valuation",
    "md_material_uom",
)

_ALL_TABLES: tuple[str, ...] = (*_GLOBAL_TABLES, *_TENANT_TABLES)


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
            "version", sa.Integer(), nullable=False, server_default=sa.text("1")
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
    # 1. Global catalogues
    op.create_table(
        "md_currency",
        _id_pk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False, server_default=sa.text("2")),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("code", name="uq_md_currency_code"),
    )
    op.create_table(
        "md_exchange_rate_type",
        _id_pk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("code", name="uq_md_exchange_rate_type_code"),
        sa.CheckConstraint(
            "code IN ('M', 'B', 'G')", name="ck_md_exchange_rate_type_code"
        ),
    )
    op.create_table(
        "md_uom",
        _id_pk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("code", name="uq_md_uom_code"),
    )
    op.create_table(
        "md_uom_conversion",
        _id_pk(),
        sa.Column(
            "from_uom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_uom.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "to_uom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_uom.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("numerator", sa.Numeric(19, 9), nullable=False),
        sa.Column("denominator", sa.Numeric(19, 9), nullable=False, server_default=sa.text("1")),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("from_uom_id", "to_uom_id", name="uq_md_uom_conversion"),
    )

    # 2. Configuration tables that other tables depend on
    op.create_table(
        "md_controlling_area",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_controlling_area_code"),
    )
    op.create_table(
        "md_fiscal_year_variant",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("period_count", sa.Integer(), nullable=False, server_default=sa.text("12")),
        sa.Column(
            "special_period_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("4"),
        ),
        sa.Column(
            "calendar_year_dependent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_fiscal_year_variant_code"),
    )
    op.create_table(
        "md_factory_calendar",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "working_days",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text(
                """'{"mon": true, "tue": true, "wed": true, "thu": true, "fri": true, "sat": false, "sun": false}'::jsonb"""
            ),
        ),
        sa.Column(
            "holidays",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_factory_calendar_code"),
    )

    # 3. CoA + GL master (CoA needed before company_code)
    op.create_table(
        "md_chart_of_accounts",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default=sa.text("'en'")),
        sa.Column("account_length", sa.Integer(), nullable=False, server_default=sa.text("8")),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_chart_of_accounts_code"),
    )
    op.create_table(
        "md_account_group",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "chart_of_accounts_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_chart_of_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("from_account", sa.Text(), nullable=True),
        sa.Column("to_account", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "chart_of_accounts_id",
            "code",
            name="uq_md_account_group_code",
        ),
    )
    op.create_table(
        "md_gl_account",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "chart_of_accounts_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_chart_of_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("account_number", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "account_group_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_account_group.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("account_kind", sa.Text(), nullable=False),
        sa.Column("is_recon", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recon_kind", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "chart_of_accounts_id",
            "account_number",
            name="uq_md_gl_account_number",
        ),
    )

    # 4. Company Code (depends on CoA + fiscal_year_variant + controlling_area)
    op.create_table(
        "md_company_code",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False),
        sa.Column(
            "local_currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "chart_of_accounts_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_chart_of_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "fiscal_year_variant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_fiscal_year_variant.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "controlling_area_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_controlling_area.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_company_code_code"),
    )

    # 5. md_gl_account_company — depends on company_code + gl_account
    op.create_table(
        "md_gl_account_company",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "gl_account_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_gl_account.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "company_code_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_company_code.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("tax_code", sa.Text(), nullable=True),
        sa.Column(
            "blocked_for_posting",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "gl_account_id",
            "company_code_id",
            name="uq_md_gl_account_company",
        ),
    )

    # 6. Plant + child entities
    op.create_table(
        "md_plant",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "company_code_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_company_code.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "factory_calendar_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_factory_calendar.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_plant_code"),
    )
    op.create_table(
        "md_storage_location",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "plant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_plant.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id", "plant_id", "code", name="uq_md_storage_location_code"
        ),
    )
    op.create_table(
        "md_purchasing_org",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "company_code_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_company_code.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_purchasing_org_code"),
    )
    op.create_table(
        "md_purchasing_group",
        _id_pk(),
        _tenant_fk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_md_purchasing_group_code"),
    )

    # 7. Posting periods
    op.create_table(
        "md_posting_period",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "company_code_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_company_code.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.Text(), nullable=False, server_default=sa.text("'closed'")
        ),
        sa.Column("period_start_date", sa.Date(), nullable=False),
        sa.Column("period_end_date", sa.Date(), nullable=False),
        sa.Column(
            "account_range_overrides",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "company_code_id",
            "fiscal_year",
            "period",
            name="uq_md_posting_period",
        ),
        sa.CheckConstraint(
            "state IN ('open', 'closed', 'special')", name="ck_md_posting_period_state"
        ),
    )

    # 8. Number ranges
    op.create_table(
        "md_number_range",
        _id_pk(),
        _tenant_fk(),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default=sa.text("'default'")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("from_number", sa.Integer(), nullable=False),
        sa.Column("to_number", sa.Integer(), nullable=False),
        sa.Column(
            "current_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id", "object_type", "scope", name="uq_md_number_range_scope"
        ),
        sa.CheckConstraint(
            "from_number <= to_number", name="ck_md_number_range_bounds"
        ),
    )

    # 9. FX rates
    op.create_table(
        "md_fx_rate",
        _id_pk(),
        _tenant_fk(),
        sa.Column("rate_type", sa.Text(), nullable=False),
        sa.Column(
            "from_currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "to_currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(19, 9), nullable=False),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "rate_type",
            "from_currency_id",
            "to_currency_id",
            "valid_from",
            name="uq_md_fx_rate",
        ),
        sa.CheckConstraint(
            "rate_type IN ('M', 'B', 'G')", name="ck_md_fx_rate_type"
        ),
    )

    # 10. Business Partner + children
    op.create_table(
        "md_business_partner",
        _id_pk(),
        _tenant_fk(),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("tax_number", sa.Text(), nullable=True),
        sa.Column("country_code", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "number", name="uq_md_business_partner_number"),
        sa.CheckConstraint(
            "kind IN ('organisation', 'person')", name="ck_md_business_partner_kind"
        ),
    )
    op.create_index(
        "ix_md_business_partner_name", "md_business_partner", ["tenant_id", "name"]
    )
    op.create_table(
        "md_bp_role",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "business_partner_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_business_partner.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id", "business_partner_id", "role", name="uq_md_bp_role"
        ),
    )
    op.create_table(
        "md_bp_address",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "business_partner_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_business_partner.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("line1", sa.Text(), nullable=False),
        sa.Column("line2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("country_code", sa.Text(), nullable=False),
        *_audit_columns_no_pk(),
        sa.CheckConstraint(
            "kind IN ('legal', 'shipping', 'billing', 'other')",
            name="ck_md_bp_address_kind",
        ),
    )
    op.create_table(
        "md_bp_bank",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "business_partner_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_business_partner.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("country_code", sa.Text(), nullable=False),
        sa.Column("iban", sa.Text(), nullable=True),
        sa.Column("swift_bic", sa.Text(), nullable=True),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("account_number", sa.Text(), nullable=True),
        sa.Column("account_holder", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
    )

    # 11. Material + children
    op.create_table(
        "md_material",
        _id_pk(),
        _tenant_fk(),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("material_type", sa.Text(), nullable=False),
        sa.Column("industry_sector", sa.Text(), nullable=False),
        sa.Column(
            "base_uom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_uom.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "flagged_for_deletion",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint("tenant_id", "number", name="uq_md_material_number"),
        sa.CheckConstraint(
            "material_type IN ('FERT', 'ROH', 'HALB', 'DIEN', 'HAWA', 'HIBE')",
            name="ck_md_material_type",
        ),
        sa.CheckConstraint(
            "industry_sector IN ('M', 'C', 'P', 'A', 'B', 'S')",
            name="ck_md_material_industry_sector",
        ),
    )
    op.create_index(
        "ix_md_material_type", "md_material", ["tenant_id", "material_type"]
    )
    op.create_table(
        "md_material_plant",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "material_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_material.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "plant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_plant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("procurement_type", sa.Text(), nullable=True),
        sa.Column("mrp_type", sa.Text(), nullable=True),
        sa.Column("mrp_controller", sa.Text(), nullable=True),
        sa.Column("minimum_order_qty", sa.Numeric(19, 4), nullable=True),
        sa.Column("safety_stock", sa.Numeric(19, 4), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id", "material_id", "plant_id", name="uq_md_material_plant"
        ),
    )
    op.create_table(
        "md_material_valuation",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "material_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_material.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "valuation_area_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_plant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("price_control", sa.Text(), nullable=False),
        sa.Column("standard_price", sa.Numeric(19, 4), nullable=True),
        sa.Column("moving_avg_price", sa.Numeric(19, 4), nullable=True),
        sa.Column(
            "currency_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_currency.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("valuation_class", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id",
            "material_id",
            "valuation_area_id",
            name="uq_md_material_valuation",
        ),
        sa.CheckConstraint(
            "price_control IN ('S', 'V')",
            name="ck_md_material_valuation_price_control",
        ),
    )
    op.create_table(
        "md_material_uom",
        _id_pk(),
        _tenant_fk(),
        sa.Column(
            "material_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_material.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "alt_uom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("md_uom.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("numerator", sa.Numeric(19, 9), nullable=False),
        sa.Column(
            "denominator", sa.Numeric(19, 9), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("ean", sa.Text(), nullable=True),
        *_audit_columns_no_pk(),
        sa.UniqueConstraint(
            "tenant_id", "material_id", "alt_uom_id", name="uq_md_material_uom"
        ),
    )

    # 12. Triggers + RLS
    for table in _ALL_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_touch_updated_audit
              BEFORE UPDATE ON {table}
              FOR EACH ROW EXECUTE FUNCTION _id_touch_updated_audit();
            """
        )
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = current_setting('openspine.tenant_id', true)::uuid);
            """
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    for table in reversed(_ALL_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_touch_updated_audit ON {table};")
        op.drop_table(table)
