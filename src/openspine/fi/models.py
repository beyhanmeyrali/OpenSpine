"""Financial Accounting ORM models (`fin_*` tables).

Implements the v0.2 cut described in `docs/modules/fi-finance.md`
and ADR 0003 (universal journal).

The universal journal — `fin_document_header` + `fin_document_line`
— is the single posting table set for FI AND CO. CO dimensions
(cost centre, profit centre, internal order, segment, ledger
group) are columns on `fin_document_line`, not separate ledgers.
Per ADR 0003 §"Practical implications", reconciliation between
FI and CO disappears because there's nothing to reconcile.

Append-only: per `data-model.md`, `fin_document_*` is never
updated and never soft-deleted. Reversals are new rows with a
back-pointer to the original. The schema enforces this by
omitting `updated_*`, `version`, and any `deleted_at` columns.

Wide-table caveat: `fin_document_line` carries many columns,
most NULL on any given posting. Index strategy below covers the
common scan patterns (by GL, by cost centre, by document, by
posting date).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openspine.core.database import Base, BusinessTableMixin

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

DOC_STATUSES = ("posted", "reversed")
DEBIT_CREDIT = ("D", "C")


def _enum_check(column: str, allowed: tuple[str, ...]) -> str:
    inside = ", ".join(f"'{v}'" for v in allowed)
    return f"{column} IN ({inside})"


# ---------------------------------------------------------------------------
# Configuration tables (mutable — these ARE updateable)
# ---------------------------------------------------------------------------


class FinLedger(BusinessTableMixin, Base):
    """A ledger — leading IFRS (`0L`), local GAAP (`2L`), tax book, etc.

    Every `fin_document_line` belongs to one ledger. Default
    deployment ships with `0L` only; multi-ledger Company Codes are
    opt-in (per `fi-finance.md` open Q1 — recorded as a v0.2 default).
    """

    __tablename__ = "fin_ledger"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_fin_ledger_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_leading: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    system_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class FinDocumentType(BusinessTableMixin, Base):
    """Document type catalogue: `SA` (GL posting), `KR` (vendor invoice),
    `DR` (customer invoice), `KZ`/`DZ` (payments), `AB` (reversal).

    System types ship from the seeder; tenants can copy + rename to
    customise (e.g., per-Company-Code GL adjustment types).
    """

    __tablename__ = "fin_document_type"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_fin_document_type_code"),)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Default number-range object_type for documents of this type. The
    # service looks up md_number_range with this object_type to allocate
    # the document number.
    number_range_object: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_reversal: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    system_key: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Universal journal (append-only)
# ---------------------------------------------------------------------------


class FinDocumentHeader(Base):
    """One row per posted document. Append-only.

    `document_number` is unique per `(tenant, company_code,
    fiscal_year, document_type)` — gap-free per number range, which
    several jurisdictions require by law.

    `reversal_of_id` points back at the original document if this
    row is a reversal (`document_type.is_reversal = TRUE`). The
    original's `reversed_by_id` points at this row. Both pointers
    are NULL on normal postings.
    """

    __tablename__ = "fin_document_header"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_code_id",
            "fiscal_year",
            "document_type_id",
            "document_number",
            name="uq_fin_document_header_number",
        ),
        CheckConstraint(_enum_check("status", DOC_STATUSES), name="ck_fin_document_header_status"),
        Index(
            "ix_fin_document_header_company_period",
            "tenant_id",
            "company_code_id",
            "fiscal_year",
            "period",
        ),
        Index(
            "ix_fin_document_header_posting_date",
            "tenant_id",
            "posting_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
        index=True,
    )
    company_code_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_company_code.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_type_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_document_type.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'posted'"))
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_document_header.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    reversed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_document_header.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )


class FinDocumentLine(Base):
    """The universal journal — one row per debit/credit line. Append-only.

    Carries the GL account, multi-currency amounts, and ALL CO
    dimensions (cost centre, profit centre, internal order, segment,
    ledger group). Per ADR 0003 §"Decision", FI and CO share this
    table — CO has no separate postings table.

    `debit_credit` ∈ {'D', 'C'} discriminates. `amount_local` is
    always positive; the debit/credit indicator is what makes it a
    debit or a credit. This avoids the entire class of "is the sign
    convention inverted in this report" bugs.
    """

    __tablename__ = "fin_document_line"
    __table_args__ = (
        UniqueConstraint("document_header_id", "line_number", name="uq_fin_document_line_number"),
        CheckConstraint(
            _enum_check("debit_credit", DEBIT_CREDIT),
            name="ck_fin_document_line_debit_credit",
        ),
        CheckConstraint("amount_local >= 0", name="ck_fin_document_line_amount_positive"),
        Index(
            "ix_fin_document_line_gl_account",
            "tenant_id",
            "gl_account_id",
            "posting_date",
        ),
        Index(
            "ix_fin_document_line_cost_centre",
            "tenant_id",
            "cost_centre_id",
            "posting_date",
        ),
        Index(
            "ix_fin_document_line_company_period",
            "tenant_id",
            "company_code_id",
            "fiscal_year",
            "period",
        ),
        Index("ix_fin_document_line_business_partner", "tenant_id", "business_partner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
        index=True,
    )
    document_header_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_document_header.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    company_code_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_company_code.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)

    # GL & ledger
    gl_account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_gl_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ledger_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_ledger.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Amount + currency
    debit_credit: Mapped[str] = mapped_column(Text, nullable=False)
    amount_local: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    local_currency_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount_document: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    document_currency_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    amount_group: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    group_currency_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_currency.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # AP/AR — populated when this line is on a recon account
    business_partner_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_business_partner.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # CO dimensions — the universal-journal payoff. None of these
    # are mandatory at the schema level; the service-layer derivation
    # rules decide which combinations are valid for which GL accounts.
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("co_cost_centre.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    profit_centre_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_order_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tax — covered minimally for v0.2; full tax engine is later.
    tax_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Quantities (used by MM/PP postings; null on pure GL entries)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    quantity_uom_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_uom.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    line_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


FIN_TABLES_WITH_UPDATE_TRIGGER: tuple[str, ...] = (
    FinLedger.__tablename__,
    FinDocumentType.__tablename__,
)

FIN_TABLES_WITH_RLS: tuple[str, ...] = (
    *FIN_TABLES_WITH_UPDATE_TRIGGER,
    FinDocumentHeader.__tablename__,
    FinDocumentLine.__tablename__,
)


__all__ = [
    "DEBIT_CREDIT",
    "DOC_STATUSES",
    "FIN_TABLES_WITH_RLS",
    "FIN_TABLES_WITH_UPDATE_TRIGGER",
    "FinDocumentHeader",
    "FinDocumentLine",
    "FinDocumentType",
    "FinLedger",
]
