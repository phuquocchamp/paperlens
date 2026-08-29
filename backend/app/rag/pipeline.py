"""``ingest_document`` orchestration — the real ingest txn chain (Build Sheet §4).

    txn1  documents -> PROCESSING           (OWNED BY THE ARQ TASK, not here)
          parse + chunk                     (in memory, no txn)
    txn2  INSERT chunks, figures, tables     (idempotent: delete doc rows first)
          embed batches + Qdrant upsert      (no txn; delete points by doc first)
    txn3  UPDATE chunks SET is_embedded=true (only the ids actually upserted —
                                              table-parent rows stay false, the
                                              reconcile discriminator)
    txn4  documents -> TEXT_READY            (OWNED BY THE ARQ TASK)

This module NEVER reads or writes the ``documents`` table; progress is surfaced
only through the injected ``progress`` callback (each call is the task's own short
committed txn). Idempotent by ``document_id``: Postgres rows and Qdrant points for
the document are deleted before re-insert.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, update

from app.db.models import Chunk, Figure, Table
from app.domain.status import IngestStage
from app.rag import IngestResult, ProgressCb
from app.rag.chunker import RawChunk, chunk_document, count_tokens, make_embed_text
from app.rag.embed import Embedder
from app.rag.errors import VectorStoreError
from app.rag.parse import (
    ParsedDoc,
    figure_label,
    parse_pdf,
    save_figure_crops,
)
from app.rag.qdrant_store import (
    build_payload,
    count_by_document,
    delete_by_document,
    ensure_collection,
    get_client,
    upsert_points,
)

_MODALITY = {
    "text": "text",
    "table_child": "table",
    "table_parent": "table",
    "fig_caption": "figure",
    "figure": "figure",
}


@dataclass
class _PendingChunk:
    """A chunk row plus the embed_text used to vectorise it."""

    id: uuid.UUID
    chunk_index: int
    content: str
    embed_text: str
    content_hash: str
    section_path: str | None
    heading_levels: list | None
    page_start: int | None
    page_end: int | None
    chunk_type: str
    figure_id: uuid.UUID | None
    table_id: uuid.UUID | None


def _table_surface(tbl) -> str:
    """Retrieval surface for a table: caption + flat/HTML body (§3 router)."""
    parts: list[str] = []
    if tbl.caption:
        parts.append(tbl.caption)
    body = tbl.markdown_flat or tbl.html
    if body:
        parts.append(body)
    return "\n".join(parts).strip()


async def _progress(progress: ProgressCb | None, stage: IngestStage, frac: float) -> None:
    if progress is not None:
        await progress(stage.value, frac)


async def ingest_document(
    *,
    document_id: str,
    project_id: str,
    file_path: str,
    session_factory,
    file_store: object,
    progress: ProgressCb | None = None,
    max_pages: int | None = None,
    force_reindex: bool = False,
) -> IngestResult:
    """See ``app.rag.ingest_document`` for the contract docstring."""
    doc_id = uuid.UUID(str(document_id))
    proj_id = uuid.UUID(str(project_id))

    # ---- parse (in memory) ---------------------------------------------- #
    await _progress(progress, IngestStage.PARSING, 0.05)
    parsed: ParsedDoc = await parse_pdf(file_path, max_pages=max_pages)
    await _progress(progress, IngestStage.PARSING, 0.35)

    # ---- chunk (in memory) ---------------------------------------------- #
    await _progress(progress, IngestStage.CHUNKING, 0.4)
    text_chunks: list[RawChunk] = chunk_document(parsed.document)

    figures_rows, tables_rows, pending, crop_jobs = _build_rows(
        parsed, text_chunks, proj_id, doc_id
    )

    # ---- crop figures (blocking PIL — run in executor) ------------------ #
    # Saves {DATA_DIR}/figures/{document_id}/{figure_id}.png and back-fills
    # image_path on the Figure rows. Junk crops (tiny/thin/empty) are dropped;
    # those rows keep image_path=None and are never emitted for display.
    if crop_jobs and file_store is not None:
        import asyncio

        dest_dir = str(file_store.figures_dir(str(doc_id)))
        loop = asyncio.get_running_loop()
        crop_map = await loop.run_in_executor(
            None, save_figure_crops, parsed.document, crop_jobs, dest_dir
        )
        for fig in figures_rows:
            path = crop_map.get(str(fig.id))
            if path:
                fig.image_path = path
    await _progress(progress, IngestStage.CHUNKING, 0.5)

    # ---- txn2a: replace derived rows (idempotent) ----------------------- #
    async with session_factory() as session:
        async with session.begin():
            # FK-safe delete order (no ON DELETE CASCADE in the schema).
            await session.execute(
                delete(Chunk).where(Chunk.document_id == doc_id)
            )
            await session.execute(
                delete(Figure).where(Figure.document_id == doc_id)
            )
            await session.execute(
                delete(Table).where(Table.document_id == doc_id)
            )
            # Insert parents (tables, figures) and FLUSH before children: chunks
            # carry FKs to tables/figures but no ORM relationship, so SQLAlchemy
            # cannot dependency-sort them — flush parents first explicitly.
            session.add_all(tables_rows)
            session.add_all(figures_rows)
            await session.flush()
            session.add_all(
                [
                    Chunk(
                        id=pc.id,
                        document_id=doc_id,
                        project_id=proj_id,
                        chunk_index=pc.chunk_index,
                        content=pc.content,
                        embed_text=pc.embed_text,
                        content_hash=pc.content_hash,
                        section_path=pc.section_path,
                        heading_levels=pc.heading_levels,
                        page_start=pc.page_start,
                        page_end=pc.page_end,
                        chunk_type=pc.chunk_type,
                        is_embedded=False,
                        figure_id=pc.figure_id,
                        table_id=pc.table_id,
                    )
                    for pc in pending
                ]
            )

    # ---- embed + upsert (no txn) ---------------------------------------- #
    await _progress(progress, IngestStage.EMBEDDING, 0.6)
    embedder = Embedder()
    try:
        result = await embedder.embed([pc.embed_text for pc in pending])
    finally:
        await embedder.aclose()

    await _progress(progress, IngestStage.INDEXING, 0.85)
    client = get_client()
    try:
        await ensure_collection(client)
        # Idempotency: drop any prior points for this document first.
        await delete_by_document(client, str(doc_id))

        ingest_generation = 0  # bumped by reindex flow later
        payloads = [
            build_payload(
                project_id=str(proj_id),
                document_id=str(doc_id),
                modality=_MODALITY.get(pc.chunk_type, "text"),
                chunk_type=pc.chunk_type,
                page=pc.page_start,
                section_path=pc.section_path,
                chunk_index=pc.chunk_index,
                content_hash=pc.content_hash,
                ingest_generation=ingest_generation,
            )
            for pc in pending
        ]
        try:
            await upsert_points(
                client,
                ids=[str(pc.id) for pc in pending],
                dense=result.dense,
                sparse=result.sparse,
                payloads=payloads,
            )
            point_count = await count_by_document(client, str(doc_id))
        except Exception as exc:
            raise VectorStoreError(f"qdrant upsert failed: {exc}") from exc
    finally:
        await client.close()

    # ---- txn3: mark embedded (only the ids we upserted) ----------------- #
    embedded_ids = [pc.id for pc in pending]
    async with session_factory() as session:
        async with session.begin():
            if embedded_ids:
                await session.execute(
                    update(Chunk)
                    .where(Chunk.id.in_(embedded_ids))
                    .values(is_embedded=True)
                )
    await _progress(progress, IngestStage.INDEXING, 1.0)

    chunk_tokens = [count_tokens(pc.content) for pc in pending]
    avg_tokens = sum(chunk_tokens) / len(chunk_tokens) if chunk_tokens else 0.0
    return IngestResult(
        document_id=str(doc_id),
        chunk_count=len(pending),
        figure_count=len(figures_rows),
        table_count=len(tables_rows),
        embedded_count=point_count,
        reindexed=force_reindex,
        billed_tokens=result.billed_tokens,
        cached_count=result.cached_count,
        cost_usd=result.cost_usd,
        avg_chunk_tokens=avg_tokens,
    )


def _build_rows(
    parsed: ParsedDoc,
    text_chunks: list[RawChunk],
    proj_id: uuid.UUID,
    doc_id: uuid.UUID,
) -> tuple[list[Figure], list[Table], list[_PendingChunk], list[tuple[str, str]]]:
    """Turn parse + chunk output into ORM rows + pending embeddable chunks.

    Also returns ``crop_jobs`` — ``(figure_id, self_ref)`` pairs the caller feeds
    to :func:`save_figure_crops` (blocking PIL work, done in an executor).
    """
    pending: list[_PendingChunk] = []
    crop_jobs: list[tuple[str, str]] = []
    idx = 0

    # Text chunks.
    for ch in text_chunks:
        pending.append(
            _PendingChunk(
                id=uuid.uuid4(),
                chunk_index=idx,
                content=ch.content,
                embed_text=ch.embed_text,
                content_hash=ch.content_hash,
                section_path=ch.section_path,
                heading_levels=ch.heading_levels,
                page_start=ch.page_start,
                page_end=ch.page_end,
                chunk_type="text",
                figure_id=None,
                table_id=None,
            )
        )
        idx += 1

    # Tables -> Table row + one table_child chunk (retrieval surface).
    tables_rows: list[Table] = []
    for t in parsed.tables:
        tid = uuid.uuid4()
        tables_rows.append(
            Table(
                id=tid,
                document_id=doc_id,
                label=t.label,
                caption=t.caption,
                html=t.html,
                markdown_flat=t.markdown_flat,
                structure_kind=t.structure_kind,
                n_rows=t.n_rows,
                n_cols=t.n_cols,
                page=t.page,
                bbox=t.bbox,
            )
        )
        surface = _table_surface(t)
        if surface:
            import hashlib

            pending.append(
                _PendingChunk(
                    id=uuid.uuid4(),
                    chunk_index=idx,
                    content=surface,
                    embed_text=make_embed_text(surface, t.section_path),
                    content_hash=hashlib.sha256(surface.encode()).hexdigest(),
                    section_path=t.section_path,
                    heading_levels=None,
                    page_start=t.page,
                    page_end=t.page,
                    chunk_type="table_child",
                    figure_id=None,
                    table_id=tid,
                )
            )
            idx += 1

    # Figures -> Figure row + shadow chunk (chunk_type='figure') for join-back.
    figures_rows: list[Figure] = []
    for f in parsed.figures:
        fid = uuid.uuid4()
        figures_rows.append(
            Figure(
                id=fid,
                document_id=doc_id,
                label=figure_label(f.caption, f.label),
                caption=f.caption,
                vlm_summary=None,
                image_path=f.image_path,
                page=f.page,
                bbox=f.bbox,
                vlm_status=f.vlm_status,
            )
        )
        # Crop job: (figure_id, docling self_ref). Image is saved after row build.
        crop_jobs.append((str(fid), f.self_ref))
        surface = (f.caption or f.label or "").strip()
        if surface:
            import hashlib

            pending.append(
                _PendingChunk(
                    id=uuid.uuid4(),
                    chunk_index=idx,
                    content=surface,
                    embed_text=make_embed_text(surface, f.section_path),
                    content_hash=hashlib.sha256(surface.encode()).hexdigest(),
                    section_path=f.section_path,
                    heading_levels=None,
                    page_start=f.page,
                    page_end=f.page,
                    chunk_type="figure",
                    figure_id=fid,
                    table_id=None,
                )
            )
            idx += 1

    return figures_rows, tables_rows, pending, crop_jobs
