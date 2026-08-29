"""Deterministic eval corpus: a dedicated project + two open-access papers.

Idempotent by construction:

* The project id and each document id are DETERMINISTIC (``uuid5``), so re-runs
  reuse the same rows instead of creating duplicates (which would also trip the
  ``UNIQUE (project_id, file_sha256) WHERE deleted_at IS NULL`` index).
* A paper is (re)ingested only when it is not already ``TEXT_READY``/``FULL_READY``
  with points present in Qdrant. The underlying ``rag.ingest_document`` is itself
  idempotent (it deletes points + rows by ``document_id`` before upserting), so a
  forced re-run is always safe.

This module reuses the REAL ingest task (``app.worker.ingest_document_task``) so
the corpus is produced by exactly the production pipeline — nothing is faked.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

# Stable namespace so the same logical corpus always maps to the same UUIDs.
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://paperlens.local/eval")

# The dedicated eval project (never collides with app-created projects).
EVAL_PROJECT_ID = uuid.uuid5(_NS, "project")


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str

    @property
    def document_id(self) -> uuid.UUID:
        return uuid.uuid5(_NS, self.arxiv_id)

    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.arxiv_id}"


# Two deterministic, born-digital papers the pipeline handles well.
#   * 1503.02531  — Distilling the Knowledge in a Neural Network (numeric/conceptual)
#   * 2309.06180  — vLLM / PagedAttention (figures + a table)
PAPERS: tuple[Paper, ...] = (
    Paper("1503.02531", "Distilling the Knowledge in a Neural Network"),
    Paper(
        "2309.06180v1",
        "Efficient Memory Management for Large Language Model Serving with PagedAttention",
    ),
)

PAPER_BY_ID: dict[str, Paper] = {p.arxiv_id: p for p in PAPERS}


def _download(paper: Paper, dest_dir: Path) -> Path:
    """Fetch the PDF with ``curl -sL`` (idempotent: skip if already on disk)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{paper.arxiv_id}.pdf"
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    subprocess.run(
        ["curl", "-sL", "-o", str(dest), paper.pdf_url],
        check=True,
    )
    if dest.stat().st_size <= 10_000:
        raise RuntimeError(f"download of {paper.pdf_url} looks truncated")
    return dest


async def _points_for(document_id: uuid.UUID) -> int:
    from app.rag.qdrant_store import count_by_document, get_client

    client = get_client()
    try:
        return await count_by_document(client, str(document_id))
    except Exception:
        # Collection may not exist yet on a first run.
        return 0
    finally:
        await client.close()


async def _ensure_project() -> None:
    from app.db.models import Project
    from app.db.session import session_factory

    async with session_factory() as session:
        async with session.begin():
            existing = await session.get(Project, EVAL_PROJECT_ID)
            if existing is not None:
                return
            session.add(
                Project(
                    id=EVAL_PROJECT_ID,
                    name="PaperLens Eval (Tier-0)",
                    description="Golden-set corpus for the deterministic eval harness.",
                )
            )


async def _ensure_document_row(paper: Paper, saved_path: Path) -> str:
    """Insert (or reset) the ``documents`` row to QUEUED so the task can run.

    Returns the current status BEFORE any reset, so the caller can decide whether
    an ingest is needed.
    """
    from app.db.models import Document
    from app.db.session import session_factory
    from app.domain.status import DocumentStatus
    from app.rag.storage import sha256_file

    sha = sha256_file(saved_path)
    async with session_factory() as session:
        async with session.begin():
            doc = await session.get(Document, paper.document_id)
            if doc is None:
                session.add(
                    Document(
                        id=paper.document_id,
                        project_id=EVAL_PROJECT_ID,
                        title=paper.title,
                        filename=f"{paper.arxiv_id}.pdf",
                        file_sha256=sha,
                        status=DocumentStatus.QUEUED.value,
                    )
                )
                return DocumentStatus.QUEUED.value
            return doc.status


@dataclass
class IngestOutcome:
    arxiv_id: str
    document_id: str
    status: str          # "reused" | "ingested"
    chunk_count: int
    point_count: int


_READY = {"text_ready", "full_ready"}


async def ensure_corpus(
    data_dir: str, *, force: bool = False, verbose: bool = True
) -> list[IngestOutcome]:
    """Ensure both papers are ingested into the eval project. Idempotent.

    ``data_dir`` mirrors ``settings.data_dir`` — uploads are saved there via the
    same ``LocalFileStore`` the worker constructs with no argument, so figure
    crops land alongside them.
    """
    from app.rag.storage import LocalFileStore
    from app.worker import ingest_document_task

    store = LocalFileStore(data_dir)
    pdf_dir = Path(data_dir) / "eval_pdfs"

    await _ensure_project()

    outcomes: list[IngestOutcome] = []
    for paper in PAPERS:
        saved = store.upload_path(str(EVAL_PROJECT_ID), str(paper.document_id))
        if not saved.exists():
            downloaded = _download(paper, pdf_dir)
            store.save_upload(
                str(EVAL_PROJECT_ID),
                str(paper.document_id),
                downloaded.read_bytes(),
            )

        status = await _ensure_document_row(paper, saved)
        points = await _points_for(paper.document_id)

        if not force and status in _READY and points > 0:
            if verbose:
                print(
                    f"[corpus] {paper.arxiv_id}: reuse (status={status}, "
                    f"points={points})"
                )
            outcomes.append(
                IngestOutcome(
                    arxiv_id=paper.arxiv_id,
                    document_id=str(paper.document_id),
                    status="reused",
                    chunk_count=0,
                    point_count=points,
                )
            )
            continue

        if verbose:
            print(f"[corpus] {paper.arxiv_id}: ingesting (real pipeline)…")
        result = await ingest_document_task(
            ctx={},
            document_id=str(paper.document_id),
            project_id=str(EVAL_PROJECT_ID),
            file_path=str(saved),
        )
        if result.get("status") != "text_ready":
            raise RuntimeError(
                f"ingest of {paper.arxiv_id} did not reach text_ready: {result}"
            )
        points = await _points_for(paper.document_id)
        if verbose:
            print(
                f"[corpus] {paper.arxiv_id}: ingested "
                f"(chunks={result.get('chunks')}, points={points}, "
                f"final={result.get('final_status')})"
            )
        outcomes.append(
            IngestOutcome(
                arxiv_id=paper.arxiv_id,
                document_id=str(paper.document_id),
                status="ingested",
                chunk_count=int(result.get("chunks") or 0),
                point_count=points,
            )
        )
    return outcomes


async def corpus_media_counts() -> dict[str, dict[str, int]]:
    """Return per-paper figure(with-crop)/table counts (advisor point #5).

    Used to confirm the figure group has something real to test before trusting
    its numbers.
    """
    from sqlalchemy import text

    from app.db.session import session_factory

    out: dict[str, dict[str, int]] = {}
    async with session_factory() as session:
        for paper in PAPERS:
            fig = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM figures "
                        "WHERE document_id = CAST(:d AS uuid) "
                        "AND image_path IS NOT NULL"
                    ),
                    {"d": str(paper.document_id)},
                )
            ).scalar_one()
            tbl = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM tables "
                        "WHERE document_id = CAST(:d AS uuid)"
                    ),
                    {"d": str(paper.document_id)},
                )
            ).scalar_one()
            out[paper.arxiv_id] = {"figures_with_crop": int(fig), "tables": int(tbl)}
    return out


async def all_chunk_contents() -> list[str]:
    """Every chunk's canonical ``content`` for the eval project (quote checks)."""
    from sqlalchemy import select

    from app.db.models import Chunk
    from app.db.session import session_factory

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Chunk.content).where(Chunk.project_id == EVAL_PROJECT_ID)
            )
        ).scalars().all()
    return list(rows)


__all__ = [
    "EVAL_PROJECT_ID",
    "Paper",
    "PAPERS",
    "PAPER_BY_ID",
    "IngestOutcome",
    "ensure_corpus",
    "corpus_media_counts",
    "all_chunk_contents",
]
