"""Controlling ORM models (`co_*` tables).

Per ADR 0003 (universal journal), CO does NOT have its own postings
table. Cost / profit centre / internal order assignments are columns
on `fin_document_line`. CO owns the master data — `co_cost_centre`,
`co_profit_centre`, etc. — and the allocation / settlement
configuration that drives derivations.

v0.2 cut: only `co_cost_centre` lands here (the minimum needed to
populate `fin_document_line.cost_centre_id`). Profit centres,
internal orders, allocation cycles, and settlement profiles arrive
in v0.2.x.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openspine.core.database import Base, BusinessTableMixin


class CoCostCentre(BusinessTableMixin, Base):
    """A cost centre — a unit responsibility area within a controlling area.

    Belongs to one controlling area; an FI line can carry a cost
    centre id from any controlling area the company code is mapped
    to (validation lives in the FI posting service).
    """

    __tablename__ = "co_cost_centre"
    __table_args__ = (
        UniqueConstraint("tenant_id", "controlling_area_id", "code", name="uq_co_cost_centre_code"),
        Index(
            "ix_co_cost_centre_validity",
            "tenant_id",
            "controlling_area_id",
            "valid_from",
            "valid_to",
        ),
    )

    controlling_area_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("md_controlling_area.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    responsible_person: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_for_posting: Mapped[bool] = mapped_column(
        nullable=False, server_default=__import__("sqlalchemy").text("false")
    )


CO_TABLES_WITH_UPDATE_TRIGGER: tuple[str, ...] = (CoCostCentre.__tablename__,)
CO_TABLES_WITH_RLS: tuple[str, ...] = CO_TABLES_WITH_UPDATE_TRIGGER


__all__ = [
    "CO_TABLES_WITH_RLS",
    "CO_TABLES_WITH_UPDATE_TRIGGER",
    "CoCostCentre",
]
