"""Alembic environment — async, targeting ``Base.metadata``.

The DSN comes from ``app.config.settings.database_url`` (not from alembic.ini,
to avoid ConfigParser '%' interpolation on DB passwords). Importing
``app.db.models`` registers every table on ``Base.metadata`` so autogenerate and
``target_metadata`` see the full schema.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import settings
from app.db import models  # noqa: F401 — registers all tables on Base.metadata
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live DB using an async engine."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
