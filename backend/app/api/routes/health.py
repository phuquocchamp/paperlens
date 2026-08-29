"""Liveness / readiness probes.

``/healthz`` is a pure liveness check (process is up). ``/readyz`` soft-checks the
external dependencies (Postgres, Qdrant, Redis). In P0 those checks are optional:
a dependency that is unreachable or whose wiring module (owned by another agent)
does not exist yet reports ``unavailable`` / ``unknown`` and degrades the aggregate
status rather than failing hard.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the API process is running."""
    return {"status": "ok"}


async def _check_redis() -> str:
    try:
        from redis.asyncio import Redis  # lazy import
    except ImportError:  # pragma: no cover
        return "unknown"
    client = None
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return "ok"
    except Exception:
        return "unavailable"
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass


async def _check_qdrant() -> str:
    try:
        from qdrant_client import AsyncQdrantClient  # lazy import
    except ImportError:  # pragma: no cover
        return "unknown"
    client = None
    try:
        client = AsyncQdrantClient(url=settings.qdrant_url)
        await client.get_collections()
        return "ok"
    except Exception:
        return "unavailable"
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:  # pragma: no cover
                pass


async def _check_db() -> str:
    # DB wiring (app.db.session, owned by Agent B) may not exist yet. Import inside
    # the handler so a missing module never breaks `app.main:app` importability.
    try:
        from sqlalchemy import text  # noqa: F401

        from app.db.session import session_factory  # type: ignore
    except Exception:
        return "unknown"
    try:
        from sqlalchemy import text as _text

        async with session_factory() as session:  # type: ignore[operator]
            await session.execute(_text("SELECT 1"))
        return "ok"
    except Exception:
        return "unavailable"


@router.get("/readyz")
async def readyz() -> dict[str, object]:
    """Readiness: soft-check DB + Qdrant + Redis. Never 5xx in P0."""
    checks = {
        "database": await _check_db(),
        "qdrant": await _check_qdrant(),
        "redis": await _check_redis(),
    }
    status = "ready" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}
