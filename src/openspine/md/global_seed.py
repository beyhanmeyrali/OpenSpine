"""Idempotent seeding of the MD global catalogues.

Currency / rate-type / UoM catalogues are global (no `tenant_id`) and
universal — every deployment needs the same baseline. The seeder
runs once at first deploy and again whenever the catalogue is
extended; existing rows are never touched.

The bootstrap admin owns the seed rows on `created_by` because there
is no other principal at first-deploy time. Cross-tenant attribution
is tolerable here because these rows are global, not tenant-scoped.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.md.models import MdCurrency, MdExchangeRateType, MdUom

# (code, name, decimals)
CURRENCIES: tuple[tuple[str, str, int], ...] = (
    ("USD", "US Dollar", 2),
    ("EUR", "Euro", 2),
    ("GBP", "Pound Sterling", 2),
    ("JPY", "Japanese Yen", 0),
    ("CHF", "Swiss Franc", 2),
    ("CAD", "Canadian Dollar", 2),
    ("AUD", "Australian Dollar", 2),
    ("CNY", "Chinese Yuan Renminbi", 2),
    ("INR", "Indian Rupee", 2),
    ("BRL", "Brazilian Real", 2),
    ("TRY", "Turkish Lira", 2),
    ("AED", "UAE Dirham", 2),
    ("SGD", "Singapore Dollar", 2),
    ("HKD", "Hong Kong Dollar", 2),
    ("MXN", "Mexican Peso", 2),
    ("ZAR", "South African Rand", 2),
    ("KRW", "South Korean Won", 0),
    ("SEK", "Swedish Krona", 2),
    ("NOK", "Norwegian Krone", 2),
    ("DKK", "Danish Krone", 2),
)

# (code, description)
RATE_TYPES: tuple[tuple[str, str], ...] = (
    ("M", "Average rate"),
    ("B", "Bank-selling rate"),
    ("G", "Bank-buying rate"),
)

# (code, description, dimension)
UOMS: tuple[tuple[str, str, str], ...] = (
    ("EA", "Each", "count"),
    ("PC", "Piece", "count"),
    ("KG", "Kilogram", "mass"),
    ("G", "Gram", "mass"),
    ("LB", "Pound", "mass"),
    ("T", "Metric ton", "mass"),
    ("L", "Litre", "volume"),
    ("ML", "Millilitre", "volume"),
    ("M3", "Cubic metre", "volume"),
    ("M", "Metre", "length"),
    ("CM", "Centimetre", "length"),
    ("MM", "Millimetre", "length"),
    ("KM", "Kilometre", "length"),
    ("M2", "Square metre", "area"),
    ("H", "Hour", "time"),
    ("MIN", "Minute", "time"),
    ("D", "Day", "time"),
    ("CTN", "Carton", "count"),
    ("PAL", "Pallet", "count"),
    ("BOX", "Box", "count"),
)


async def seed_md_globals(
    session: AsyncSession, *, actor_principal_id: uuid.UUID
) -> dict[str, int]:
    """Upsert global MD catalogues (currencies, rate types, UoMs).

    Idempotent. Returns counts of new rows created per catalogue.
    """
    counts = {"currencies": 0, "rate_types": 0, "uoms": 0}

    for code, name, decimals in CURRENCIES:
        existing = (
            await session.execute(select(MdCurrency).where(MdCurrency.code == code))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                MdCurrency(
                    code=code,
                    name=name,
                    decimals=decimals,
                    created_by=actor_principal_id,
                    updated_by=actor_principal_id,
                )
            )
            counts["currencies"] += 1

    for code, description in RATE_TYPES:
        existing_rt = (
            await session.execute(select(MdExchangeRateType).where(MdExchangeRateType.code == code))
        ).scalar_one_or_none()
        if existing_rt is None:
            session.add(
                MdExchangeRateType(
                    code=code,
                    description=description,
                    created_by=actor_principal_id,
                    updated_by=actor_principal_id,
                )
            )
            counts["rate_types"] += 1

    for code, description, dimension in UOMS:
        existing_uom = (
            await session.execute(select(MdUom).where(MdUom.code == code))
        ).scalar_one_or_none()
        if existing_uom is None:
            session.add(
                MdUom(
                    code=code,
                    description=description,
                    dimension=dimension,
                    created_by=actor_principal_id,
                    updated_by=actor_principal_id,
                )
            )
            counts["uoms"] += 1

    await session.flush()
    return counts


__all__ = ["CURRENCIES", "RATE_TYPES", "UOMS", "seed_md_globals"]
