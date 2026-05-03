"""Master Data HTTP surface.

POST/GET endpoints for the v0.1 happy-path entities. Every mutating
route gates with `enforce()` against the relevant `md.*` auth object;
the bootstrap admin holds MD_ADMIN and so passes by default.

The router is intentionally narrow: it covers every entity the v0.1
acceptance test exercises end-to-end, plus the building-block lookups
those entities need (currencies, UoMs, fiscal year variants).
Per-entity update endpoints land with v0.2 once we have a clearer
sense of which fields tenants actually mutate.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from openspine.agents.meta import (
    meta_for_business_partner,
    meta_for_company_code,
    meta_for_search_result,
)
from openspine.core.errors import AuthenticationError, NotFoundError
from openspine.identity.authz import enforce
from openspine.identity.context import PrincipalContext
from openspine.identity.middleware import get_request_session
from openspine.md import service
from openspine.md.models import (
    MdBusinessPartner,
    MdCompanyCode,
    MdCurrency,
    MdMaterial,
    MdUom,
)

router = APIRouter(prefix="/md", tags=["master-data"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(request: Request) -> PrincipalContext:
    ctx: PrincipalContext = getattr(request.state, "principal_context", None) or (
        PrincipalContext.anonymous(trace_id=uuid.uuid4())
    )
    if ctx.is_anonymous:
        raise AuthenticationError(
            "authentication required",
            domain="auth",
            action="access",
            reason="not_authenticated",
        )
    return ctx


# ---------------------------------------------------------------------------
# Lookups (display-only, no auth gate beyond authentication)
# ---------------------------------------------------------------------------


class CurrencyOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    decimals: int


@router.get("/currencies", response_model=list[CurrencyOut])
async def list_currencies(request: Request) -> list[CurrencyOut]:
    _ctx(request)
    session = get_request_session()
    rows = (await session.execute(select(MdCurrency).order_by(MdCurrency.code))).scalars().all()
    return [CurrencyOut(id=r.id, code=r.code, name=r.name, decimals=r.decimals) for r in rows]


class UomOut(BaseModel):
    id: uuid.UUID
    code: str
    description: str
    dimension: str | None


@router.get("/uoms", response_model=list[UomOut])
async def list_uoms(request: Request) -> list[UomOut]:
    _ctx(request)
    session = get_request_session()
    rows = (await session.execute(select(MdUom).order_by(MdUom.code))).scalars().all()
    return [
        UomOut(id=r.id, code=r.code, description=r.description, dimension=r.dimension) for r in rows
    ]


# ---------------------------------------------------------------------------
# Fiscal Year Variant
# ---------------------------------------------------------------------------


class FiscalYearVariantIn(BaseModel):
    code: str = Field(min_length=1, max_length=8)
    description: str | None = None
    period_count: int = 12
    special_period_count: int = 4


class FiscalYearVariantOut(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None
    period_count: int


@router.post(
    "/fiscal-year-variants",
    response_model=FiscalYearVariantOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_fiscal_year_variant_endpoint(
    payload: FiscalYearVariantIn, request: Request
) -> FiscalYearVariantOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.posting_period", action="open")
    row = await service.create_fiscal_year_variant(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]  # not anonymous
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        code=payload.code,
        description=payload.description,
        period_count=payload.period_count,
        special_period_count=payload.special_period_count,
    )
    return FiscalYearVariantOut(
        id=row.id, code=row.code, description=row.description, period_count=row.period_count
    )


# ---------------------------------------------------------------------------
# Chart of Accounts + GL
# ---------------------------------------------------------------------------


class ChartOfAccountsIn(BaseModel):
    code: str = Field(min_length=1, max_length=8)
    name: str
    language: str = "en"
    account_length: int = 8


class ChartOfAccountsOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str


@router.post(
    "/charts-of-accounts",
    response_model=ChartOfAccountsOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_coa_endpoint(payload: ChartOfAccountsIn, request: Request) -> ChartOfAccountsOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.chart_of_accounts", action="create")
    row = await service.create_chart_of_accounts(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        code=payload.code,
        name=payload.name,
        language=payload.language,
        account_length=payload.account_length,
    )
    return ChartOfAccountsOut(id=row.id, code=row.code, name=row.name)


class GlAccountIn(BaseModel):
    chart_of_accounts_id: uuid.UUID
    account_number: str
    name: str
    account_kind: str  # 'balance_sheet' | 'pnl'
    is_recon: bool = False
    recon_kind: str | None = None


class GlAccountOut(BaseModel):
    id: uuid.UUID
    account_number: str
    name: str
    account_kind: str


@router.post("/gl-accounts", response_model=GlAccountOut, status_code=status.HTTP_201_CREATED)
async def create_gl_account_endpoint(payload: GlAccountIn, request: Request) -> GlAccountOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.gl_account", action="create")
    row = await service.create_gl_account(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        chart_of_accounts_id=payload.chart_of_accounts_id,
        account_number=payload.account_number,
        name=payload.name,
        account_kind=payload.account_kind,
        is_recon=payload.is_recon,
        recon_kind=payload.recon_kind,
    )
    return GlAccountOut(
        id=row.id,
        account_number=row.account_number,
        name=row.name,
        account_kind=row.account_kind,
    )


# ---------------------------------------------------------------------------
# Company Code
# ---------------------------------------------------------------------------


class CompanyCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=10)
    name: str
    country_code: str = Field(min_length=2, max_length=2)
    local_currency_id: uuid.UUID
    chart_of_accounts_id: uuid.UUID
    fiscal_year_variant_id: uuid.UUID
    controlling_area_id: uuid.UUID | None = None


class CompanyCodeOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    country_code: str
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")

    model_config = {"populate_by_name": True}


@router.post(
    "/company-codes",
    response_model=CompanyCodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_code_endpoint(payload: CompanyCodeIn, request: Request) -> CompanyCodeOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.company_code", action="create")
    row = await service.create_company_code(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        code=payload.code,
        name=payload.name,
        country_code=payload.country_code,
        local_currency_id=payload.local_currency_id,
        chart_of_accounts_id=payload.chart_of_accounts_id,
        fiscal_year_variant_id=payload.fiscal_year_variant_id,
        controlling_area_id=payload.controlling_area_id,
    )
    return CompanyCodeOut(
        id=row.id,
        code=row.code,
        name=row.name,
        country_code=row.country_code,
        _meta=meta_for_company_code(row.id),
    )


@router.get("/company-codes", response_model=list[CompanyCodeOut])
async def list_company_codes(request: Request) -> list[CompanyCodeOut]:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.company_code", action="display")
    rows = (
        (await session.execute(select(MdCompanyCode).order_by(MdCompanyCode.code))).scalars().all()
    )
    return [
        CompanyCodeOut(
            id=r.id,
            code=r.code,
            name=r.name,
            country_code=r.country_code,
            _meta=meta_for_company_code(r.id),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Plant
# ---------------------------------------------------------------------------


class PlantIn(BaseModel):
    code: str = Field(min_length=1, max_length=10)
    name: str
    company_code_id: uuid.UUID
    factory_calendar_id: uuid.UUID | None = None


class PlantOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    company_code_id: uuid.UUID


@router.post("/plants", response_model=PlantOut, status_code=status.HTTP_201_CREATED)
async def create_plant_endpoint(payload: PlantIn, request: Request) -> PlantOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.plant", action="create")
    row = await service.create_plant(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        code=payload.code,
        name=payload.name,
        company_code_id=payload.company_code_id,
        factory_calendar_id=payload.factory_calendar_id,
    )
    return PlantOut(id=row.id, code=row.code, name=row.name, company_code_id=row.company_code_id)


# ---------------------------------------------------------------------------
# Business Partner
# ---------------------------------------------------------------------------


class BpAddressIn(BaseModel):
    kind: str = "legal"
    line1: str
    line2: str | None = None
    city: str
    region: str | None = None
    postal_code: str | None = None
    country_code: str = Field(min_length=2, max_length=2)


class BusinessPartnerIn(BaseModel):
    number: str = Field(min_length=1, max_length=20)
    kind: str = "organisation"
    name: str
    legal_name: str | None = None
    tax_number: str | None = None
    country_code: str | None = None
    industry: str | None = None
    roles: list[str] = Field(default_factory=list)
    addresses: list[BpAddressIn] = Field(default_factory=list)


class BusinessPartnerOut(BaseModel):
    id: uuid.UUID
    number: str
    kind: str
    name: str
    roles: list[str]
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")

    model_config = {"populate_by_name": True}


@router.post(
    "/business-partners",
    response_model=BusinessPartnerOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_business_partner_endpoint(
    payload: BusinessPartnerIn, request: Request
) -> BusinessPartnerOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.business_partner", action="create")
    bp = await service.create_business_partner(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        number=payload.number,
        kind=payload.kind,
        name=payload.name,
        legal_name=payload.legal_name,
        tax_number=payload.tax_number,
        country_code=payload.country_code,
        industry=payload.industry,
        roles=payload.roles,
        addresses=[a.model_dump() for a in payload.addresses],
    )
    return BusinessPartnerOut(
        id=bp.id,
        number=bp.number,
        kind=bp.kind,
        name=bp.name,
        roles=payload.roles,
        _meta=meta_for_business_partner(bp.id),
    )


@router.get("/business-partners/{bp_id}", response_model=BusinessPartnerOut)
async def get_business_partner_endpoint(bp_id: uuid.UUID, request: Request) -> BusinessPartnerOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.business_partner", action="display")
    bp = await session.get(MdBusinessPartner, bp_id)
    if bp is None or bp.tenant_id != ctx.tenant_id:
        raise NotFoundError(
            "business partner not found",
            domain="md.business_partner",
            action="display",
            reason="bp_not_found",
        )
    from openspine.md.models import MdBpRole

    roles = (
        (await session.execute(select(MdBpRole.role).where(MdBpRole.business_partner_id == bp.id)))
        .scalars()
        .all()
    )
    return BusinessPartnerOut(
        id=bp.id,
        number=bp.number,
        kind=bp.kind,
        name=bp.name,
        roles=list(roles),
        _meta=meta_for_business_partner(bp.id),
    )


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


class MaterialIn(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    description: str
    material_type: str
    industry_sector: str
    base_uom_id: uuid.UUID


class MaterialOut(BaseModel):
    id: uuid.UUID
    number: str
    description: str
    material_type: str


@router.post("/materials", response_model=MaterialOut, status_code=status.HTTP_201_CREATED)
async def create_material_endpoint(payload: MaterialIn, request: Request) -> MaterialOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.material", action="create")
    row = await service.create_material(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        number=payload.number,
        description=payload.description,
        material_type=payload.material_type,
        industry_sector=payload.industry_sector,
        base_uom_id=payload.base_uom_id,
    )
    return MaterialOut(
        id=row.id,
        number=row.number,
        description=row.description,
        material_type=row.material_type,
    )


class MaterialPlantIn(BaseModel):
    material_id: uuid.UUID
    plant_id: uuid.UUID
    procurement_type: str | None = None
    mrp_type: str | None = None


class MaterialValuationIn(BaseModel):
    material_id: uuid.UUID
    valuation_area_id: uuid.UUID
    price_control: str  # 'S' or 'V'
    currency_id: uuid.UUID
    standard_price: Decimal | None = None
    moving_avg_price: Decimal | None = None
    valuation_class: str | None = None


@router.post("/material-plants", status_code=status.HTTP_201_CREATED)
async def extend_material_to_plant_endpoint(
    payload: MaterialPlantIn, request: Request
) -> dict[str, Any]:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.material", action="change")
    row = await service.extend_material_to_plant(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        material_id=payload.material_id,
        plant_id=payload.plant_id,
        procurement_type=payload.procurement_type,
        mrp_type=payload.mrp_type,
    )
    return {"id": str(row.id)}


@router.post("/material-valuations", status_code=status.HTTP_201_CREATED)
async def value_material_endpoint(payload: MaterialValuationIn, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.material", action="change")
    row = await service.value_material(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        material_id=payload.material_id,
        valuation_area_id=payload.valuation_area_id,
        price_control=payload.price_control,
        currency_id=payload.currency_id,
        standard_price=payload.standard_price,
        moving_avg_price=payload.moving_avg_price,
        valuation_class=payload.valuation_class,
    )
    return {"id": str(row.id)}


# ---------------------------------------------------------------------------
# FX rates
# ---------------------------------------------------------------------------


class FxRateIn(BaseModel):
    rate_type: str  # 'M' | 'B' | 'G'
    from_currency_id: uuid.UUID
    to_currency_id: uuid.UUID
    valid_from: date
    rate: Decimal


@router.post("/fx-rates", status_code=status.HTTP_201_CREATED)
async def upload_fx_rate_endpoint(payload: FxRateIn, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.fx_rate", action="upload")
    row = await service.upload_fx_rate(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        rate_type=payload.rate_type,
        from_currency_id=payload.from_currency_id,
        to_currency_id=payload.to_currency_id,
        valid_from=payload.valid_from,
        rate=payload.rate,
    )
    return {"id": str(row.id), "rate": str(row.rate)}


# ---------------------------------------------------------------------------
# Posting periods
# ---------------------------------------------------------------------------


class PostingPeriodIn(BaseModel):
    company_code_id: uuid.UUID
    fiscal_year: int
    period: int
    period_start_date: date
    period_end_date: date
    state: str = "closed"


class PostingPeriodOut(BaseModel):
    id: uuid.UUID
    fiscal_year: int
    period: int
    state: str


@router.post(
    "/posting-periods",
    response_model=PostingPeriodOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_posting_period_endpoint(
    payload: PostingPeriodIn, request: Request
) -> PostingPeriodOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="md.posting_period", action="open")
    row = await service.create_posting_period(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        company_code_id=payload.company_code_id,
        fiscal_year=payload.fiscal_year,
        period=payload.period,
        period_start_date=payload.period_start_date,
        period_end_date=payload.period_end_date,
        state=payload.state,
    )
    return PostingPeriodOut(
        id=row.id, fiscal_year=row.fiscal_year, period=row.period, state=row.state
    )


class PostingPeriodStateIn(BaseModel):
    state: str  # 'open' | 'closed' | 'special'


@router.post(
    "/company-codes/{company_code_id}/posting-periods/{fiscal_year}/{period}/state",
    response_model=PostingPeriodOut,
)
async def set_posting_period_state_endpoint(
    company_code_id: uuid.UUID,
    fiscal_year: int,
    period: int,
    payload: PostingPeriodStateIn,
    request: Request,
) -> PostingPeriodOut:
    ctx = _ctx(request)
    session = get_request_session()
    action = "open" if payload.state == "open" else "close"
    await enforce(session, ctx=ctx, domain="md.posting_period", action=action)
    row = await service.set_posting_period_state(
        session,
        tenant_id=ctx.tenant_id,  # type: ignore[arg-type]
        actor_principal_id=ctx.principal_id,  # type: ignore[arg-type]
        company_code_id=company_code_id,
        fiscal_year=fiscal_year,
        period=period,
        state=payload.state,
    )
    return PostingPeriodOut(
        id=row.id, fiscal_year=row.fiscal_year, period=row.period, state=row.state
    )


# ---------------------------------------------------------------------------
# Hybrid search — semantic candidates + structured verification
# ---------------------------------------------------------------------------


class SearchHit(BaseModel):
    """A single hit. `id` is the Postgres row's UUID; `score` is the
    semantic distance (None when the hit came from the structured
    fallback). `source` distinguishes — agents read this to know
    whether to weight the ranking or treat it as exact."""

    id: uuid.UUID
    entity: str
    number: str | None = None
    name: str
    description: str | None = None
    score: float | None = None
    source: str  # 'semantic' | 'structured'


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")

    model_config = {"populate_by_name": True}


_SEARCHABLE_ENTITIES = ("business_partner", "material")


@router.get("/search", response_model=SearchResponse)
async def hybrid_search(
    request: Request, q: str, entity: str = "business_partner", limit: int = 10
) -> SearchResponse:
    """Hybrid search per ARCHITECTURE.md §7.

    Semantic candidates from Qdrant (when available) → structured
    verification against Postgres → return both rankings + the
    verifying rows.

    v0.1 simplification: Qdrant has no vectors yet (the embedding
    worker writes them when entities change post-§4.5; v0.2 work
    expands this). Until vectors exist, the endpoint falls back to
    Postgres ILIKE — same response shape, `source='structured'`,
    `score=None`. The contract is what matters; agents see one shape
    and don't have to special-case the empty-index startup state.
    """
    ctx = _ctx(request)
    if entity not in _SEARCHABLE_ENTITIES:
        raise NotFoundError(
            f"unsupported entity {entity!r}",
            domain="md.search",
            action="display",
            reason="unknown_entity",
            allowed={"entities": list(_SEARCHABLE_ENTITIES)},
        )
    session = get_request_session()
    # Authority gate: display permission on the entity.
    await enforce(session, ctx=ctx, domain=f"md.{entity}", action="display")

    # In v0.1, the structured fallback is the only path that returns
    # rows (Qdrant has nothing to index yet). When the embedding
    # worker starts populating vectors, the Qdrant lookup goes here
    # and the fallback runs only on miss.
    pattern = f"%{q}%"
    if entity == "business_partner":
        rows_bp = (
            (
                await session.execute(
                    select(MdBusinessPartner)
                    .where(MdBusinessPartner.name.ilike(pattern))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        hits = [
            SearchHit(
                id=r.id,
                entity="business_partner",
                number=r.number,
                name=r.name,
                source="structured",
            )
            for r in rows_bp
        ]
    else:  # material
        rows_mat = (
            (
                await session.execute(
                    select(MdMaterial).where(MdMaterial.description.ilike(pattern)).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        hits = [
            SearchHit(
                id=r.id,
                entity="material",
                number=r.number,
                name=r.description,
                description=r.description,
                source="structured",
            )
            for r in rows_mat
        ]

    return SearchResponse(
        hits=hits,
        _meta=meta_for_search_result(query=q, entity=entity, source="structured", total=len(hits)),
    )


__all__ = ["router"]
