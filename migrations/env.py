"""Alembic environment.

Reads the database URL from OpenSpine settings rather than alembic.ini so
local, CI, and production deployments share one config surface.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from openspine.config import get_settings
from openspine.core.database import metadata

# Import every domain package so each one's ORM models register their
# tables on the shared `metadata`. Without these imports, --autogenerate
# would think the schema is empty.
import openspine.identity  # noqa: F401, E402  (registration side-effect)
import openspine.md  # noqa: F401, E402  (registration side-effect)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# Models are progressively added under openspine.{identity,md,fi,co,mm,pp};
# importing those modules registers their tables on this shared metadata.
# Until they exist, --autogenerate sees an empty schema, which is correct.
target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
