"""``reconcile`` — cross-check Postgres chunks against Qdrant points (Build Sheet §3).

The join-back invariant is: every live, embedded chunk has exactly one Qdrant
point (id == chunk id), and that point's payload agrees with the chunk on
``content_hash`` and ``embedding_model``. This job re-derives that invariant from
both stores and classifies every discrepancy:

    orphan_vectors  : a point exists in Qdrant with no matching live chunk.
                      HARMLESS — a leftover from a delete/re-ingest. LOG at info.
    missing_vectors : a live chunk has no matching point. ALERT — the chunk is
                      retrievable in Postgres but can never be found. WARNING.
    stale_vectors   : a point matches a live chunk but its payload
                      ``content_hash`` or ``embedding_model`` disagrees (a stale
                      re-embed / model drift). ALERT. WARNING.

"Live chunk" = ``is_embedded=true`` AND ``is_quarantined=false`` AND the owning
document is not soft-deleted. Table-parent rows (``is_embedded=false``) are
correctly excluded — they intentionally have no point.

Asymmetry held throughout (per §3):
  - the LIVE-CHUNK set drives ``missing_vectors``;
  - the POINT set drives ``orphan_vectors`` and ``stale_vectors``.

This module NEVER writes either store — it only reads and reports. It opens its
own short read transactions via the injected ``session_factory`` and never holds
a session across the Qdrant network calls (§4).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from app.config import settings
from app.db.models import Chunk, Document
from app.domain.status import DocumentStatus
from app.rag.qdrant_store import get_client

log = structlog.get_logger(__name__)

# Qdrant scroll page size. The scroll loop pages until the offset is exhausted,
# so this only trades round-trips against per-response size; it is not a cap.
_SCROLL_PAGE = 1000


async def _load_live_chunks(
    session_factory, *, project_id: str | None
) -> dict[str, str]:
    """Return ``{chunk_id: content_hash}`` for every live, embedded chunk.

    Live = embedded, not quarantined, and the owning document is not
    soft-deleted (``deleted_at IS NULL`` and status != DELETED). Optionally
    scoped to one project. Keys are the string form of the chunk UUID so they
    compare directly against Qdrant point ids.
    """
    stmt = (
        select(Chunk.id, Chunk.content_hash)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.is_embedded.is_(True),
            Chunk.is_quarantined.is_(False),
            Document.deleted_at.is_(None),
            Document.status != DocumentStatus.DELETED.value,
        )
    )
    if project_id is not None:
        # Match on chunks.project_id; the Qdrant side filters the same string.
        stmt = stmt.where(Chunk.project_id == project_id)

    live: dict[str, str] = {}
    async with session_factory() as session:
        result = await session.execute(stmt)
        for chunk_id, content_hash in result.all():
            live[str(chunk_id)] = content_hash
    return live


async def _scroll_points(client, *, project_id: str | None) -> dict[str, dict]:
    """Return ``{point_id: payload}`` for every point (optionally one project).

    Pages the Qdrant scroll cursor until it is exhausted — a single bounded call
    would silently under-report on a large corpus and manufacture false
    ``missing_vectors`` alerts. Vectors are never fetched (``with_vectors=False``);
    only the payload fields used for reconciliation are needed.
    """
    from qdrant_client import models

    scroll_filter = None
    if project_id is not None:
        scroll_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="project_id",
                    match=models.MatchValue(value=str(project_id)),
                )
            ]
        )

    points: dict[str, dict] = {}
    offset = None
    while True:
        batch, offset = await client.scroll(
            collection_name=settings.qdrant_collection_alias,
            scroll_filter=scroll_filter,
            limit=_SCROLL_PAGE,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        for point in batch:
            points[str(point.id)] = point.payload or {}
        if offset is None:
            break
    return points


def _classify(
    live_chunks: dict[str, str], points: dict[str, dict]
) -> dict[str, int]:
    """Classify the two sets into the reconcile counters.

    The LIVE-CHUNK set drives ``missing_vectors``; the POINT set drives
    ``orphan_vectors`` and ``stale_vectors`` — nothing else.
    """
    orphan = 0
    stale = 0
    orphan_ids: list[str] = []
    stale_ids: list[str] = []

    for point_id, payload in points.items():
        live_hash = live_chunks.get(point_id)
        if live_hash is None:
            # No live chunk owns this point → harmless leftover.
            orphan += 1
            orphan_ids.append(point_id)
            continue
        # Point matches a live chunk: it is stale iff the embedding model or the
        # content hash drifted from what the chunk now says.
        payload_model = payload.get("embedding_model")
        payload_hash = payload.get("content_hash")
        if payload_model != settings.embedding_model or payload_hash != live_hash:
            stale += 1
            stale_ids.append(point_id)

    missing = 0
    missing_ids: list[str] = []
    for chunk_id in live_chunks:
        if chunk_id not in points:
            missing += 1
            missing_ids.append(chunk_id)

    # Log at the level the invariant demands: orphans are informational,
    # missing/stale are alerts. Sample ids so the log stays bounded.
    if orphan:
        log.info("reconcile.orphan_vectors", count=orphan, sample=orphan_ids[:10])
    if missing:
        log.warning(
            "reconcile.missing_vectors", count=missing, sample=missing_ids[:10]
        )
    if stale:
        log.warning("reconcile.stale_vectors", count=stale, sample=stale_ids[:10])

    return {
        "hydrated": len(live_chunks),
        "retrieved": len(points),
        "orphan_vectors": orphan,
        "missing_vectors": missing,
        "stale_vectors": stale,
    }


async def reconcile(
    *,
    project_id: str | None = None,
    session_factory,
) -> dict[str, int]:
    """Reconcile Qdrant points against live Postgres chunks.

    See ``app.rag.reconcile`` for the public contract. Returns a flat
    ``dict[str, int]`` summary: ``hydrated`` (live chunks), ``retrieved``
    (points scanned), ``orphan_vectors``, ``missing_vectors``, ``stale_vectors``.
    """
    # Read Postgres first (cheap, short txn), then Qdrant (network, no session
    # held). Order is not correctness-critical — this is a best-effort snapshot.
    live_chunks = await _load_live_chunks(session_factory, project_id=project_id)

    client = get_client()
    try:
        points = await _scroll_points(client, project_id=project_id)
    finally:
        await client.close()

    summary = _classify(live_chunks, points)
    log.info(
        "reconcile.summary",
        project_id=str(project_id) if project_id is not None else "all",
        **summary,
    )
    return summary
