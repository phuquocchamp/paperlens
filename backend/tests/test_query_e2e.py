"""REAL end-to-end proof of the query path against the LIVE stack.

Gated behind ``PAPERLENS_E2E=1`` (plain skipif, no custom marker — pyproject is
pinned), so a bare ``pytest`` stays green. Ingests 2 small open-access PDFs into a
fresh project via the REAL ingest task, then exercises ``rag.answer_stream``:

  * in-corpus question -> real citations whose quotes are VERBATIM substrings of
    real chunk content, inline ``[C#]`` markers reference only retrieved chunks,
    pages are present, TTFT + model are printed;
  * out-of-corpus question -> ABSTAINS with zero citations.

Run::

    PAPERLENS_E2E=1 \
    DATABASE_URL="postgresql+asyncpg://paperlens:paperlens@127.0.0.1:5432/paperlens" \
    REDIS_URL="redis://127.0.0.1:6379/0" QDRANT_URL="http://127.0.0.1:6333" \
    DATA_DIR="$PWD/.e2e-data" HF_HOME="$PWD/.e2e-data/models" \
    .venv/bin/python -m pytest tests/test_query_e2e.py -s -p no:cacheprovider
"""

from __future__ import annotations

import os
import time
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PAPERLENS_E2E"),
    reason="live e2e query; set PAPERLENS_E2E=1 with services + network + keys",
)

# Two small, born-digital open-access PDFs.
PAPERS = [
    ("1503.02531", "Distilling the Knowledge in a Neural Network"),
    ("1301.3781", "Efficient Estimation of Word Representations in Vector Space"),
]

IN_CORPUS_Q = "What is knowledge distillation and how does the soft-target temperature work?"
OFF_CORPUS_Q = "What is the best recipe for a traditional Neapolitan pizza dough?"


async def _ingest(tmp_path, project_id):
    from app.db.models import Document, Project
    from app.db.session import session_factory
    from app.domain.status import DocumentStatus
    from app.rag.storage import LocalFileStore, sha256_file
    from app.worker import ingest_document_task

    store = LocalFileStore(str(tmp_path / "data"))
    doc_ids = []
    async with session_factory() as session:
        async with session.begin():
            session.add(Project(id=project_id, name="Query E2E Project"))
    for arxiv_id, title in PAPERS:
        document_id = uuid.uuid4()
        pdf_path = tmp_path / f"{arxiv_id}.pdf"
        urllib.request.urlretrieve(  # noqa: S310 - trusted arXiv host
            f"https://arxiv.org/pdf/{arxiv_id}", pdf_path
        )
        saved = store.save_upload(
            str(project_id), str(document_id), pdf_path.read_bytes()
        )
        async with session_factory() as session:
            async with session.begin():
                session.add(
                    Document(
                        id=document_id,
                        project_id=project_id,
                        title=title,
                        filename=f"{arxiv_id}.pdf",
                        file_sha256=sha256_file(saved),
                        status=DocumentStatus.QUEUED.value,
                    )
                )
        result = await ingest_document_task(
            ctx={},
            document_id=str(document_id),
            project_id=str(project_id),
            file_path=str(saved),
        )
        assert result["status"] == "text_ready", result
        doc_ids.append(document_id)
        print(f"[ingest] {arxiv_id} '{title}' -> text_ready")
    return doc_ids


async def _cleanup(project_id, doc_ids):
    from sqlalchemy import delete

    from app.db.models import Chunk, Document, Figure, IngestionJob, Project, Table
    from app.db.session import session_factory
    from app.rag.qdrant_store import delete_by_document, get_client

    client = get_client()
    try:
        for d in doc_ids:
            try:
                await delete_by_document(client, str(d))
            except Exception:
                pass
    finally:
        await client.close()
    async with session_factory() as session:
        async with session.begin():
            for d in doc_ids:
                await session.execute(delete(Chunk).where(Chunk.document_id == d))
                await session.execute(delete(Figure).where(Figure.document_id == d))
                await session.execute(delete(Table).where(Table.document_id == d))
                await session.execute(
                    delete(IngestionJob).where(IngestionJob.document_id == d)
                )
                await session.execute(delete(Document).where(Document.id == d))
            await session.execute(delete(Project).where(Project.id == project_id))


async def _collect(events):
    """Drain answer_stream, timing TTFT to the first text-delta."""
    from app.domain.events import (
        CitationEvent,
        FinishEvent,
        TextDeltaEvent,
    )

    t0 = time.perf_counter()
    ttft = None
    citations_pending = {}
    citations_verified = {}
    text = []
    finish = None
    async for ev in events:
        if isinstance(ev, CitationEvent):
            if ev.verify_status.value == "pending":
                citations_pending[ev.marker] = ev
            else:
                citations_verified[ev.marker] = ev
        elif isinstance(ev, TextDeltaEvent):
            if ttft is None:
                ttft = time.perf_counter() - t0
            text.append(ev.delta)
        elif isinstance(ev, FinishEvent):
            finish = ev
    return {
        "ttft": ttft,
        "pending": citations_pending,
        "verified": citations_verified,
        "text": "".join(text),
        "finish": finish,
    }


async def test_query_e2e(tmp_path):
    import re

    from sqlalchemy import select

    from app.config import settings
    from app.db.models import Chunk
    from app.db.session import session_factory
    from app.rag import answer_stream
    from app.rag.generate import ABSTAIN_TEXT, ACTUAL_MODEL
    from app.rag.retrieval import retrieve

    project_id = uuid.uuid4()
    doc_ids = await _ingest(tmp_path, project_id)
    try:
        # --- all chunk contents for verbatim-quote verification ------------- #
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Chunk.content).where(Chunk.project_id == project_id)
                )
            ).scalars().all()
        all_contents = list(rows)

        # ================= IN-CORPUS ================= #
        # Report the raw max dense cosine (calibration evidence for the gate).
        r_in = await retrieve(
            project_id=str(project_id),
            question=IN_CORPUS_Q,
            session_factory=session_factory,
        )
        res = await _collect(
            answer_stream(
                project_id=str(project_id),
                question=IN_CORPUS_Q,
                history=[],
                session_factory=session_factory,
            )
        )
        model_used = ACTUAL_MODEL.get()

        assert res["pending"], "in-corpus question produced no citations"
        # Every displayed quote is a verbatim substring of a real chunk.
        for marker, cit in res["pending"].items():
            assert any(cit.quote in c for c in all_contents), (
                f"citation {marker} quote is not a verbatim chunk substring"
            )
            assert cit.page is not None
        # Inline [C#] markers reference ONLY retrieved chunks.
        used = set()
        for m in re.finditer(settings.marker_re, res["text"]):
            for tok in m.group(1).split(","):
                used.add(tok.strip())
        assert used, "answer cited nothing inline"
        assert used <= set(res["pending"].keys()), (
            f"answer used anchors not retrieved: {used - set(res['pending'])}"
        )
        # Verified re-emission only for used anchors.
        assert set(res["verified"].keys()) <= used

        print("\n================ QUERY E2E PROOF ================")
        print(f"model_used         : {model_used}")
        print(f"TTFT               : {res['ttft'] * 1000:.0f} ms")
        print(f"in-corpus Q        : {IN_CORPUS_Q}")
        print(f"max_dense_cosine   : {r_in.max_dense_cosine:.4f}  (gate=0.28)")
        print(f"retrieved/hydrated : {r_in.retrieved_count}/{r_in.hydrated_count}")
        print(f"citations emitted  : {len(res['pending'])}  used inline: {sorted(used)}")
        example = res["pending"][sorted(used)[0]]
        print("--- example cited answer ---")
        print(res["text"][:600])
        print("--- example citation (verbatim, server-dereferenced) ---")
        print(f"  {example.marker}  p.{example.page}  {example.document_title}")
        print(f"  verify_status={res['verified'].get(example.marker).verify_status.value if example.marker in res['verified'] else 'n/a'}")
        print(f"  quote: {example.quote[:240]}")

        # ================= OUT-OF-CORPUS ================= #
        r_off = await retrieve(
            project_id=str(project_id),
            question=OFF_CORPUS_Q,
            session_factory=session_factory,
        )
        res_off = await _collect(
            answer_stream(
                project_id=str(project_id),
                question=OFF_CORPUS_Q,
                history=[],
                session_factory=session_factory,
            )
        )
        assert not res_off["pending"], "out-of-corpus MUST NOT cite"
        assert not res_off["verified"]
        assert ABSTAIN_TEXT[:30] in res_off["text"], res_off["text"][:200]
        print("\n--- out-of-corpus (abstain) ---")
        print(f"off-corpus Q       : {OFF_CORPUS_Q}")
        print(f"max_dense_cosine   : {r_off.max_dense_cosine:.4f}  (gate=0.28)")
        print(f"answer             : {res_off['text'][:160]}")
        print(f"citations          : {len(res_off['pending'])}")
        print("================================================")
    finally:
        await _cleanup(project_id, doc_ids)
