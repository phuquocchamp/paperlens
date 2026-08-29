"""``delete_document`` — purge one document's vectors and derived rows (M8).

Mirrors the idempotent delete-by-document discipline used by ``pipeline.py``'s
txn2a (delete children before re-insert), but as a standalone terminal purge:

    Qdrant : delete all points by ``document_id`` filter.
    Postgres (FK-safe order, no ON DELETE CASCADE in the schema):
        chunk_figures  (edges into chunks/figures)
        chunks
        figures
        tables
    Volume : optionally remove the uploaded PDF + the figure crop directory.

This module NEVER writes the ``documents`` table — the route owns the soft
delete of ``documents.deleted_at`` and the state-machine transition to DELETED
(same rule the ingest pipeline follows: rag/ never touches ``documents``).

Idempotent: deleting an already-purged document is a no-op (empty deletes, a
non-existent crop directory is ignored). Safe to call repeatedly.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.db.models import Chunk, ChunkFigure, Figure, Table
from app.rag.qdrant_store import delete_by_document, get_client


async def delete_document(
    *,
    document_id: str,
    project_id: str,
    session_factory,
    file_store: object | None = None,
) -> None:
    """See ``app.rag.delete_document`` for the contract docstring."""
    doc_id = uuid.UUID(str(document_id))

    # ---- Qdrant: drop every point for the document (no txn, network) ------ #
    client = get_client()
    try:
        await delete_by_document(client, str(doc_id))
    finally:
        await client.close()

    # ---- Postgres: delete derived rows in FK-safe order ------------------- #
    # No ON DELETE CASCADE in the schema, so children are removed explicitly.
    # chunk_figures edges reference chunks AND figures, so they go first.
    async with session_factory() as session:
        async with session.begin():
            child_chunk_ids = select(Chunk.id).where(Chunk.document_id == doc_id)
            child_figure_ids = select(Figure.id).where(Figure.document_id == doc_id)
            await session.execute(
                delete(ChunkFigure).where(
                    ChunkFigure.chunk_id.in_(child_chunk_ids)
                    | ChunkFigure.figure_id.in_(child_figure_ids)
                )
            )
            await session.execute(delete(Chunk).where(Chunk.document_id == doc_id))
            await session.execute(delete(Figure).where(Figure.document_id == doc_id))
            await session.execute(delete(Table).where(Table.document_id == doc_id))

    # ---- Volume: best-effort removal of the raw PDF + figure crops -------- #
    # Missing files/dirs are ignored (idempotent). Storage errors here must not
    # leave orphan vectors/rows behind — those are already gone above.
    if file_store is not None:
        _remove_files(file_store, project_id=str(project_id), document_id=str(doc_id))


def _remove_files(file_store, *, project_id: str, document_id: str) -> None:
    """Delete the uploaded PDF and the figure-crop directory (best effort)."""
    import shutil

    upload_path = getattr(file_store, "upload_path", None)
    if callable(upload_path):
        pdf = file_store.upload_path(project_id, document_id)
        try:
            pdf.unlink(missing_ok=True)
        except OSError:
            pass  # best effort — vectors/rows are already purged

    figures_dir = getattr(file_store, "figures_dir", None)
    if callable(figures_dir):
        crops = file_store.figures_dir(document_id)
        shutil.rmtree(crops, ignore_errors=True)
