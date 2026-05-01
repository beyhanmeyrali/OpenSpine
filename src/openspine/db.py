"""Async SQLAlchemy engine + session factory.

The engine is created once at import time using the application settings.
Request-scoped sessions are dispensed by `get_session`, which is wired into
FastAPI dependencies in the route layer. Tests substitute a fixture-provided
session that runs against a transactional rollback.

Postgres-side row-level security is set per-session via
`SET LOCAL openspine.tenant_id = '<uuid>'` before any tenant-scoped query.
The middleware in `core/principal_context.py` is the single place that issues
that statement; nothing in domain code reaches around it.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from openspine.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
