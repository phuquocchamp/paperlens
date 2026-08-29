"""Qdrant collection lifecycle and point I/O (Build Sheet §3, Qdrant block).

The collection ``chunks`` (alias name from ``settings.qdrant_collection_alias``)
carries two named vectors whose names must NEVER change (renaming breaks queries
at alias-swap time):

    text_dense  : 1024 dims, COSINE
    bm25_sparse : SparseVectorParams(modifier=Modifier.IDF)

Payload indexes are created BEFORE any upsert — the filterable-HNSW side edges are
only built when the index already exists. The payload is enough to FILTER and to
RECONCILE and NEVER contains ``content``.

All heavy imports (qdrant_client) are lazy so importing this module — and hence
``app.main`` / the test suite — stays cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qdrant_client import AsyncQdrantClient

# Named vectors — pinned identifiers (§3: "TÊN KHÔNG BAO GIỜ ĐỔI").
VECTOR_DENSE = "text_dense"
VECTOR_SPARSE = "bm25_sparse"


def get_client() -> AsyncQdrantClient:
    """Build an async Qdrant client.

    ``check_compatibility=False`` silences the 1.19 client vs 1.16 server minor
    mismatch (verified working for the ops we use: named + sparse vectors,
    tenant payload index, filtered delete/upsert/query).
    """
    from qdrant_client import AsyncQdrantClient

    return AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)


async def ensure_collection(client: AsyncQdrantClient | None = None) -> str:
    """Idempotently create the ``chunks`` collection + payload indexes.

    Returns the collection name. Safe to call repeatedly (startup / before every
    ingest). Payload indexes are created before any upsert ever happens.
    """
    from qdrant_client import models

    owns = client is None
    client = client or get_client()
    name = settings.qdrant_collection_alias
    try:
        if not await client.collection_exists(name):
            await client.create_collection(
                collection_name=name,
                vectors_config={
                    VECTOR_DENSE: models.VectorParams(
                        size=settings.embedding_dims,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    VECTOR_SPARSE: models.SparseVectorParams(
                        modifier=models.Modifier.IDF
                    )
                },
            )

        # Payload indexes — create BEFORE any upsert. Idempotent server-side.
        # project_id is the tenant key (is_tenant=True): storage defragmentation
        # only, harmless, kept per §3.
        await _ensure_index(
            client,
            name,
            "project_id",
            models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD, is_tenant=True
            ),
        )
        await _ensure_index(client, name, "document_id", models.PayloadSchemaType.KEYWORD)
        await _ensure_index(client, name, "modality", models.PayloadSchemaType.KEYWORD)
        await _ensure_index(client, name, "chunk_type", models.PayloadSchemaType.KEYWORD)
        await _ensure_index(client, name, "page", models.PayloadSchemaType.INTEGER)
        await _ensure_index(client, name, "quarantined", models.PayloadSchemaType.BOOL)
        return name
    finally:
        if owns:
            await client.close()


async def _ensure_index(
    client: AsyncQdrantClient, name: str, field: str, schema: Any
) -> None:
    """Create a payload index, tolerating the 'already exists' race."""
    try:
        await client.create_payload_index(name, field, schema)
    except Exception as exc:  # pragma: no cover - depends on server state
        # Re-creating an identical index is a no-op we accept; anything else
        # should surface. Qdrant returns 4xx with "already exists" in the body.
        if "already exists" not in str(exc).lower():
            raise


def build_payload(
    *,
    project_id: str,
    document_id: str,
    modality: str,
    chunk_type: str,
    page: int | None,
    section_path: str | None,
    chunk_index: int,
    content_hash: str,
    ingest_generation: int,
) -> dict[str, Any]:
    """Assemble a point payload. NEVER includes ``content`` (§3)."""
    return {
        "project_id": str(project_id),
        "document_id": str(document_id),
        "modality": modality,
        "chunk_type": chunk_type,
        "page": page,
        "quarantined": False,
        "section_path": section_path,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
        "embedding_model": settings.embedding_model,
        "embedding_dims": settings.embedding_dims,
        "ingest_generation": ingest_generation,
        "indexed_at": datetime.now(UTC).isoformat(),
    }


async def delete_by_document(
    client: AsyncQdrantClient, document_id: str
) -> None:
    """Delete every point for a document (idempotency: run before re-upsert)."""
    from qdrant_client import models

    await client.delete(
        collection_name=settings.qdrant_collection_alias,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=str(document_id)),
                    )
                ]
            )
        ),
        wait=True,
    )


async def upsert_points(
    client: AsyncQdrantClient,
    *,
    ids: list[str],
    dense: list[list[float]],
    sparse: list[tuple[list[int], list[float]]],
    payloads: list[dict[str, Any]],
) -> None:
    """Upsert a batch of points carrying both named vectors."""
    from qdrant_client import models

    points = [
        models.PointStruct(
            id=pid,
            vector={
                VECTOR_DENSE: dvec,
                VECTOR_SPARSE: models.SparseVector(indices=sidx, values=svals),
            },
            payload=payload,
        )
        for pid, dvec, (sidx, svals), payload in zip(
            ids, dense, sparse, payloads, strict=True
        )
    ]
    await client.upsert(
        collection_name=settings.qdrant_collection_alias, points=points, wait=True
    )


async def count_by_document(client: AsyncQdrantClient, document_id: str) -> int:
    """Count points for a document (used by the e2e proof + reconcile)."""
    from qdrant_client import models

    res = await client.count(
        collection_name=settings.qdrant_collection_alias,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=str(document_id)),
                )
            ]
        ),
        exact=True,
    )
    return res.count
