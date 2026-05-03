"""Idempotent seeder for FI configuration tables.

Runs as part of `bootstrap_tenant_and_admin`. Seeds:

- A leading ledger (`0L`).
- Two document types: `SA` (general-ledger posting) and `AB` (reversal).

Tenants can copy + rename these to make their own; system rows are
keyed on `system_key` for idempotent upsert.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.fi.models import FinDocumentType, FinLedger
from openspine.md.service import create_number_range

# (system_key, code, name, is_leading)
LEDGERS: tuple[tuple[str, str, str, bool], ...] = (("0L", "0L", "Leading Ledger (IFRS)", True),)

# (system_key, code, name, description, number_range_object, is_reversal)
DOCUMENT_TYPES: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (
        "SA",
        "SA",
        "GL Posting",
        "General ledger journal entry — manual or system-generated.",
        "fi_document",
        False,
    ),
    (
        "AB",
        "AB",
        "Reversal",
        "Reversal of a previously posted document.",
        "fi_document",
        True,
    ),
)


async def seed_fi_configuration(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> dict[str, int]:
    """Idempotent. Returns counts of new rows created per category."""
    counts = {"ledgers": 0, "document_types": 0, "number_ranges": 0}

    for system_key, code, name, is_leading in LEDGERS:
        existing = (
            await session.execute(
                select(FinLedger).where(
                    FinLedger.tenant_id == tenant_id,
                    FinLedger.system_key == system_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                FinLedger(
                    tenant_id=tenant_id,
                    code=code,
                    name=name,
                    is_leading=is_leading,
                    is_system=True,
                    system_key=system_key,
                    created_by=actor_principal_id,
                    updated_by=actor_principal_id,
                )
            )
            counts["ledgers"] += 1

    for system_key, code, name, description, nr_object, is_reversal in DOCUMENT_TYPES:
        existing_dt = (
            await session.execute(
                select(FinDocumentType).where(
                    FinDocumentType.tenant_id == tenant_id,
                    FinDocumentType.system_key == system_key,
                )
            )
        ).scalar_one_or_none()
        if existing_dt is None:
            session.add(
                FinDocumentType(
                    tenant_id=tenant_id,
                    code=code,
                    name=name,
                    description=description,
                    number_range_object=nr_object,
                    is_reversal=is_reversal,
                    is_system=True,
                    system_key=system_key,
                    created_by=actor_principal_id,
                    updated_by=actor_principal_id,
                )
            )
            counts["document_types"] += 1

    # Number range for FI documents — one range per tenant, scope = "default".
    # Real deployments will override per Company Code; the default keeps
    # the v0.2 happy path single-line.
    from openspine.md.models import MdNumberRange

    existing_nr = (
        await session.execute(
            select(MdNumberRange).where(
                MdNumberRange.tenant_id == tenant_id,
                MdNumberRange.object_type == "fi_document",
                MdNumberRange.scope == "default",
            )
        )
    ).scalar_one_or_none()
    if existing_nr is None:
        await create_number_range(
            session,
            tenant_id=tenant_id,
            actor_principal_id=actor_principal_id,
            object_type="fi_document",
            from_number=1_000_000,
            to_number=1_999_999,
            description="System-default FI document number range.",
        )
        counts["number_ranges"] += 1

    await session.flush()
    return counts


__all__ = ["DOCUMENT_TYPES", "LEDGERS", "seed_fi_configuration"]
