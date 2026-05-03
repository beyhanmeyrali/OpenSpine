"""AP invoice posting — wraps `post_journal_entry` for the vendor side.

An AP invoice is structurally a journal entry that:
- Uses document type `KR` (vendor invoice).
- Posts a credit to a vendor reconciliation account (the BP becomes
  an open item).
- Posts the debit lines to expense / asset GL accounts (the cost side).
- Carries `business_partner_id` on the recon line so the open-item
  view can find it.

Validations:
- The vendor BP exists in the tenant + holds the `vendor` role.
- The recon GL account exists in the tenant + has `is_recon=TRUE` +
  `recon_kind='vendor'`.
- The recon account has a Company Code overlay (the standard GL
  posting prerequisite).
- All lines balance per (ledger, currency) — enforced by
  `post_journal_entry`.

v0.2 simplification: the request passes `vendor_recon_account_id`
explicitly. Real ERPs derive this from a per-Company-Code BP overlay
table; that lands in v0.2.x alongside the BP-CC config table.
Tax derivation, payment-term handling, and currency conversion are
also v0.2.x.
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
from openspine.fi.service import (
    JournalEntryInput,
    JournalLineInput,
    PostedJournalEntry,
    post_journal_entry,
)
from openspine.md.models import MdBpRole, MdBusinessPartner, MdGlAccount


@dataclass
class APExpenseLine:
    """One debit line on the cost side of an AP invoice."""

    gl_account_id: uuid.UUID
    amount_local: Decimal
    cost_centre_id: uuid.UUID | None = None
    profit_centre_code: str | None = None
    internal_order_code: str | None = None
    line_text: str | None = None


@dataclass
class APInvoiceInput:
    """Inbound AP invoice payload."""

    company_code_id: uuid.UUID
    vendor_business_partner_id: uuid.UUID
    vendor_recon_account_id: uuid.UUID
    invoice_date: date
    posting_date: date
    fiscal_year: int
    period: int
    local_currency_id: uuid.UUID
    expense_lines: list[APExpenseLine]
    reference: str | None = None
    header_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


async def _validate_vendor_bp(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    business_partner_id: uuid.UUID,
) -> MdBusinessPartner:
    bp = await session.get(MdBusinessPartner, business_partner_id)
    if bp is None or bp.tenant_id != tenant_id:
        raise NotFoundError(
            "business partner not in tenant",
            domain="fi.ap_invoice",
            action="post",
            reason="bp_not_in_tenant",
        )
    if bp.blocked:
        raise ConflictError(
            "business partner is blocked",
            domain="fi.ap_invoice",
            action="post",
            reason="bp_blocked",
        )
    has_vendor_role = (
        await session.execute(
            select(MdBpRole).where(
                MdBpRole.tenant_id == tenant_id,
                MdBpRole.business_partner_id == bp.id,
                MdBpRole.role == "vendor",
            )
        )
    ).scalar_one_or_none()
    if has_vendor_role is None:
        raise ValidationError(
            "business partner does not hold the 'vendor' role",
            domain="fi.ap_invoice",
            action="post",
            reason="bp_not_vendor",
            attempted={"bp_id": str(bp.id), "required_role": "vendor"},
        )
    return bp


async def _validate_vendor_recon_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    recon_account_id: uuid.UUID,
) -> MdGlAccount:
    gl = await session.get(MdGlAccount, recon_account_id)
    if gl is None or gl.tenant_id != tenant_id:
        raise NotFoundError(
            "recon account not in tenant",
            domain="fi.ap_invoice",
            action="post",
            reason="recon_account_not_in_tenant",
        )
    if not gl.is_recon:
        raise ValidationError(
            "GL account is not a reconciliation account",
            domain="fi.ap_invoice",
            action="post",
            reason="not_a_recon_account",
            attempted={"gl_account_id": str(gl.id)},
        )
    if gl.recon_kind != "vendor":
        raise ValidationError(
            f"recon account is for {gl.recon_kind!r}, not 'vendor'",
            domain="fi.ap_invoice",
            action="post",
            reason="wrong_recon_kind",
            attempted={"recon_kind": gl.recon_kind},
            allowed={"recon_kind": "vendor"},
        )
    return gl


async def post_ap_invoice(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
    invoice: APInvoiceInput,
) -> PostedJournalEntry:
    """Post an AP invoice. Returns the persisted document.

    Builds a journal entry with:
    - one credit line to the vendor recon account (carries
      `business_partner_id` so the open-item view picks it up)
    - one debit line per expense item

    The total credit equals the sum of expense debits — `post_journal_entry`
    will reject anything else.
    """
    if not invoice.expense_lines:
        raise ValidationError(
            "AP invoice needs at least one expense line",
            domain="fi.ap_invoice",
            action="post",
            reason="no_expense_lines",
        )
    bp = await _validate_vendor_bp(
        session,
        tenant_id=tenant_id,
        business_partner_id=invoice.vendor_business_partner_id,
    )
    await _validate_vendor_recon_account(
        session,
        tenant_id=tenant_id,
        recon_account_id=invoice.vendor_recon_account_id,
    )

    total = sum((line.amount_local for line in invoice.expense_lines), Decimal(0))

    lines: list[JournalLineInput] = [
        JournalLineInput(
            gl_account_id=line.gl_account_id,
            debit_credit="D",
            amount_local=line.amount_local,
            local_currency_id=invoice.local_currency_id,
            cost_centre_id=line.cost_centre_id,
            profit_centre_code=line.profit_centre_code,
            internal_order_code=line.internal_order_code,
            line_text=line.line_text or f"AP invoice — {bp.name}",
        )
        for line in invoice.expense_lines
    ]
    lines.append(
        JournalLineInput(
            gl_account_id=invoice.vendor_recon_account_id,
            debit_credit="C",
            amount_local=total,
            local_currency_id=invoice.local_currency_id,
            business_partner_id=bp.id,
            line_text=invoice.reference or f"Vendor invoice — {bp.name}",
            line_metadata={"ap_invoice": True, **invoice.metadata},
        )
    )

    return await post_journal_entry(
        session,
        tenant_id=tenant_id,
        actor_principal_id=actor_principal_id,
        entry=JournalEntryInput(
            company_code_id=invoice.company_code_id,
            document_type_code="KR",
            posting_date=invoice.posting_date,
            document_date=invoice.invoice_date,
            fiscal_year=invoice.fiscal_year,
            period=invoice.period,
            reference=invoice.reference,
            header_text=invoice.header_text or f"AP invoice — {bp.name}",
            lines=lines,
        ),
    )


__all__ = ["APExpenseLine", "APInvoiceInput", "post_ap_invoice"]
