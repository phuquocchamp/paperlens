"""Project-scoped hybrid retrieval (Build Sheet §5.4, steps 5-6 + join-back).

Pipeline:
  1. Embed the query: dense (OpenAI ``text-embedding-3-large`` @ dims=1024, the
     SAME model/dims as ingestion) + sparse (FastEmbed BM25 *query* side).
  2. Two ``query_points`` calls against Qdrant (dense prefetch 40, sparse prefetch
     40), both scoped by a ``project_id`` filter (+ optional ``document_ids``) and
     excluding quarantined points.
  3. Reciprocal-Rank Fusion in Python with an EXPLICIT ``k`` (``settings.rrf_k``).
     We fuse ourselves rather than using Qdrant's server-side ``FusionQuery``
     because the 1.19 client's ``FusionQuery`` exposes no ``k`` parameter, and the
     build sheet's blocker #1 fix requires (a) an explicit, measurable ``k`` and
     (b) the raw dense cosine of the top hit for the off-topic gate — both of which
     the two-query + Python-RRF shape gives us directly.
  4. Per-document quota (default 4, relaxed unconditionally by back-filling from
     the parked overflow when the admitted list is underfilled).
  5. Join-back: ONE Postgres query with ``WITH ORDINALITY`` preserving Qdrant
     rank order, enforcing ``project_id`` + ``NOT is_quarantined`` +
     ``documents.deleted_at IS NULL`` as defense in depth (payload can lie).

Returns hydrated chunks (content, page, section_path, doc title/filename,
content_hash) in ranked order, capped to ``min(top_k, context_top_k)``. Never
holds an ``AsyncSession`` across a network call (§4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.config import settings
from app.rag.qdrant_store import VECTOR_DENSE, VECTOR_SPARSE, get_client


@dataclass
class RetrievedChunk:
    """One hydrated retrieval hit, in ranked order."""

    chunk_id: str
    content: str
    content_hash: str
    section_path: str | None
    page: int | None
    chunk_type: str
    document_id: str
    document_title: str | None
    filename: str
    dense_score: float | None  # raw COSINE similarity; None if sparse-only hit
    rank: int  # 0-based fused rank
    # Parent-media links (P3): set on figure shadow chunks / table_child chunks so
    # answer_stream can resolve the figure/table row and emit data-figure/table.
    figure_id: str | None = None
    table_id: str | None = None


@dataclass
class RetrievalResult:
    """Hydrated, ranked chunks plus the signals the abstain gate needs."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    max_dense_cosine: float = 0.0
    retrieved_count: int = 0   # fused candidates before join-back
    hydrated_count: int = 0    # rows that survived the DB guardrails


# --------------------------------------------------------------------------- #
# Query embedding (self-contained; does not touch the ingest-side cache)
# --------------------------------------------------------------------------- #
async def _embed_query_dense(question: str) -> list[float]:
    """Dense query vector via the same model + dims as ingestion."""
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.embeddings.create(
            model=settings.embedding_model,
            input=[question],
            dimensions=settings.embedding_dims,
        )
    finally:
        await client.close()
    return list(resp.data[0].embedding)


_BM25_QUERY_MODEL = None


def _embed_query_sparse(question: str) -> tuple[list[int], list[float]]:
    """Sparse query vector via FastEmbed BM25 *query* side.

    ``query_embed`` (not ``embed``) is mandatory: ``embed`` applies document-side
    term-frequency weighting, whereas the query representation must be the plain
    query-side vector — the collection's ``Modifier.IDF`` supplies IDF server-side.
    """
    global _BM25_QUERY_MODEL
    if _BM25_QUERY_MODEL is None:
        from fastembed import SparseTextEmbedding

        _BM25_QUERY_MODEL = SparseTextEmbedding(model_name="Qdrant/bm25")
    emb = next(iter(_BM25_QUERY_MODEL.query_embed(question)))
    return [int(i) for i in emb.indices], [float(v) for v in emb.values]


# --------------------------------------------------------------------------- #
# Filter + fusion helpers
# --------------------------------------------------------------------------- #
def _build_filter(project_id: str, document_ids: list[str] | None):
    from qdrant_client import models

    must = [
        models.FieldCondition(
            key="project_id", match=models.MatchValue(value=str(project_id))
        )
    ]
    if document_ids:
        must.append(
            models.FieldCondition(
                key="document_id",
                match=models.MatchAny(any=[str(d) for d in document_ids]),
            )
        )
    return models.Filter(
        must=must,
        # NOTE: Qdrant payload key is "quarantined" (qdrant_store.build_payload),
        # NOT the Postgres column name "is_quarantined".
        must_not=[
            models.FieldCondition(
                key="quarantined", match=models.MatchValue(value=True)
            )
        ],
    )


def _rrf_fuse(
    dense_ids: list[str], sparse_ids: list[str], k: int
) -> list[str]:
    """Reciprocal-Rank Fusion with an explicit ``k`` (0-based ranks).

    ``score(id) = sum over lists of 1 / (k + rank)``. Returns ids sorted by fused
    score descending. Deterministic tie-break by first-seen order.
    """
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    seen = 0
    for lst in (dense_ids, sparse_ids):
        for rank, pid in enumerate(lst):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
            if pid not in order:
                order[pid] = seen
                seen += 1
    return sorted(scores, key=lambda pid: (-scores[pid], order[pid]))


def _apply_quota(
    fused_ids: list[str], doc_of: dict[str, str], quota: int, cap: int
) -> list[str]:
    """Per-document quota with unconditional relaxation when underfilled.

    Single admitting pass (park overflow), then back-fill from the parked list in
    fused-rank order until the admitted list reaches ``cap`` (relaxes the quota).
    """
    admitted: list[str] = []
    parked: list[str] = []
    per_doc: dict[str, int] = {}
    for pid in fused_ids:
        doc = doc_of.get(pid, "")
        if per_doc.get(doc, 0) < quota:
            admitted.append(pid)
            per_doc[doc] = per_doc.get(doc, 0) + 1
        else:
            parked.append(pid)
        if len(admitted) >= cap:
            return admitted[:cap]
    for pid in parked:
        if len(admitted) >= cap:
            break
        admitted.append(pid)
    return admitted[:cap]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def retrieve(
    *,
    project_id: str,
    question: str,
    session_factory,
    document_ids: list[str] | None = None,
    top_k: int = 8,
) -> RetrievalResult:
    """Run project-scoped hybrid retrieval and return hydrated, ranked chunks."""
    from qdrant_client import models

    # ---- 1. embed the query (dense + sparse) ---------------------------- #
    dense_vec = await _embed_query_dense(question)
    sparse_idx, sparse_val = _embed_query_sparse(question)

    # ---- 2. two Qdrant queries (dense prefetch + sparse prefetch) ------- #
    qfilter = _build_filter(project_id, document_ids)
    client = get_client()
    try:
        dense_res = await client.query_points(
            collection_name=settings.qdrant_collection_alias,
            query=dense_vec,
            using=VECTOR_DENSE,
            limit=settings.prefetch_dense,
            query_filter=qfilter,
            with_payload=True,
        )
        sparse_res = await client.query_points(
            collection_name=settings.qdrant_collection_alias,
            query=models.SparseVector(indices=sparse_idx, values=sparse_val),
            using=VECTOR_SPARSE,
            limit=settings.prefetch_sparse,
            query_filter=qfilter,
            with_payload=True,
        )
    finally:
        await client.close()

    dense_hits = list(dense_res.points)
    sparse_hits = list(sparse_res.points)

    dense_ids = [str(p.id) for p in dense_hits]
    sparse_ids = [str(p.id) for p in sparse_hits]

    # Off-topic gate signal: raw max dense COSINE of the top candidate (blocker
    # fix #1 — gate on cosine, never on the RRF score which only sees rank).
    max_dense_cosine = max((float(p.score) for p in dense_hits), default=0.0)

    # Map ids -> dense score and -> document_id (from payload) for quota + report.
    dense_score_of: dict[str, float] = {
        str(p.id): float(p.score) for p in dense_hits
    }
    doc_of: dict[str, str] = {}
    for p in dense_hits + sparse_hits:
        payload = p.payload or {}
        doc_of.setdefault(str(p.id), str(payload.get("document_id", "")))

    if not dense_ids and not sparse_ids:
        return RetrievalResult(max_dense_cosine=max_dense_cosine)

    # ---- 3. RRF (explicit k) -> 4. per-doc quota ------------------------ #
    fused = _rrf_fuse(dense_ids, sparse_ids, settings.rrf_k)
    fused = fused[: settings.fused_top_k]
    quota_ids = _apply_quota(
        fused, doc_of, settings.per_doc_quota, settings.fused_top_k
    )

    context_k = min(top_k, settings.context_top_k)
    candidate_ids = quota_ids[:context_k]
    if not candidate_ids:
        return RetrievalResult(
            max_dense_cosine=max_dense_cosine, retrieved_count=len(fused)
        )

    # ---- 5. join-back: ONE Postgres query, WITH ORDINALITY -------------- #
    hydrated = await _hydrate(session_factory, project_id, candidate_ids)

    chunks: list[RetrievedChunk] = []
    for rank, row in enumerate(hydrated):
        cid = str(row["id"])
        chunks.append(
            RetrievedChunk(
                chunk_id=cid,
                content=row["content"],
                content_hash=row["content_hash"],
                section_path=row["section_path"],
                page=row["page_start"],
                chunk_type=row["chunk_type"],
                document_id=str(row["document_id"]),
                document_title=row["title"],
                filename=row["filename"],
                dense_score=dense_score_of.get(cid),
                rank=rank,
                figure_id=(
                    str(row["figure_id"]) if row["figure_id"] is not None else None
                ),
                table_id=(
                    str(row["table_id"]) if row["table_id"] is not None else None
                ),
            )
        )

    return RetrievalResult(
        chunks=chunks,
        max_dense_cosine=max_dense_cosine,
        retrieved_count=len(fused),
        hydrated_count=len(chunks),
    )


_JOINBACK_SQL = text(
    """
    SELECT c.id, c.content, c.content_hash, c.section_path, c.page_start,
           c.chunk_type, c.document_id, c.figure_id, c.table_id,
           d.title, d.filename, o.ord
    FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS o(chunk_id, ord)
    JOIN chunks c ON c.id = o.chunk_id
    JOIN documents d ON d.id = c.document_id
    WHERE c.project_id = CAST(:project_id AS uuid)  -- layer 1: cross-project leak guard
      AND NOT c.is_quarantined                       -- layer 2: guardrail at the DB
      AND d.deleted_at IS NULL                        -- layer 3: orphans vanish now
    ORDER BY o.ord
    """
)


async def _hydrate(
    session_factory, project_id: str, chunk_ids: list[str]
) -> list[dict]:
    """Run the ordinality-preserving join-back. Short, read-only transaction."""
    async with session_factory() as session:
        res = await session.execute(
            _JOINBACK_SQL, {"ids": chunk_ids, "project_id": str(project_id)}
        )
        return [dict(m) for m in res.mappings().all()]


__all__ = ["retrieve", "RetrievedChunk", "RetrievalResult"]
