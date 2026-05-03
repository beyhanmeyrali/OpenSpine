"""Master Data ORM models (`md_*` tables).

Implements the v0.1 §4.4 schema described in `docs/modules/md-master-data.md`.

The cut covers everything the v0.1 acceptance happy path needs:
tenant → Company Code → CoA + GL accounts → vendor BP → material →
FX rates → open posting period. Plus the supporting org-structure
(plant, storage location, purchasing org/group, controlling area)
and configuration (calendar, fiscal year variant, posting period
variant, number ranges).

Global catalogues — `md_currency`, `md_exchange_rate_type`, `md_uom`,
`md_uom_conversion` — are the documented exceptions to "every business
table carries `tenant_id`". They're shared across tenants because their
contents (ISO 4217 currency codes, kg/m/EA unit codes) are universal.

Custom-field hooks: every entity in this module can be extended by
plugins via `ext_<plugin_id>_<field>` columns added through the
plugin's own migration. The serialiser surfaces them automatically.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openspine.core.database import AuditMixin, Base, BusinessTableMixin

# ---------------------------------------------------------------------------
# Vocabularies — CHECK-constrained TEXT, not lookup tables (core-owned).
# ---------------------------------------------------------------------------

POSTING_PERIOD_STATES = ("open", "closed", "special")
BP_KIND = ("organisation", "person")
ADDRESS_TYPES = ("legal", "shipping", "billing", "other")
PRICE_CONTROL = ("S", "V")  # S = standard price, V = moving average
MATERIAL_TYPES = ("FERT", "ROH", "HALB", "DIEN", "HAWA", "HIBE")  # SAP-style
INDUSTRY_SECTORS = ("M", "C", "P", "A", "B", "S")  # mech / chem / pharma / auto / build / svcs
RATE_TYPES = ("M", "B", "G")  # average, bank-selling, bank-buying


def _enum_check(column: str, allowed: tuple[str, ...]) -> str:
    inside = ", ".join(f"'{v}'" for v in allowed)
    return f"{column} IN ({inside})"


# ---------------------------------------------------------------------------
# Global catalogues (no tenant_id, no RLS)
# ---------------------------------------------------------------------------


class MdCurrency(AuditMixin, Base):
    """ISO 4217 currency master.

    Global because currency codes are universal. `decimals` is the
    minor-unit count (USD/EUR=2; JPY=0; some Middle-Eastern dinars=3).
    """

    __tablename__ = "md_currency"
    __table_args__ = (UniqueConstraint("code", name="uq_md_currency_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))


class MdExchangeRateType(AuditMixin, Base):
    """FX rate types. Per spec: M=average, B=bank-selling, G=bank-buying."""

    __tablename__ = "md_exchange_rate_type"
    __table_args__ = (
        UniqueConstraint("code", name="uq_md_exchange_rate_type_code"),
        CheckConstraint(_enum_check("code", RATE_TYPES), name="ck_md_exchange_rate_type_code"),
    )

    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class MdUom(AuditMixin, Base):
    """Unit of measure catalogue. Global — kg, m, EA, etc."""

    __tablename__ = "md_uom"
    __table_args__ = (UniqueConstraint("code", name="uq_md_uom_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[str | None] = mapped_column(Text, nullable=True)  # mass/length/count/...


class MdUomConversion(AuditMixin, Base):
    """Global UoM conversions — `1 base = (numerator/denominator) alt`.

    For mass: 1 kg = 1000/1 g. Material-specific overrides live on
    `md_material_uom`.
    """

    __tablename__ = "md_uom_conversion"
    __table_args__ = (UniqueConstraint("from_uom_id", "to_uom_id", name="uq_md_uom_conversion"),)

    from_uom_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_uom.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    to_uom_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_uom.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    numerator: Mapped[Decimal] = mapped_column(Numeric(19, 9), nullable=False)
    denominator: Mapped[Decimal] = mapped_column(
        Numeric(19, 9), nullable=False, server_default=text("1")
    )


# ---------------------------------------------------------------------------
# FX rates — tenant-scoped (each tenant maintains their own rates)
# ---------------------------------------------------------------------------


class MdFxRate(BusinessTableMixin, Base):
    """Daily FX rates for `(from, to, rate_type, valid_from)`."""

    __tablename__ = "md_fx_rate"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "rate_type",
            "from_currency_id",
            "to_currency_id",
            "valid_from",
            name="uq_md_fx_rate",
        ),
        CheckConstraint(_enum_check("rate_type", RATE_TYPES), name="ck_md_fx_rate_type"),
    )

    rate_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_currency_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    to_currency_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(19, 9), nullable=False)


# ---------------------------------------------------------------------------
# Organisational structure
# ---------------------------------------------------------------------------


class MdControllingArea(BusinessTableMixin, Base):
    """Management accounting scope. One currency for internal reporting.
    A Company Code attaches to exactly one CO area."""

    __tablename__ = "md_controlling_area"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_controlling_area_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class MdFiscalYearVariant(BusinessTableMixin, Base):
    """Number of posting periods per fiscal year + calendar mapping.

    `period_count` is typically 12 (calendar months) plus optional
    special periods. Detailed calendar→period mapping is stored as
    JSONB to avoid an O(N) variant-period table for what is usually
    a static config.
    """

    __tablename__ = "md_fiscal_year_variant"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_fiscal_year_variant_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("12"))
    special_period_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("4")
    )
    calendar_year_dependent: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )


class MdFactoryCalendar(BusinessTableMixin, Base):
    """Working-days / holidays per location.

    The list of holidays + non-working-days lives in a JSONB column
    rather than a child table — they're effectively a static config
    blob and querying individual dates is rare (the application
    materialises the calendar at the start of an MRP run).
    """

    __tablename__ = "md_factory_calendar"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_factory_calendar_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    working_days: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            """'{"mon": true, "tue": true, "wed": true, "thu": true, "fri": true, "sat": false, "sun": false}'::jsonb"""
        ),
    )
    holidays: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class MdCompanyCode(BusinessTableMixin, Base):
    """Legal entity. Books are closed at this level."""

    __tablename__ = "md_company_code"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_company_code_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str] = mapped_column(Text, nullable=False)  # ISO 3166-1 alpha-2
    local_currency_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    chart_of_accounts_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_chart_of_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fiscal_year_variant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_fiscal_year_variant.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    controlling_area_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_controlling_area.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class MdPlant(BusinessTableMixin, Base):
    """Physical or logical site. Belongs to one Company Code."""

    __tablename__ = "md_plant"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_plant_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    company_code_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_company_code.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    factory_calendar_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_factory_calendar.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class MdStorageLocation(BusinessTableMixin, Base):
    """Stocking point within a Plant."""

    __tablename__ = "md_storage_location"
    __table_args__ = (
        UniqueConstraint("tenant_id", "plant_id", "code", name="uq_md_storage_location_code"),
    )

    plant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_plant.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class MdPurchasingOrg(BusinessTableMixin, Base):
    """Buying unit. Can span Plants — assignment table not modelled here
    because v0.1 pilots typically run with one Purch Org per Company Code."""

    __tablename__ = "md_purchasing_org"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_purchasing_org_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    company_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_company_code.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class MdPurchasingGroup(BusinessTableMixin, Base):
    """Buyer team or individual responsibility. Orthogonal to Purch Org."""

    __tablename__ = "md_purchasing_group"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_purchasing_group_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# Posting periods
# ---------------------------------------------------------------------------


class MdPostingPeriod(BusinessTableMixin, Base):
    """Open/closed state per `(company_code, fiscal_year, period)`.

    Account-range scoping (the spec mentions "open for accountants, closed
    for everyone else") is modelled via JSONB `account_range_overrides`
    rather than a child table — overrides are sparse in practice.
    """

    __tablename__ = "md_posting_period"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_code_id",
            "fiscal_year",
            "period",
            name="uq_md_posting_period",
        ),
        CheckConstraint(
            _enum_check("state", POSTING_PERIOD_STATES), name="ck_md_posting_period_state"
        ),
    )

    company_code_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_company_code.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'closed'"))
    period_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_range_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


# ---------------------------------------------------------------------------
# Number ranges
# ---------------------------------------------------------------------------


class MdNumberRange(BusinessTableMixin, Base):
    """Number-range definition: a name + range bounds + scope.

    The actual sequential counter lives on this row (`current_number`).
    Concurrent allocation uses a row-level FOR UPDATE lock in the
    service layer.
    """

    __tablename__ = "md_number_range"
    __table_args__ = (
        UniqueConstraint("tenant_id", "object_type", "scope", name="uq_md_number_range_scope"),
        CheckConstraint("from_number <= to_number", name="ck_md_number_range_bounds"),
    )

    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'default'"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_number: Mapped[int] = mapped_column(Integer, nullable=False)
    to_number: Mapped[int] = mapped_column(Integer, nullable=False)
    current_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Chart of Accounts + GL master
# ---------------------------------------------------------------------------


class MdChartOfAccounts(BusinessTableMixin, Base):
    """Operational chart of accounts header."""

    __tablename__ = "md_chart_of_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_md_chart_of_accounts_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en'"))
    account_length: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))


class MdAccountGroup(BusinessTableMixin, Base):
    """Account grouping (ASSETS / LIABILITIES / REVENUES / etc.)."""

    __tablename__ = "md_account_group"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "chart_of_accounts_id",
            "code",
            name="uq_md_account_group_code",
        ),
    )

    chart_of_accounts_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_chart_of_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    from_account: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_account: Mapped[str | None] = mapped_column(Text, nullable=True)


class MdGlAccount(BusinessTableMixin, Base):
    """GL account master at the chart-of-accounts level."""

    __tablename__ = "md_gl_account"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "chart_of_accounts_id",
            "account_number",
            name="uq_md_gl_account_number",
        ),
    )

    chart_of_accounts_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_chart_of_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_number: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    account_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_account_group.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    account_kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'balance_sheet' | 'pnl'
    is_recon: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    recon_kind: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 'vendor'|'customer'|'asset'


class MdGlAccountCompany(BusinessTableMixin, Base):
    """Company-code-specific GL properties."""

    __tablename__ = "md_gl_account_company"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "gl_account_id",
            "company_code_id",
            name="uq_md_gl_account_company",
        ),
    )

    gl_account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_gl_account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_code_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_company_code.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    currency_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    tax_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_for_posting: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# ---------------------------------------------------------------------------
# Business Partner
# ---------------------------------------------------------------------------


class MdBusinessPartner(BusinessTableMixin, Base):
    """Unified BP — customer, vendor, employee. Roles attached separately."""

    __tablename__ = "md_business_partner"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_md_business_partner_number"),
        CheckConstraint(_enum_check("kind", BP_KIND), name="ck_md_business_partner_kind"),
        Index("ix_md_business_partner_name", "tenant_id", "name"),
    )

    number: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


class MdBpRole(BusinessTableMixin, Base):
    """A BP's roles. A BP can hold several over time (vendor + customer).

    `role` is `customer` | `vendor` | `employee` | `prospect`. Active
    bracketed by `valid_from`/`valid_to` (NULL = open-ended).
    """

    __tablename__ = "md_bp_role"
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_partner_id", "role", name="uq_md_bp_role"),
    )

    business_partner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_business_partner.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class MdBpAddress(BusinessTableMixin, Base):
    """Addresses attached to a BP."""

    __tablename__ = "md_bp_address"
    __table_args__ = (
        CheckConstraint(_enum_check("kind", ADDRESS_TYPES), name="ck_md_bp_address_kind"),
    )

    business_partner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_business_partner.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    line1: Mapped[str] = mapped_column(Text, nullable=False)
    line2: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_code: Mapped[str] = mapped_column(Text, nullable=False)


class MdBpBank(BusinessTableMixin, Base):
    """Bank accounts attached to a BP."""

    __tablename__ = "md_bp_bank"

    business_partner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_business_partner.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    country_code: Mapped[str] = mapped_column(Text, nullable=False)
    iban: Mapped[str | None] = mapped_column(Text, nullable=True)
    swift_bic: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_holder: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


class MdMaterial(BusinessTableMixin, Base):
    """Material master — basic data valid across plants."""

    __tablename__ = "md_material"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_md_material_number"),
        CheckConstraint(_enum_check("material_type", MATERIAL_TYPES), name="ck_md_material_type"),
        CheckConstraint(
            _enum_check("industry_sector", INDUSTRY_SECTORS),
            name="ck_md_material_industry_sector",
        ),
        Index("ix_md_material_type", "tenant_id", "material_type"),
    )

    number: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    material_type: Mapped[str] = mapped_column(Text, nullable=False)
    industry_sector: Mapped[str] = mapped_column(Text, nullable=False)
    base_uom_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_uom.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    flagged_for_deletion: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


class MdMaterialPlant(BusinessTableMixin, Base):
    """Plant-level material extension (procurement, MRP)."""

    __tablename__ = "md_material_plant"
    __table_args__ = (
        UniqueConstraint("tenant_id", "material_id", "plant_id", name="uq_md_material_plant"),
    )

    material_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_material.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_plant.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    procurement_type: Mapped[str | None] = mapped_column(Text, nullable=True)  # E/F/X
    mrp_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    mrp_controller: Mapped[str | None] = mapped_column(Text, nullable=True)
    minimum_order_qty: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    safety_stock: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)


class MdMaterialValuation(BusinessTableMixin, Base):
    """Valuation-area-level (price control + price)."""

    __tablename__ = "md_material_valuation"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "material_id",
            "valuation_area_id",
            name="uq_md_material_valuation",
        ),
        CheckConstraint(
            _enum_check("price_control", PRICE_CONTROL),
            name="ck_md_material_valuation_price_control",
        ),
    )

    material_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_material.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # In v0.1 valuation area = plant. ECC users may recognise this as
    # the simple case; group-level valuation lands later if needed.
    valuation_area_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_plant.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price_control: Mapped[str] = mapped_column(Text, nullable=False)
    standard_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    moving_avg_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    currency_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    valuation_class: Mapped[str | None] = mapped_column(Text, nullable=True)


class MdMaterialUom(BusinessTableMixin, Base):
    """Material-specific alternative UoM with conversion to base."""

    __tablename__ = "md_material_uom"
    __table_args__ = (
        UniqueConstraint("tenant_id", "material_id", "alt_uom_id", name="uq_md_material_uom"),
    )

    material_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_material.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alt_uom_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_uom.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    numerator: Mapped[Decimal] = mapped_column(Numeric(19, 9), nullable=False)
    denominator: Mapped[Decimal] = mapped_column(
        Numeric(19, 9), nullable=False, server_default=text("1")
    )
    ean: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Registries (consumed by migration + schema-invariants test)
# ---------------------------------------------------------------------------


# All MD tables that carry tenant_id and need RLS. The four global
# catalogues are excluded.
MD_GLOBAL_TABLES: tuple[str, ...] = (
    MdCurrency.__tablename__,
    MdExchangeRateType.__tablename__,
    MdUom.__tablename__,
    MdUomConversion.__tablename__,
)

MD_TENANT_TABLES: tuple[str, ...] = (
    MdControllingArea.__tablename__,
    MdFiscalYearVariant.__tablename__,
    MdFactoryCalendar.__tablename__,
    MdChartOfAccounts.__tablename__,
    MdAccountGroup.__tablename__,
    MdGlAccount.__tablename__,
    MdGlAccountCompany.__tablename__,
    MdCompanyCode.__tablename__,
    MdPlant.__tablename__,
    MdStorageLocation.__tablename__,
    MdPurchasingOrg.__tablename__,
    MdPurchasingGroup.__tablename__,
    MdPostingPeriod.__tablename__,
    MdNumberRange.__tablename__,
    MdFxRate.__tablename__,
    MdBusinessPartner.__tablename__,
    MdBpRole.__tablename__,
    MdBpAddress.__tablename__,
    MdBpBank.__tablename__,
    MdMaterial.__tablename__,
    MdMaterialPlant.__tablename__,
    MdMaterialValuation.__tablename__,
    MdMaterialUom.__tablename__,
)

# Update-trigger tables = global tables (for global audit trigger) +
# tenant-scoped tables. All have updated_at/version.
MD_TABLES_WITH_UPDATE_TRIGGER: tuple[str, ...] = (
    *MD_GLOBAL_TABLES,
    *MD_TENANT_TABLES,
)


__all__ = [
    "ADDRESS_TYPES",
    "BP_KIND",
    "INDUSTRY_SECTORS",
    "MATERIAL_TYPES",
    "MD_GLOBAL_TABLES",
    "MD_TABLES_WITH_UPDATE_TRIGGER",
    "MD_TENANT_TABLES",
    "POSTING_PERIOD_STATES",
    "PRICE_CONTROL",
    "RATE_TYPES",
    "MdAccountGroup",
    "MdBpAddress",
    "MdBpBank",
    "MdBpRole",
    "MdBusinessPartner",
    "MdChartOfAccounts",
    "MdCompanyCode",
    "MdControllingArea",
    "MdCurrency",
    "MdExchangeRateType",
    "MdFactoryCalendar",
    "MdFiscalYearVariant",
    "MdFxRate",
    "MdGlAccount",
    "MdGlAccountCompany",
    "MdMaterial",
    "MdMaterialPlant",
    "MdMaterialUom",
    "MdMaterialValuation",
    "MdNumberRange",
    "MdPlant",
    "MdPostingPeriod",
    "MdPurchasingGroup",
    "MdPurchasingOrg",
    "MdStorageLocation",
]
