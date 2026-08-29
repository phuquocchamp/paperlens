"""Public API surface of the RAG package (CONTRACT.md §4).

Six coroutines make up the whole contract between the API/worker layers and the
retrieval pipeline. For P0 only the *signatures* are frozen; every body raises
``NotImplementedError``. The one real streaming path used by P0 lives in
``app/api/routes/chat.py`` (the fake stream), NOT here — ``answer_stream`` keeps
its stub so the contract stays honest until the pipeline lands.

Cross-cutting rules encoded by these signatures (translated from the Vietnamese
build sheet §4):

* The caller injects a ``session_factory`` (an async-session *factory*), never a
  live ``AsyncSession``. RAG opens short transactions itself and must never hold
  a session across a network call (embedding, Qdrant, LLM).
* ``ingest_document`` is idempotent: it deletes existing Qdrant points by
  ``document_id`` filter before upserting, and it does NOT read or write the
  ``documents`` table (the ARQ task owns that state machine).
* Failure semantics are the only way ARQ can tell "do not retry" from "retry":
    - ``ParseError | TooLarge | Blocked``  -> mapped to REJECTED (task returns).
    - ``Embedding | VectorStore | Storage`` -> mapped to FAILED (task raises).
    - ``asyncio.CancelledError`` is never swallowed -> the job is re-queued.
* ``answer_stream`` yields protocol-neutral ``GenEvent`` objects. It does NOT
  write Postgres. Translation to Vercel AI SDK frames happens in exactly one
  place: ``app/streaming/render_aisdk.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import (
    TYPE_CHECKING,
    Protocol,
)

from pydantic import BaseModel

# GenEvent is re-exported so callers import it from a single place.
from app.domain.events import GenEvent

if TYPE_CHECKING:
    # Typing-only imports: these modules are owned by other agents and may not
    # exist yet at import time. Keeping them under TYPE_CHECKING avoids a hard
    # runtime dependency so ``app.main:app`` stays importable during P0.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    SessionFactory = async_sessionmaker[AsyncSession]
else:  # pragma: no cover - runtime alias only
    SessionFactory = Callable[[], object]


# --------------------------------------------------------------------------- #
# Supporting types
# --------------------------------------------------------------------------- #
class IngestResult(BaseModel):
    """Summary returned by :func:`ingest_document`.

    Counters are logged by the ARQ task to detect orphan/stale vectors during the
    nightly ``reconcile`` cron. ``document_id`` echoes the input for correlation.
    """

    document_id: str
    chunk_count: int = 0
    figure_count: int = 0
    table_count: int = 0
    embedded_count: int = 0
    reindexed: bool = False
    # Additive P1 telemetry (defaults keep the P0 contract intact). The ARQ task
    # logs these to ``ingestion_jobs.usage`` and the e2e proof prints the cost.
    billed_tokens: int = 0
    cached_count: int = 0
    cost_usd: float = 0.0
    avg_chunk_tokens: float = 0.0


class ProgressCb(Protocol):
    """Progress callback. Each invocation is its own short, committed transaction.

    Called as ``await progress(stage, fraction)`` where ``fraction`` is in
    ``[0.0, 1.0]``. Committing per call is mandatory: otherwise polling shows 0%
    for minutes and then jumps to 100%.
    """

    def __call__(self, stage: str, fraction: float) -> Awaitable[None]: ...


# Message history is a protocol-neutral list of role/content turns. Kept loose
# for P0; the concrete schema is pinned by the pipeline later.
History = Sequence[dict[str, str]]


# --------------------------------------------------------------------------- #
# Public API (6 functions) — P0 stubs
# --------------------------------------------------------------------------- #
async def ingest_document(
    *,
    document_id: str,
    project_id: str,
    file_path: str,
    session_factory: SessionFactory,
    file_store: object,
    progress: ProgressCb | None = None,
    max_pages: int | None = None,
    force_reindex: bool = False,
) -> IngestResult:
    """Parse, chunk, embed and upsert a single document into Qdrant + Postgres.

    Idempotent: deletes existing points by ``document_id`` filter before upsert.
    Does NOT touch the ``documents`` table (the ARQ task owns that state machine).

    Raises (mapped by the caller):
        ParseError | TooLarge | Blocked -> REJECTED (return, do not retry).
        EmbeddingError | VectorStoreError | StorageError -> FAILED (raise, retry).
        asyncio.CancelledError -> propagated, never swallowed (re-queue).
    """
    # Lazy import: the pipeline pulls docling/openai/qdrant. Keeping it out of
    # module scope preserves ``app.rag`` (and ``app.main``) import-cheapness.
    from app.rag.pipeline import ingest_document as _impl

    return await _impl(
        document_id=document_id,
        project_id=project_id,
        file_path=file_path,
        session_factory=session_factory,
        file_store=file_store,
        progress=progress,
        max_pages=max_pages,
        force_reindex=force_reindex,
    )


async def answer_stream(
    *,
    project_id: str,
    question: str,
    history: History,
    session_factory: SessionFactory,
    document_ids: Sequence[str] | None = None,
    top_k: int = 8,
    trace_id: str | None = None,
) -> AsyncIterator[GenEvent]:
    """Stream a grounded answer as protocol-neutral :class:`GenEvent` objects.

    Yields events in the order fixed by CONTRACT.md §2 (status -> citations ->
    figures -> tables -> text lifecycle -> re-emitted citations/figures ->
    finish). Does NOT write Postgres and does NOT hold an ``AsyncSession`` across
    any network call. The route maps the events to AI SDK frames via
    ``app/streaming/render_aisdk.py``.

    P2: real hybrid retrieval + LLM streaming + server-dereferenced citations.
    Delegates to ``app.rag.query`` (lazy import keeps ``app.rag``/``app.main``
    import-cheap — the impl pulls openai/qdrant/fastembed).
    """
    from app.rag.query import answer_stream as _impl

    async for event in _impl(
        project_id=project_id,
        question=question,
        history=history,
        session_factory=session_factory,
        document_ids=document_ids,
        top_k=top_k,
        trace_id=trace_id,
    ):
        yield event


async def delete_document(
    *,
    document_id: str,
    project_id: str,
    session_factory: SessionFactory,
    file_store: object | None = None,
) -> None:
    """Delete one document's vectors (Qdrant) and derived rows. Idempotent."""
    # Lazy import (mirrors ingest/answer): the impl pulls qdrant_client.
    from app.rag.delete import delete_document as _impl

    await _impl(
        document_id=document_id,
        project_id=project_id,
        session_factory=session_factory,
        file_store=file_store,
    )


async def delete_project(
    *,
    project_id: str,
    session_factory: SessionFactory,
    file_store: object | None = None,
) -> None:
    """Delete every vector/document for a project. Idempotent."""
    raise NotImplementedError


async def reconcile(
    *,
    project_id: str | None,
    session_factory: SessionFactory,
) -> dict[str, int]:
    """Nightly cron: reconcile Qdrant vectors against Postgres chunks.

    Returns counters (e.g. ``missing_vectors``, ``stale_vectors``,
    ``orphan_vectors``). Orphan vectors are harmless; missing/stale ones alert.
    """
    # Lazy import (mirrors ingest/delete): the impl pulls qdrant_client.
    from app.rag.reconcile import reconcile as _impl

    return await _impl(project_id=project_id, session_factory=session_factory)


async def reindex_document(
    *,
    document_id: str,
    project_id: str,
    file_path: str,
    session_factory: SessionFactory,
    file_store: object,
) -> IngestResult:
    """Force a full re-parse + re-embed of a document (``force_reindex=True``)."""
    # Lazy import (mirrors ingest): the pipeline pulls docling/openai/qdrant.
    from app.rag.pipeline import ingest_document as _impl

    return await _impl(
        document_id=document_id,
        project_id=project_id,
        file_path=file_path,
        session_factory=session_factory,
        file_store=file_store,
        force_reindex=True,
    )


# --------------------------------------------------------------------------- #
# Model capability helper (build sheet §8, P0 day 3-4)
# --------------------------------------------------------------------------- #
# Model families whose chat endpoint accepts image inputs. Used to decide whether
# figure crops can be sent to the LLM for captioning/vision grounding.
_VISION_CAPABLE_PREFIXES: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o4",
)


def chat_supports_vision(model: str) -> bool:
    """Return ``True`` if the given chat model can accept image inputs.

    DeepSeek chat models are text-only, so figure crops must fall back to the
    OpenAI vision-capable model. Matching is prefix-based and case-insensitive.
    """
    name = (model or "").strip().lower()
    return any(name.startswith(prefix) for prefix in _VISION_CAPABLE_PREFIXES)


__all__ = [
    "IngestResult",
    "ProgressCb",
    "GenEvent",
    "ingest_document",
    "answer_stream",
    "delete_document",
    "delete_project",
    "reconcile",
    "reindex_document",
    "chat_supports_vision",
]
