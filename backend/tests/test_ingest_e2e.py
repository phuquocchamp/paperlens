"""REAL end-to-end ingestion proof against the LIVE services.

This test is NOT part of the normal ``pytest`` run: it needs network access (to
download an open-access PDF and call the OpenAI embeddings API) plus the live
Postgres / Qdrant / Redis stack. It is gated behind ``PAPERLENS_E2E=1`` with a
plain ``skipif`` (no custom marker — pyproject is pinned), so a bare ``pytest``
stays green at 52.

Run it with the host-facing service URLs::

    PAPERLENS_E2E=1 \
    DATABASE_URL="postgresql+asyncpg://paperlens:paperlens@127.0.0.1:5432/paperlens" \
    REDIS_URL="redis://127.0.0.1:6379/0" \
    QDRANT_URL="http://127.0.0.1:6333" \
    DATA_DIR="$PWD/.e2e-data" HF_HOME="$PWD/.e2e-data/models" \
    OPENAI_API_KEY=... \
    .venv/bin/python -m pytest tests/test_ingest_e2e.py -s

It asserts the document reaches TEXT_READY, chunks exist in Postgres with distinct
``content`` vs ``embed_text``, and Qdrant holds points for the document filter. It
prints the real billed-token count and embedding cost.
"""

from __future__ import annotations

import os
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PAPERLENS_E2E"),
    reason="live e2e ingestion; set PAPERLENS_E2E=1 with services + network",
)

PDF_URL = "https://arxiv.org/pdf/1503.02531"  # Hinton et al., 9 pages, born-digital


async def _cleanup(document_id: uuid.UUID, project_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.db.models import (
        Chunk,
        Document,
        Figure,
        IngestionJob,
        Project,
        Table,
    )
    from app.db.session import session_factory
    from app.rag.qdrant_store import delete_by_document, get_client

    client = get_client()
    try:
        await delete_by_document(client, str(document_id))
    except Exception:  # collection may not exist if ingest failed early
        pass
    finally:
        await client.close()

    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await session.execute(delete(Figure).where(Figure.document_id == document_id))
            await session.execute(delete(Table).where(Table.document_id == document_id))
            await session.execute(
                delete(IngestionJob).where(IngestionJob.document_id == document_id)
            )
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.execute(delete(Project).where(Project.id == project_id))


async def test_ingest_e2e(tmp_path):
    from sqlalchemy import func, select

    from app.db.models import Chunk, Document, Project
    from app.db.session import session_factory
    from app.domain.status import DocumentStatus
    from app.rag.qdrant_store import count_by_document, get_client
    from app.rag.storage import LocalFileStore, sha256_file
    from app.worker import ingest_document_task

    # --- fetch a small open-access PDF ----------------------------------- #
    pdf_path = tmp_path / "paper.pdf"
    urllib.request.urlretrieve(PDF_URL, pdf_path)  # noqa: S310 - trusted arXiv host
    assert pdf_path.stat().st_size > 10_000

    import pymupdf

    with pymupdf.open(pdf_path) as d:
        pages = d.page_count
        fitz_chars = sum(len(p.get_text()) for p in d)
    assert fitz_chars > 10_000  # born-digital ground truth for the completeness gate

    project_id = uuid.uuid4()
    document_id = uuid.uuid4()

    # --- seed project + document (status QUEUED, as the upload route would) - #
    store = LocalFileStore(str(tmp_path / "data"))
    saved = store.save_upload(str(project_id), str(document_id), pdf_path.read_bytes())

    async with session_factory() as session:
        async with session.begin():
            session.add(Project(id=project_id, name="E2E Test Project"))
            session.add(
                Document(
                    id=document_id,
                    project_id=project_id,
                    title="Distilling the Knowledge in a Neural Network",
                    filename="paper.pdf",
                    file_sha256=sha256_file(saved),
                    page_count=pages,
                    status=DocumentStatus.QUEUED.value,
                )
            )

    try:
        # --- run the REAL task in-process (real state machine + progress) --- #
        result = await ingest_document_task(
            ctx={},
            document_id=str(document_id),
            project_id=str(project_id),
            file_path=str(saved),
        )
        assert result["status"] == "text_ready", result

        # --- assert: document reached TEXT_READY --------------------------- #
        async with session_factory() as session:
            doc = await session.get(Document, document_id)
            assert doc.status == DocumentStatus.TEXT_READY.value
            assert doc.stage is None
            assert doc.progress == 100
            assert doc.chunk_count > 0

            # --- assert: chunks exist, content != embed_text --------------- #
            rows = (
                await session.execute(
                    select(Chunk).where(Chunk.document_id == document_id)
                )
            ).scalars().all()
            assert len(rows) > 0
            assert all(c.is_embedded for c in rows)
            sample = rows[0]
            assert sample.content and sample.embed_text
            assert sample.content != sample.embed_text
            assert sample.embed_text.startswith("[Paper]")
            assert sample.content_hash

            # Parse-completeness gate: a degraded/partial parse (the RapidOCR
            # nondeterminism finding) would slip past the 200-char/page text
            # yield gate. Assert we captured most of the body text vs fitz's
            # ground-truth char count. This is what turns a "5 chunks, green"
            # corrupted parse into a real failure.
            total_chunk_chars = sum(len(c.content) for c in rows)
            coverage = total_chunk_chars / fitz_chars
            assert coverage >= 0.6, (
                f"parse coverage {coverage:.0%} of fitz text "
                f"({total_chunk_chars}/{fitz_chars}); likely a partial parse"
            )
            n_headed = sum(1 for c in rows if c.heading_levels)
            assert n_headed > 0  # chunks under sections carry heading_levels

            n_chunks = (
                await session.execute(
                    select(func.count())
                    .select_from(Chunk)
                    .where(Chunk.document_id == document_id)
                )
            ).scalar_one()

        # --- assert: Qdrant has points for this document ------------------- #
        client = get_client()
        try:
            point_count = await count_by_document(client, str(document_id))
        finally:
            await client.close()
        assert point_count > 0
        assert point_count == n_chunks

        # --- report ------------------------------------------------------- #
        print("\n================ E2E INGEST RESULT ================")
        print(f"PDF               : {PDF_URL} ({pages} pages)")
        print(f"status            : {doc.status}")
        print(f"chunks (Postgres) : {n_chunks}")
        print(f"points (Qdrant)   : {point_count}")
        print(
            f"parse coverage    : {coverage:.0%} "
            f"({total_chunk_chars}/{fitz_chars} chars vs fitz)"
        )
        # Cost/token detail comes from the ingestion_jobs usage row.
        from app.db.models import IngestionJob

        async with session_factory() as session:
            job = (
                await session.execute(
                    select(IngestionJob)
                    .where(IngestionJob.document_id == document_id)
                    .order_by(IngestionJob.created_at.desc())
                )
            ).scalars().first()
        if job and job.usage:
            print(f"billed_tokens     : {job.usage.get('billed_tokens')}")
            print(f"cached_count      : {job.usage.get('cached_count')}")
            print(f"cost_usd          : ${job.usage.get('cost_usd')}")
            print(f"avg_chunk_tokens  : {job.usage.get('avg_chunk_tokens')}")
        if job and job.step_timings_ms:
            print(f"total_ms          : {job.step_timings_ms.get('total_ms')}")
        print("===================================================")
    finally:
        await _cleanup(document_id, project_id)


async def test_enqueue_smoke():
    """Verify ARQ enqueue works against live Redis (separate from the task run)."""
    from app.api.routes.documents import _enqueue_ingest

    job_id = await _enqueue_ingest(str(uuid.uuid4()), str(uuid.uuid4()), "/tmp/none.pdf")
    assert job_id is not None
