"""FI HTTP surface — `/fi/*`.

POST /fi/journal-entries — direct universal-journal posting. The
v0.2 cut covers the GL posting path (document type `SA`); AP/AR
specifics arrive when the open-item + clearing tables land.

Authority gate: `fi.document:post`. The amount_range qualifier on
the auth object will eventually compare the entry's total debit
against the principal's per-posting limit; v0.2 ships the gate
without the FX-converted amount check (lands when the FX rate
service plugs into the evaluator).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from openspine.agents.meta import build_meta_block
from openspine.core.errors import AuthenticationError
from openspine.fi.service import (
    JournalEntryInput,
    JournalLineInput,
    ReverseRequest,
    post_journal_entry,
    reverse_journal_entry,
)
from openspine.identity.authz import enforce
from openspine.identity.context import PrincipalContext
from openspine.identity.middleware import get_request_session

router = APIRouter(prefix="/fi", tags=["finance"])


class JournalLineIn(BaseModel):
    gl_account_id: uuid.UUID
    debit_credit: str = Field(description="'D' or 'C'")
    amount_local: Decimal
    local_currency_id: uuid.UUID
    ledger_id: uuid.UUID | None = None
    business_partner_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    profit_centre_code: str | None = None
    internal_order_code: str | None = None
    segment_code: str | None = None
    project_code: str | None = None
    tax_code: str | None = None
    line_text: str | None = None
    line_metadata: dict[str, Any] = Field(default_factory=dict)


class JournalEntryIn(BaseModel):
    company_code_id: uuid.UUID
    document_type_code: str = "SA"
    posting_date: date
    document_date: date
    fiscal_year: int
    period: int
    lines: list[JournalLineIn] = Field(min_length=2)
    reference: str | None = None
    header_text: str | None = None


class JournalLineOut(BaseModel):
    id: uuid.UUID
    line_number: int
    gl_account_id: uuid.UUID
    debit_credit: str
    amount_local: Decimal


class JournalEntryOut(BaseModel):
    id: uuid.UUID
    document_number: int
    document_type: str
    company_code_id: uuid.UUID
    fiscal_year: int
    period: int
    posting_date: date
    line_count: int
    lines: list[JournalLineOut]
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")

    model_config = {"populate_by_name": True}


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


@router.post(
    "/journal-entries",
    response_model=JournalEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_journal_entry_endpoint(payload: JournalEntryIn, request: Request) -> JournalEntryOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="fi.document", action="post")
    assert ctx.tenant_id is not None
    assert ctx.principal_id is not None
    result = await post_journal_entry(
        session,
        tenant_id=ctx.tenant_id,
        actor_principal_id=ctx.principal_id,
        entry=JournalEntryInput(
            company_code_id=payload.company_code_id,
            document_type_code=payload.document_type_code,
            posting_date=payload.posting_date,
            document_date=payload.document_date,
            fiscal_year=payload.fiscal_year,
            period=payload.period,
            reference=payload.reference,
            header_text=payload.header_text,
            lines=[
                JournalLineInput(
                    gl_account_id=line.gl_account_id,
                    debit_credit=line.debit_credit,
                    amount_local=line.amount_local,
                    local_currency_id=line.local_currency_id,
                    ledger_id=line.ledger_id,
                    business_partner_id=line.business_partner_id,
                    cost_centre_id=line.cost_centre_id,
                    profit_centre_code=line.profit_centre_code,
                    internal_order_code=line.internal_order_code,
                    segment_code=line.segment_code,
                    project_code=line.project_code,
                    tax_code=line.tax_code,
                    line_text=line.line_text,
                    line_metadata=line.line_metadata,
                )
                for line in payload.lines
            ],
        ),
    )
    return JournalEntryOut(
        id=result.header.id,
        document_number=result.header.document_number,
        document_type=payload.document_type_code,
        company_code_id=result.header.company_code_id,
        fiscal_year=result.header.fiscal_year,
        period=result.header.period,
        posting_date=result.header.posting_date,
        line_count=len(result.lines),
        lines=[
            JournalLineOut(
                id=line.id,
                line_number=line.line_number,
                gl_account_id=line.gl_account_id,
                debit_credit=line.debit_credit,
                amount_local=line.amount_local,
            )
            for line in result.lines
        ],
        _meta=build_meta_block(
            self_href=f"/fi/journal-entries/{result.header.id}",
            actions=[
                {
                    "name": "reverse",
                    "method": "POST",
                    "href": f"/fi/journal-entries/{result.header.id}/reverse",
                    "requires": [["fi.document", "reverse"]],
                    "available_in": "v0.2.x",
                },
            ],
            extra={"document_number": result.header.document_number},
        ),
    )


# ---------------------------------------------------------------------------
# Reverse
# ---------------------------------------------------------------------------


class ReverseRequestIn(BaseModel):
    posting_date: date
    fiscal_year: int
    period: int
    reason: str | None = None


@router.post(
    "/journal-entries/{document_id}/reverse",
    response_model=JournalEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def reverse_journal_entry_endpoint(
    document_id: uuid.UUID, payload: ReverseRequestIn, request: Request
) -> JournalEntryOut:
    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="fi.document", action="reverse")
    assert ctx.tenant_id is not None
    assert ctx.principal_id is not None
    posted = await reverse_journal_entry(
        session,
        tenant_id=ctx.tenant_id,
        actor_principal_id=ctx.principal_id,
        original_id=document_id,
        request=ReverseRequest(
            posting_date=payload.posting_date,
            fiscal_year=payload.fiscal_year,
            period=payload.period,
            reason=payload.reason,
        ),
    )
    return JournalEntryOut(
        id=posted.header.id,
        document_number=posted.header.document_number,
        document_type="AB",
        company_code_id=posted.header.company_code_id,
        fiscal_year=posted.header.fiscal_year,
        period=posted.header.period,
        posting_date=posted.header.posting_date,
        line_count=len(posted.lines),
        lines=[
            JournalLineOut(
                id=line.id,
                line_number=line.line_number,
                gl_account_id=line.gl_account_id,
                debit_credit=line.debit_credit,
                amount_local=line.amount_local,
            )
            for line in posted.lines
        ],
        _meta=build_meta_block(
            self_href=f"/fi/journal-entries/{posted.header.id}",
            related={
                "reversal_of": f"/fi/journal-entries/{posted.header.reversal_of_id}",
            },
            extra={
                "document_number": posted.header.document_number,
                "is_reversal": True,
            },
        ),
    )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _meta_for_document(header: Any) -> dict[str, Any]:
    related: dict[str, str] = {
        "lines": f"/fi/journal-entries/{header.id}/lines",
    }
    if header.reversal_of_id:
        related["reversal_of"] = f"/fi/journal-entries/{header.reversal_of_id}"
    if header.reversed_by_id:
        related["reversed_by"] = f"/fi/journal-entries/{header.reversed_by_id}"
    actions: list[dict[str, Any]] = []
    if header.status == "posted":
        actions.append(
            {
                "name": "reverse",
                "method": "POST",
                "href": f"/fi/journal-entries/{header.id}/reverse",
                "requires": [["fi.document", "reverse"]],
            }
        )
    return build_meta_block(
        self_href=f"/fi/journal-entries/{header.id}",
        related=related,
        actions=actions,
        extra={
            "document_number": header.document_number,
            "status": header.status,
        },
    )


@router.get("/journal-entries/{document_id}", response_model=JournalEntryOut)
async def get_journal_entry(document_id: uuid.UUID, request: Request) -> JournalEntryOut:
    from sqlalchemy import select

    from openspine.core.errors import NotFoundError
    from openspine.fi.models import FinDocumentHeader, FinDocumentLine, FinDocumentType

    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="fi.document", action="display")

    header = await session.get(FinDocumentHeader, document_id)
    if header is None or header.tenant_id != ctx.tenant_id:
        raise NotFoundError(
            "document not found",
            domain="fi.document",
            action="display",
            reason="document_not_in_tenant",
        )
    doc_type = await session.get(FinDocumentType, header.document_type_id)
    lines = (
        (
            await session.execute(
                select(FinDocumentLine)
                .where(FinDocumentLine.document_header_id == header.id)
                .order_by(FinDocumentLine.line_number)
            )
        )
        .scalars()
        .all()
    )
    return JournalEntryOut(
        id=header.id,
        document_number=header.document_number,
        document_type=doc_type.code if doc_type else "?",
        company_code_id=header.company_code_id,
        fiscal_year=header.fiscal_year,
        period=header.period,
        posting_date=header.posting_date,
        line_count=len(lines),
        lines=[
            JournalLineOut(
                id=line.id,
                line_number=line.line_number,
                gl_account_id=line.gl_account_id,
                debit_credit=line.debit_credit,
                amount_local=line.amount_local,
            )
            for line in lines
        ],
        _meta=_meta_for_document(header),
    )


class JournalEntrySummaryOut(BaseModel):
    id: uuid.UUID
    document_number: int
    document_type: str
    fiscal_year: int
    period: int
    posting_date: date
    status: str
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")

    model_config = {"populate_by_name": True}


class JournalEntryListOut(BaseModel):
    items: list[JournalEntrySummaryOut]
    total: int
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")

    model_config = {"populate_by_name": True}


@router.get("/journal-entries", response_model=JournalEntryListOut)
async def list_journal_entries(
    request: Request,
    company_code_id: uuid.UUID,
    fiscal_year: int,
    period: int,
    limit: int = 100,
) -> JournalEntryListOut:
    from sqlalchemy import select

    from openspine.fi.models import FinDocumentHeader, FinDocumentType

    ctx = _ctx(request)
    session = get_request_session()
    await enforce(session, ctx=ctx, domain="fi.document", action="display")

    stmt = (
        select(FinDocumentHeader)
        .where(
            FinDocumentHeader.company_code_id == company_code_id,
            FinDocumentHeader.fiscal_year == fiscal_year,
            FinDocumentHeader.period == period,
        )
        .order_by(FinDocumentHeader.document_number)
        .limit(limit)
    )
    headers = (await session.execute(stmt)).scalars().all()

    # Resolve document types in one query.
    type_ids = {h.document_type_id for h in headers}
    types = (
        (await session.execute(select(FinDocumentType).where(FinDocumentType.id.in_(type_ids))))
        .scalars()
        .all()
        if type_ids
        else []
    )
    type_code_by_id = {t.id: t.code for t in types}

    items = [
        JournalEntrySummaryOut(
            id=h.id,
            document_number=h.document_number,
            document_type=type_code_by_id.get(h.document_type_id, "?"),
            fiscal_year=h.fiscal_year,
            period=h.period,
            posting_date=h.posting_date,
            status=h.status,
            _meta=_meta_for_document(h),
        )
        for h in headers
    ]
    return JournalEntryListOut(
        items=items,
        total=len(items),
        _meta=build_meta_block(
            self_href=(
                f"/fi/journal-entries?company_code_id={company_code_id}"
                f"&fiscal_year={fiscal_year}&period={period}"
            ),
            extra={"limit": limit, "fiscal_year": fiscal_year, "period": period},
        ),
    )


__all__ = ["router"]
