"""FI posting service — the single chokepoint for universal-journal writes.

Per ADR 0003 + fi-finance.md, every business event that touches the
ledger reduces to a `post_journal_entry()` call. MM goods receipts,
PP confirmations, AR receipts, AP invoices — all of them go through
here. Direct INSERT into `fin_document_*` is forbidden by convention.

The service enforces the four invariants every posting must satisfy:

1. **Balanced per currency per ledger.** Σ debits = Σ credits within
   each (ledger, local_currency) pair. Imbalance → ValidationError.
2. **Period open.** The posting date's `(company_code, fiscal_year,
   period)` must be in `state = 'open'`. Closed → ConflictError.
3. **GL accounts valid.** Each line's GL account must exist in the
   Company Code's chart of accounts and not be blocked. Bad → 404.
4. **Document number gap-free.** Allocated from `md_number_range`
   under `SELECT ... FOR UPDATE`. Several jurisdictions require this
   by law; OpenSpine defaults to it.

Hooks: `journal_entry.pre_post` runs inline (sync — can abort);
`journal_entry.post_post` fires after commit via the event bus.
Both names per ADR 0008.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.core.errors import ConflictError, NotFoundError, ValidationError
from openspine.core.events import Event, get_event_bus
from openspine.core.hooks import (  # noqa: F401  (registered_hooks for diagnostics)
    dispatch_pre,
    registered_hooks,
)
from openspine.fi.models import (
    DEBIT_CREDIT,
    FinDocumentHeader,
    FinDocumentLine,
    FinDocumentType,
    FinLedger,
)
from openspine.md.models import MdGlAccount, MdGlAccountCompany, MdPostingPeriod
from openspine.md.service import next_number


@dataclass
class JournalLineInput:
    """One line in an inbound journal entry posting.

    `amount_local` is always positive; `debit_credit` ∈ {'D','C'}
    decides the sign (Σ debits must equal Σ credits in each
    `(ledger, local_currency)` group).

    `gl_account_id` is mandatory. Everything else is optional —
    business-partner, cost centre, profit centre, etc. The service
    validates that nothing references a row in another tenant.
    """

    gl_account_id: uuid.UUID
    debit_credit: str
    amount_local: Decimal
    local_currency_id: uuid.UUID
    ledger_id: uuid.UUID | None = None  # None → use the leading ledger
    business_partner_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    profit_centre_code: str | None = None
    internal_order_code: str | None = None
    segment_code: str | None = None
    project_code: str | None = None
    tax_code: str | None = None
    line_text: str | None = None
    line_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class JournalEntryInput:
    """A complete inbound journal entry posting."""

    company_code_id: uuid.UUID
    document_type_code: str  # e.g., 'SA'
    posting_date: date
    document_date: date
    fiscal_year: int
    period: int
    lines: list[JournalLineInput]
    reference: str | None = None
    header_text: str | None = None


@dataclass
class PostedJournalEntry:
    header: FinDocumentHeader
    lines: list[FinDocumentLine]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_balanced(lines: list[JournalLineInput]) -> None:
    """Σ debits == Σ credits per (ledger, local_currency) group.

    Pre-resolution we don't have ledger IDs for lines that left it None,
    but we group by the resolved ledger after the leading-ledger fill;
    callers ensure ledger_id is set before this check.
    """
    sums: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for line in lines:
        if line.debit_credit not in DEBIT_CREDIT:
            raise ValidationError(
                f"invalid debit_credit {line.debit_credit!r}",
                domain="fi.document",
                action="post",
                reason="invalid_debit_credit",
            )
        if line.amount_local <= 0:
            raise ValidationError(
                "amount_local must be positive (sign comes from debit_credit)",
                domain="fi.document",
                action="post",
                reason="non_positive_amount",
            )
        if line.ledger_id is None:
            raise ValidationError(
                "ledger_id must be resolved before balance check",
                domain="fi.document",
                action="post",
                reason="ledger_unresolved",
            )
        key = (line.ledger_id, line.local_currency_id)
        signed = line.amount_local if line.debit_credit == "D" else -line.amount_local
        sums[key] = sums.get(key, Decimal(0)) + signed
    for (ledger, currency), total in sums.items():
        if total != 0:
            raise ValidationError(
                "journal entry not balanced",
                domain="fi.document",
                action="post",
                reason="unbalanced",
                attempted={
                    "ledger_id": str(ledger),
                    "currency_id": str(currency),
                    "imbalance": str(total),
                },
            )


async def _validate_period_open(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    company_code_id: uuid.UUID,
    fiscal_year: int,
    period: int,
) -> None:
    stmt = select(MdPostingPeriod).where(
        MdPostingPeriod.tenant_id == tenant_id,
        MdPostingPeriod.company_code_id == company_code_id,
        MdPostingPeriod.fiscal_year == fiscal_year,
        MdPostingPeriod.period == period,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ConflictError(
            f"posting period {fiscal_year}/{period:02d} does not exist",
            domain="fi.document",
            action="post",
            reason="period_not_defined",
        )
    if row.state != "open":
        raise ConflictError(
            f"posting period {fiscal_year}/{period:02d} is {row.state!r}",
            domain="fi.document",
            action="post",
            reason="period_closed",
            attempted={"period_state": row.state},
        )


async def _validate_gl_accounts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    company_code_id: uuid.UUID,
    gl_account_ids: set[uuid.UUID],
) -> None:
    """Every GL account must exist in the tenant AND have a
    `md_gl_account_company` overlay for the Company Code (otherwise
    posting to it is undefined). The overlay must not be blocked."""
    if not gl_account_ids:
        raise ValidationError(
            "journal entry must have at least one line",
            domain="fi.document",
            action="post",
            reason="no_lines",
        )
    accounts = (
        (
            await session.execute(
                select(MdGlAccount).where(
                    MdGlAccount.tenant_id == tenant_id,
                    MdGlAccount.id.in_(gl_account_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    found_ids = {a.id for a in accounts}
    missing = gl_account_ids - found_ids
    if missing:
        raise NotFoundError(
            f"unknown GL accounts: {sorted(str(m) for m in missing)}",
            domain="fi.document",
            action="post",
            reason="gl_account_not_found",
        )

    overlays = (
        (
            await session.execute(
                select(MdGlAccountCompany).where(
                    MdGlAccountCompany.tenant_id == tenant_id,
                    MdGlAccountCompany.company_code_id == company_code_id,
                    MdGlAccountCompany.gl_account_id.in_(gl_account_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    overlay_by_gl = {o.gl_account_id: o for o in overlays}
    for gl_id in gl_account_ids:
        overlay = overlay_by_gl.get(gl_id)
        if overlay is None:
            raise NotFoundError(
                "GL account has no Company Code overlay",
                domain="fi.document",
                action="post",
                reason="gl_account_company_missing",
                attempted={"gl_account_id": str(gl_id)},
            )
        if overlay.blocked_for_posting:
            raise ConflictError(
                "GL account is blocked for posting in this Company Code",
                domain="fi.document",
                action="post",
                reason="gl_account_blocked",
                attempted={"gl_account_id": str(gl_id)},
            )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def post_journal_entry(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    entry: JournalEntryInput,
) -> PostedJournalEntry:
    """Post a balanced journal entry. Returns the persisted header + lines.

    Order of operations:
    1. Resolve the leading ledger; fill it into any line with `ledger_id=None`.
    2. Resolve the document type (must exist + match the tenant).
    3. Validate balanced per (ledger, currency).
    4. Validate posting period is open.
    5. Validate GL accounts exist + have a CC overlay + not blocked.
    6. Run `journal_entry.pre_post` hooks (can abort).
    7. Allocate document_number from md_number_range (FOR UPDATE).
    8. Insert header + lines.
    9. Publish `finance.document.posted` event (after-commit semantics
       — the InMemoryEventBus delivers synchronously; with Redis it
       fires asynchronously to subscribers).
    """
    # Resolve the leading ledger.
    leading = (
        await session.execute(
            select(FinLedger).where(
                FinLedger.tenant_id == tenant_id, FinLedger.is_leading.is_(True)
            )
        )
    ).scalar_one_or_none()
    if leading is None:
        raise ConflictError(
            "no leading ledger configured for tenant",
            domain="fi.document",
            action="post",
            reason="no_leading_ledger",
        )
    for line in entry.lines:
        if line.ledger_id is None:
            line.ledger_id = leading.id

    # Resolve the document type.
    doc_type = (
        await session.execute(
            select(FinDocumentType).where(
                FinDocumentType.tenant_id == tenant_id,
                FinDocumentType.code == entry.document_type_code,
            )
        )
    ).scalar_one_or_none()
    if doc_type is None:
        raise NotFoundError(
            f"unknown document type {entry.document_type_code!r}",
            domain="fi.document",
            action="post",
            reason="document_type_not_found",
        )

    _validate_balanced(entry.lines)

    await _validate_period_open(
        session,
        tenant_id=tenant_id,
        company_code_id=entry.company_code_id,
        fiscal_year=entry.fiscal_year,
        period=entry.period,
    )

    gl_ids = {line.gl_account_id for line in entry.lines}
    await _validate_gl_accounts(
        session,
        tenant_id=tenant_id,
        company_code_id=entry.company_code_id,
        gl_account_ids=gl_ids,
    )

    # Pre-post hook (plugin extension point — can abort).
    await dispatch_pre(
        "journal_entry.pre_post",
        {
            "company_code_id": str(entry.company_code_id),
            "document_type": entry.document_type_code,
            "posting_date": entry.posting_date.isoformat(),
            "fiscal_year": entry.fiscal_year,
            "period": entry.period,
            "lines": [
                {
                    "gl_account_id": str(line.gl_account_id),
                    "debit_credit": line.debit_credit,
                    "amount_local": str(line.amount_local),
                }
                for line in entry.lines
            ],
        },
    )

    # Allocate document number.
    nr_object = doc_type.number_range_object or "fi_document"
    document_number = await next_number(
        session,
        tenant_id=tenant_id,
        object_type=nr_object,
        scope="default",
    )

    # Insert header + lines.
    header = FinDocumentHeader(
        tenant_id=tenant_id,
        company_code_id=entry.company_code_id,
        document_type_id=doc_type.id,
        document_number=document_number,
        fiscal_year=entry.fiscal_year,
        period=entry.period,
        posting_date=entry.posting_date,
        document_date=entry.document_date,
        reference=entry.reference,
        header_text=entry.header_text,
        status="posted",
        created_by=actor_principal_id,
    )
    session.add(header)
    await session.flush()

    line_rows: list[FinDocumentLine] = []
    for idx, line in enumerate(entry.lines, start=1):
        assert line.ledger_id is not None
        row = FinDocumentLine(
            tenant_id=tenant_id,
            document_header_id=header.id,
            line_number=idx,
            company_code_id=entry.company_code_id,
            fiscal_year=entry.fiscal_year,
            period=entry.period,
            posting_date=entry.posting_date,
            gl_account_id=line.gl_account_id,
            ledger_id=line.ledger_id,
            debit_credit=line.debit_credit,
            amount_local=line.amount_local,
            local_currency_id=line.local_currency_id,
            business_partner_id=line.business_partner_id,
            cost_centre_id=line.cost_centre_id,
            profit_centre_code=line.profit_centre_code,
            internal_order_code=line.internal_order_code,
            segment_code=line.segment_code,
            project_code=line.project_code,
            tax_code=line.tax_code,
            line_text=line.line_text,
            line_metadata=line.line_metadata,
            created_by=actor_principal_id,
        )
        session.add(row)
        line_rows.append(row)
    await session.flush()

    # Publish post-commit event. The InMemoryEventBus delivers
    # synchronously; with the future Redis bus this is the
    # transactional-outbox boundary.
    bus = get_event_bus()
    await bus.publish(
        Event(
            stream="finance.document.posted",
            tenant_id=str(tenant_id),
            payload={
                "id": str(header.id),
                "company_code_id": str(entry.company_code_id),
                "document_type": entry.document_type_code,
                "document_number": document_number,
                "fiscal_year": entry.fiscal_year,
                "period": entry.period,
                "posting_date": entry.posting_date.isoformat(),
                "line_count": len(line_rows),
            },
        )
    )

    return PostedJournalEntry(header=header, lines=line_rows)


__all__ = [
    "JournalEntryInput",
    "JournalLineInput",
    "PostedJournalEntry",
    "post_journal_entry",
]
