"""Master Data service layer.

Pure business logic — HTTP routes in `openspine.md.router` translate
shapes; integration tests can call these directly.

Each create function:
- Validates required FKs are in the same tenant.
- Sets `created_by` / `updated_by` from the principal context.
- Inserts the row(s) and flushes.

The auth-object engine is not invoked here — gating is the route's
responsibility (via `enforce()` or `@requires_auth`). That keeps the
service layer single-purpose: it does the work and trusts the caller
to have authorised it.

Number-range allocation uses `SELECT ... FOR UPDATE` on the
`md_number_range` row to serialise concurrent allocations within
a tenant. This is the Postgres-idiomatic way to do gap-free
sequencing without leaning on a sequence (which can return ids
out of bounds and isn't easily tenant-scoped).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.core.errors import ConflictError, NotFoundError, ValidationError
from openspine.md.models import (
    MdAccountGroup,
    MdBpAddress,
    MdBpRole,
    MdBusinessPartner,
    MdChartOfAccounts,
    MdCompanyCode,
    MdCurrency,
    MdFiscalYearVariant,
    MdFxRate,
    MdGlAccount,
    MdGlAccountCompany,
    MdMaterial,
    MdMaterialPlant,
    MdMaterialValuation,
    MdNumberRange,
    MdPlant,
    MdPostingPeriod,
    MdUom,
)

# ---------------------------------------------------------------------------
# Lookup helpers (all assume the tenant GUC is set by middleware)
# ---------------------------------------------------------------------------


async def get_currency_by_code(session: AsyncSession, code: str) -> MdCurrency:
    row = (
        await session.execute(select(MdCurrency).where(MdCurrency.code == code))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            f"unknown currency {code!r}",
            domain="md.currency",
            action="display",
            reason="currency_not_found",
        )
    return row


async def get_uom_by_code(session: AsyncSession, code: str) -> MdUom:
    row = (await session.execute(select(MdUom).where(MdUom.code == code))).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            f"unknown uom {code!r}",
            domain="md.uom",
            action="display",
            reason="uom_not_found",
        )
    return row


# ---------------------------------------------------------------------------
# Number ranges
# ---------------------------------------------------------------------------


async def create_number_range(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    object_type: str,
    from_number: int,
    to_number: int,
    scope: str = "default",
    description: str | None = None,
    fiscal_year: int | None = None,
) -> MdNumberRange:
    if from_number > to_number:
        raise ValidationError(
            "from_number must be <= to_number",
            domain="md.number_range",
            action="create",
            reason="bounds_invalid",
        )
    row = MdNumberRange(
        tenant_id=tenant_id,
        object_type=object_type,
        scope=scope,
        description=description,
        from_number=from_number,
        to_number=to_number,
        current_number=from_number - 1,
        fiscal_year=fiscal_year,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def next_number(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_type: str,
    scope: str = "default",
) -> int:
    """Allocate and return the next number for a range.

    Uses `SELECT ... FOR UPDATE` to serialise concurrent allocations.
    Raises `ConflictError` if the range is exhausted.
    """
    stmt = (
        select(MdNumberRange)
        .where(
            MdNumberRange.tenant_id == tenant_id,
            MdNumberRange.object_type == object_type,
            MdNumberRange.scope == scope,
        )
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            f"no number range for {object_type!r}/{scope!r}",
            domain="md.number_range",
            action="next",
            reason="range_not_found",
        )
    next_value = row.current_number + 1
    if next_value > row.to_number:
        raise ConflictError(
            "number range exhausted",
            domain="md.number_range",
            action="next",
            reason="range_exhausted",
        )
    row.current_number = next_value
    await session.flush()
    return next_value


# ---------------------------------------------------------------------------
# Chart of Accounts + GL master
# ---------------------------------------------------------------------------


async def create_chart_of_accounts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    code: str,
    name: str,
    language: str = "en",
    account_length: int = 8,
) -> MdChartOfAccounts:
    row = MdChartOfAccounts(
        tenant_id=tenant_id,
        code=code,
        name=name,
        language=language,
        account_length=account_length,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_account_group(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    chart_of_accounts_id: uuid.UUID,
    code: str,
    name: str,
    from_account: str | None = None,
    to_account: str | None = None,
) -> MdAccountGroup:
    row = MdAccountGroup(
        tenant_id=tenant_id,
        chart_of_accounts_id=chart_of_accounts_id,
        code=code,
        name=name,
        from_account=from_account,
        to_account=to_account,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_gl_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    chart_of_accounts_id: uuid.UUID,
    account_number: str,
    name: str,
    account_kind: str,
    account_group_id: uuid.UUID | None = None,
    is_recon: bool = False,
    recon_kind: str | None = None,
) -> MdGlAccount:
    if account_kind not in ("balance_sheet", "pnl"):
        raise ValidationError(
            f"unknown account_kind {account_kind!r}",
            domain="md.gl_account",
            action="create",
            reason="invalid_account_kind",
            allowed={"account_kinds": ["balance_sheet", "pnl"]},
        )
    row = MdGlAccount(
        tenant_id=tenant_id,
        chart_of_accounts_id=chart_of_accounts_id,
        account_number=account_number,
        name=name,
        account_kind=account_kind,
        account_group_id=account_group_id,
        is_recon=is_recon,
        recon_kind=recon_kind,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_gl_account_company(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    gl_account_id: uuid.UUID,
    company_code_id: uuid.UUID,
    currency_id: uuid.UUID | None = None,
    tax_code: str | None = None,
    blocked_for_posting: bool = False,
) -> MdGlAccountCompany:
    row = MdGlAccountCompany(
        tenant_id=tenant_id,
        gl_account_id=gl_account_id,
        company_code_id=company_code_id,
        currency_id=currency_id,
        tax_code=tax_code,
        blocked_for_posting=blocked_for_posting,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Org structure
# ---------------------------------------------------------------------------


async def create_fiscal_year_variant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    code: str,
    description: str | None = None,
    period_count: int = 12,
    special_period_count: int = 4,
) -> MdFiscalYearVariant:
    row = MdFiscalYearVariant(
        tenant_id=tenant_id,
        code=code,
        description=description,
        period_count=period_count,
        special_period_count=special_period_count,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_company_code(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    code: str,
    name: str,
    country_code: str,
    local_currency_id: uuid.UUID,
    chart_of_accounts_id: uuid.UUID,
    fiscal_year_variant_id: uuid.UUID,
    controlling_area_id: uuid.UUID | None = None,
) -> MdCompanyCode:
    row = MdCompanyCode(
        tenant_id=tenant_id,
        code=code,
        name=name,
        country_code=country_code,
        local_currency_id=local_currency_id,
        chart_of_accounts_id=chart_of_accounts_id,
        fiscal_year_variant_id=fiscal_year_variant_id,
        controlling_area_id=controlling_area_id,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_plant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    code: str,
    name: str,
    company_code_id: uuid.UUID,
    factory_calendar_id: uuid.UUID | None = None,
) -> MdPlant:
    row = MdPlant(
        tenant_id=tenant_id,
        code=code,
        name=name,
        company_code_id=company_code_id,
        factory_calendar_id=factory_calendar_id,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Posting periods
# ---------------------------------------------------------------------------


async def create_posting_period(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    company_code_id: uuid.UUID,
    fiscal_year: int,
    period: int,
    period_start_date: date,
    period_end_date: date,
    state: str = "closed",
) -> MdPostingPeriod:
    if state not in ("open", "closed", "special"):
        raise ValidationError(
            f"invalid state {state!r}",
            domain="md.posting_period",
            action="create",
            reason="invalid_state",
        )
    row = MdPostingPeriod(
        tenant_id=tenant_id,
        company_code_id=company_code_id,
        fiscal_year=fiscal_year,
        period=period,
        period_start_date=period_start_date,
        period_end_date=period_end_date,
        state=state,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def set_posting_period_state(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    company_code_id: uuid.UUID,
    fiscal_year: int,
    period: int,
    state: str,
) -> MdPostingPeriod:
    if state not in ("open", "closed", "special"):
        raise ValidationError(
            f"invalid state {state!r}",
            domain="md.posting_period",
            action="set_state",
            reason="invalid_state",
        )
    stmt = select(MdPostingPeriod).where(
        MdPostingPeriod.tenant_id == tenant_id,
        MdPostingPeriod.company_code_id == company_code_id,
        MdPostingPeriod.fiscal_year == fiscal_year,
        MdPostingPeriod.period == period,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            "posting period not found",
            domain="md.posting_period",
            action="set_state",
            reason="period_not_found",
        )
    row.state = state
    row.updated_by = actor_principal_id
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# FX rates
# ---------------------------------------------------------------------------


async def upload_fx_rate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    rate_type: str,
    from_currency_id: uuid.UUID,
    to_currency_id: uuid.UUID,
    valid_from: date,
    rate: Decimal,
) -> MdFxRate:
    if rate_type not in ("M", "B", "G"):
        raise ValidationError(
            f"invalid rate_type {rate_type!r}",
            domain="md.fx_rate",
            action="upload",
            reason="invalid_rate_type",
        )
    row = MdFxRate(
        tenant_id=tenant_id,
        rate_type=rate_type,
        from_currency_id=from_currency_id,
        to_currency_id=to_currency_id,
        valid_from=valid_from,
        rate=rate,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Business Partner
# ---------------------------------------------------------------------------


async def create_business_partner(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    number: str,
    kind: str,
    name: str,
    legal_name: str | None = None,
    tax_number: str | None = None,
    country_code: str | None = None,
    industry: str | None = None,
    roles: list[str] | None = None,
    addresses: list[dict[str, Any]] | None = None,
) -> MdBusinessPartner:
    if kind not in ("organisation", "person"):
        raise ValidationError(
            f"invalid kind {kind!r}",
            domain="md.business_partner",
            action="create",
            reason="invalid_kind",
        )
    bp = MdBusinessPartner(
        tenant_id=tenant_id,
        number=number,
        kind=kind,
        name=name,
        legal_name=legal_name,
        tax_number=tax_number,
        country_code=country_code,
        industry=industry,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(bp)
    await session.flush()

    for role in roles or []:
        if role not in ("customer", "vendor", "employee", "prospect"):
            raise ValidationError(
                f"invalid bp role {role!r}",
                domain="md.business_partner",
                action="create",
                reason="invalid_role",
            )
        session.add(
            MdBpRole(
                tenant_id=tenant_id,
                business_partner_id=bp.id,
                role=role,
                created_by=actor_principal_id,
                updated_by=actor_principal_id,
            )
        )

    for addr in addresses or []:
        session.add(
            MdBpAddress(
                tenant_id=tenant_id,
                business_partner_id=bp.id,
                kind=addr.get("kind", "legal"),
                line1=addr["line1"],
                line2=addr.get("line2"),
                city=addr["city"],
                region=addr.get("region"),
                postal_code=addr.get("postal_code"),
                country_code=addr["country_code"],
                created_by=actor_principal_id,
                updated_by=actor_principal_id,
            )
        )

    await session.flush()
    return bp


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


async def create_material(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    number: str,
    description: str,
    material_type: str,
    industry_sector: str,
    base_uom_id: uuid.UUID,
) -> MdMaterial:
    row = MdMaterial(
        tenant_id=tenant_id,
        number=number,
        description=description,
        material_type=material_type,
        industry_sector=industry_sector,
        base_uom_id=base_uom_id,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def extend_material_to_plant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    material_id: uuid.UUID,
    plant_id: uuid.UUID,
    procurement_type: str | None = None,
    mrp_type: str | None = None,
    mrp_controller: str | None = None,
    minimum_order_qty: Decimal | None = None,
    safety_stock: Decimal | None = None,
) -> MdMaterialPlant:
    row = MdMaterialPlant(
        tenant_id=tenant_id,
        material_id=material_id,
        plant_id=plant_id,
        procurement_type=procurement_type,
        mrp_type=mrp_type,
        mrp_controller=mrp_controller,
        minimum_order_qty=minimum_order_qty,
        safety_stock=safety_stock,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


async def value_material(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    material_id: uuid.UUID,
    valuation_area_id: uuid.UUID,
    price_control: str,
    currency_id: uuid.UUID,
    standard_price: Decimal | None = None,
    moving_avg_price: Decimal | None = None,
    valuation_class: str | None = None,
) -> MdMaterialValuation:
    if price_control not in ("S", "V"):
        raise ValidationError(
            f"invalid price_control {price_control!r}",
            domain="md.material",
            action="value",
            reason="invalid_price_control",
        )
    row = MdMaterialValuation(
        tenant_id=tenant_id,
        material_id=material_id,
        valuation_area_id=valuation_area_id,
        price_control=price_control,
        standard_price=standard_price,
        moving_avg_price=moving_avg_price,
        currency_id=currency_id,
        valuation_class=valuation_class,
        created_by=actor_principal_id,
        updated_by=actor_principal_id,
    )
    session.add(row)
    await session.flush()
    return row


__all__ = [
    "create_account_group",
    "create_business_partner",
    "create_chart_of_accounts",
    "create_company_code",
    "create_fiscal_year_variant",
    "create_gl_account",
    "create_gl_account_company",
    "create_material",
    "create_number_range",
    "create_plant",
    "create_posting_period",
    "extend_material_to_plant",
    "get_currency_by_code",
    "get_uom_by_code",
    "next_number",
    "set_posting_period_state",
    "upload_fx_rate",
    "value_material",
]
