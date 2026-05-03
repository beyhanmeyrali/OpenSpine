"""Open-item view — derived from `fin_document_line` at read time.

Per `fi-finance.md` §4, `fin_open_item` is "a materialised view over
open AP/AR items from `fin_document_line`". v0.2 ships the read-side
derivation; the materialised table + clearing pipeline arrive when
`fin_clearing` lands (v0.2.x).

A line is an **open item** when:
1. Its GL account is a reconciliation account (`md_gl_account.is_recon`).
2. The document hasn't been reversed (`fin_document_header.status =
   'posted'`).
3. (Future) The line isn't covered by a `fin_clearing` row.

Per ADR 0003 the universal journal carries `business_partner_id`
on the line itself, so AP/AR open items aren't a separate
sub-ledger — they're a derived view over the journal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.fi.models import FinDocumentHeader, FinDocumentLine
from openspine.md.models import MdBpRole, MdBusinessPartner, MdGlAccount


@dataclass(frozen=True)
class OpenItem:
    """One open AP/AR item (a single line on a recon account)."""

    line_id: uuid.UUID
    document_header_id: uuid.UUID
    document_number: int
    business_partner_id: uuid.UUID
    business_partner_name: str
    gl_account_id: uuid.UUID
    gl_account_number: str
    debit_credit: str  # 'D' for AR-style; 'C' for AP-style
    amount_local: Decimal
    posting_date: date
    line_text: str | None
    recon_kind: str | None  # 'vendor' | 'customer' | 'asset'


async def list_open_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    role: str | None = None,  # 'vendor' | 'customer' — filters by BP role
    business_partner_id: uuid.UUID | None = None,
    company_code_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[OpenItem]:
    """Return open items for the tenant.

    Filters compose: a vendor BP filter + a Company Code filter
    returns only that BP's open items in that CC. With `role` and
    no BP, returns all open items for vendors (or customers) across
    the tenant.
    """
    # Join lines → headers (status filter) → GL account (is_recon
    # filter + recon_kind) → business_partner (name + filter).
    stmt = (
        select(FinDocumentLine, FinDocumentHeader, MdGlAccount, MdBusinessPartner)
        .join(
            FinDocumentHeader,
            FinDocumentHeader.id == FinDocumentLine.document_header_id,
        )
        .join(MdGlAccount, MdGlAccount.id == FinDocumentLine.gl_account_id)
        .join(
            MdBusinessPartner,
            MdBusinessPartner.id == FinDocumentLine.business_partner_id,
        )
        .where(
            FinDocumentLine.tenant_id == tenant_id,
            FinDocumentHeader.status == "posted",
            MdGlAccount.is_recon.is_(True),
            FinDocumentLine.business_partner_id.is_not(None),
        )
        .order_by(FinDocumentLine.posting_date, FinDocumentHeader.document_number)
        .limit(limit)
    )
    if business_partner_id is not None:
        stmt = stmt.where(FinDocumentLine.business_partner_id == business_partner_id)
    if company_code_id is not None:
        stmt = stmt.where(FinDocumentLine.company_code_id == company_code_id)

    rows = (await session.execute(stmt)).all()

    if role is not None:
        # Filter by BP role membership.
        bp_ids = {bp.id for _line, _hdr, _gl, bp in rows}
        if not bp_ids:
            return []
        bp_with_role = (
            (
                await session.execute(
                    select(MdBpRole.business_partner_id).where(
                        MdBpRole.tenant_id == tenant_id,
                        MdBpRole.business_partner_id.in_(bp_ids),
                        MdBpRole.role == role,
                    )
                )
            )
            .scalars()
            .all()
        )
        allowed = set(bp_with_role)
        rows = [r for r in rows if r[3].id in allowed]

    return [
        OpenItem(
            line_id=line.id,
            document_header_id=hdr.id,
            document_number=hdr.document_number,
            business_partner_id=bp.id,
            business_partner_name=bp.name,
            gl_account_id=gl.id,
            gl_account_number=gl.account_number,
            debit_credit=line.debit_credit,
            amount_local=line.amount_local,
            posting_date=line.posting_date,
            line_text=line.line_text,
            recon_kind=gl.recon_kind,
        )
        for (line, hdr, gl, bp) in rows
    ]


__all__ = ["OpenItem", "list_open_items"]
