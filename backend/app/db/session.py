"""Async engine, session factory, and the FastAPI ``get_session`` dependency.

Convention (enforced project-wide): relationships default to
``lazy="raise"``. Nothing loads a relationship implicitly — every access
that would trigger a lazy SELECT raises instead. Callers must eager-load
(``selectinload`` / ``joinedload``) or query explicitly. This keeps N+1s and
accidental I/O outside a transaction from ever shipping, which matters because
``rag.answer_stream`` must not hold an ``AsyncSession`` across a network call
(§4). See ``app/db/models.py`` where each relationship sets ``lazy="raise"``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# Async engine built from the single source of truth for the DSN.
# ``pool_pre_ping`` avoids handing out a dead connection after an idle period.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

# Session factory. ``expire_on_commit=False`` so objects stay usable after a
# commit inside a short progress-callback transaction (§4: each progress_cb is
# its own committed txn).
session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped ``AsyncSession``.

    The session is closed when the request finishes. It does not commit on
    your behalf — route handlers commit explicitly.
    """
    async with session_factory() as session:
        yield session
