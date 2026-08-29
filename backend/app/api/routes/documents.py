"""Project + document ingestion routes (Build Sheet §P1, week 1).

* ``POST /v1/projects``                          create a project
* ``GET  /v1/projects``                          list projects
* ``POST /v1/projects/{pid}/documents``          upload a PDF -> enqueue ingest
* ``GET  /v1/projects/{pid}/documents``          list documents (status polling)
* ``GET  /v1/projects/{pid}/documents/{id}``     one document (status polling)

Upload flow: pre-check ``page_count`` with fitz, ``check_free_space(size*3)``,
compute ``file_sha256`` and dedup (409 on the ``UNIQUE(project_id, file_sha256)
WHERE deleted_at IS NULL`` index), persist the PDF to the uploads volume, INSERT
the ``documents`` row (UPLOADED -> QUEUED), then enqueue the ARQ ingest task.

Heavy imports (fitz, arq) are lazy so this router stays import-cheap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import rag
from app.api.limits import check_cost_cap, upload_rate_limit
from app.config import settings
from app.db.models import Document, Project
from app.db.session import get_session, session_factory
from app.domain.status import DocumentStatus, transition
from app.rag.storage import LocalFileStore, sha256_bytes
from app.schemas.documents import (
    DocumentOut,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(prefix="/v1", tags=["documents"])


# --------------------------------------------------------------------------- #
# ARQ enqueue helper
# --------------------------------------------------------------------------- #
async def _enqueue_ingest(document_id: str, project_id: str, file_path: str) -> str | None:
    """Enqueue the ingest task; return the ARQ job id (or None if unavailable)."""
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        job = await pool.enqueue_job(
            "ingest_document_task",
            document_id=document_id,
            project_id=project_id,
            file_path=file_path,
        )
        return job.job_id if job else None
    finally:
        await pool.aclose()


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> ProjectOut:
    project = Project(name=body.name, description=body.description)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return ProjectOut(
        id=str(project.id),
        name=project.name,
        description=project.description,
        created_at=project.created_at,
    )


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProjectOut]:
    rows = (await session.execute(select(Project).order_by(Project.created_at.desc()))).scalars()
    return [
        ProjectOut(
            id=str(p.id), name=p.name, description=p.description, created_at=p.created_at
        )
        for p in rows
    ]


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
def _to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=str(doc.id),
        project_id=str(doc.project_id),
        title=doc.title,
        filename=doc.filename,
        file_sha256=doc.file_sha256,
        page_count=doc.page_count,
        status=doc.status,
        stage=doc.stage,
        progress=doc.progress,
        error_code=doc.error_code,
        error_message=doc.error_message,
        chunk_count=doc.chunk_count,
        figure_count=doc.figure_count,
        created_at=doc.created_at,
    )


def _page_count(data: bytes) -> int:
    """Pre-check page count with PyMuPDF; raise 400 if the file is not a PDF."""
    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as d:
            return d.page_count
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid PDF: {exc}") from exc


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentOut,
    status_code=201,
    # Priority #1 (Build Sheet §2): per-IP upload rate limit + daily cost-cap
    # pre-check (ingest embeds text and thus spends credits). Rate limit first so
    # an abusive IP gets 429, not 503.
    dependencies=[Depends(upload_rate_limit), Depends(check_cost_cap)],
)
async def upload_document(
    project_id: str,
    file: Annotated[UploadFile, File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentOut:
    try:
        proj_uuid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project_id") from exc

    project = await session.get(Project, proj_uuid)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")

    # Pre-check page count + hard page cap.
    pages = _page_count(data)
    if pages > settings.max_pages:
        raise HTTPException(
            status_code=413,
            detail=f"document has {pages} pages; cap is {settings.max_pages}",
        )

    # Free-space guard (raw + parsed artifacts + crops headroom).
    store = LocalFileStore()
    if not store.check_free_space(len(data) * 3):
        raise HTTPException(status_code=507, detail="insufficient storage")

    # Dedup on (project_id, file_sha256) among live documents.
    file_hash = sha256_bytes(data)
    existing = (
        await session.execute(
            select(Document).where(
                Document.project_id == proj_uuid,
                Document.file_sha256 == file_hash,
                Document.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "document already exists in this project",
                "existing_document_id": str(existing.id),
            },
        )

    # INSERT the document row (UPLOADED), persist the file, then -> QUEUED.
    doc = Document(
        project_id=proj_uuid,
        title=(file.filename or "document.pdf").rsplit(".", 1)[0],
        filename=file.filename or "document.pdf",
        file_sha256=file_hash,
        page_count=pages,
        status=DocumentStatus.UPLOADED.value,
    )
    session.add(doc)
    await session.flush()  # assign doc.id

    saved = store.save_upload(str(proj_uuid), str(doc.id), data)

    doc.status = transition(DocumentStatus.UPLOADED, DocumentStatus.QUEUED).value
    await session.commit()
    await session.refresh(doc)

    await _enqueue_ingest(str(doc.id), str(proj_uuid), str(saved))
    return _to_out(doc)


@router.get("/projects/{project_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    project_id: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[DocumentOut]:
    try:
        proj_uuid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project_id") from exc
    rows = (
        await session.execute(
            select(Document)
            .where(Document.project_id == proj_uuid, Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
        )
    ).scalars()
    return [_to_out(d) for d in rows]


@router.get(
    "/projects/{project_id}/documents/{document_id}", response_model=DocumentOut
)
async def get_document(
    project_id: str,
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentOut:
    try:
        proj_uuid = uuid.UUID(project_id)
        doc_uuid = uuid.UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid id") from exc
    doc = await session.get(Document, doc_uuid)
    if doc is None or doc.project_id != proj_uuid or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="document not found")
    return _to_out(doc)


@router.delete(
    "/projects/{project_id}/documents/{document_id}", status_code=204
)
async def delete_document(
    project_id: str,
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Purge a document's vectors/rows, then soft-delete the documents row.

    Idempotent: an already-deleted (or unknown) document returns 204. The purge
    (Qdrant points + chunks/figures/tables/chunk_figures + files) is owned by
    ``rag.delete_document``; this route owns only the ``documents`` soft delete
    (``deleted_at`` + status -> DELETED), matching the module rule that rag/
    never writes the ``documents`` table.
    """
    try:
        proj_uuid = uuid.UUID(project_id)
        doc_uuid = uuid.UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid id") from exc

    doc = await session.get(Document, doc_uuid)
    if doc is None or doc.project_id != proj_uuid:
        # Unknown document — idempotent no-op (nothing to orphan).
        return Response(status_code=204)

    # Purge vectors + derived rows + files (idempotent). Uses its own sessions.
    await rag.delete_document(
        document_id=str(doc_uuid),
        project_id=str(proj_uuid),
        session_factory=session_factory,
        file_store=LocalFileStore(),
    )

    # Soft-delete the documents row (any -> DELETED is always legal, incl.
    # DELETED -> DELETED, so re-deleting is a no-op).
    if doc.deleted_at is None:
        doc.status = transition(
            DocumentStatus(doc.status), DocumentStatus.DELETED
        ).value
        doc.deleted_at = datetime.now(UTC)
        doc.stage = None
        await session.commit()

    return Response(status_code=204)


@router.post(
    "/projects/{project_id}/documents/{document_id}/retry",
    response_model=DocumentOut,
)
async def retry_document(
    project_id: str,
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentOut:
    """Re-queue a FAILED document for ingestion.

    Only FAILED is retryable (state machine: FAILED -> QUEUED). REJECTED is
    terminal-no-retry (409). A document that is still queued/processing or is
    already ready cannot be retried (409). The uploaded PDF path is
    reconstructed deterministically from the file store.
    """
    try:
        proj_uuid = uuid.UUID(project_id)
        doc_uuid = uuid.UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid id") from exc

    doc = await session.get(Document, doc_uuid)
    if doc is None or doc.project_id != proj_uuid or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="document not found")

    if doc.status == DocumentStatus.REJECTED.value:
        raise HTTPException(
            status_code=409, detail="rejected documents cannot be retried"
        )

    # Only FAILED is retryable. The state machine also permits UPLOADED -> QUEUED,
    # so gating on the transition table alone would wrongly re-enqueue an
    # already-queued upload; require FAILED explicitly. The transition() call
    # then just validates the (always-legal) FAILED -> QUEUED edge.
    if doc.status != DocumentStatus.FAILED.value:
        raise HTTPException(
            status_code=409,
            detail=f"document in status '{doc.status}' cannot be retried",
        )
    new_status = transition(
        DocumentStatus.FAILED, DocumentStatus.QUEUED
    ).value

    # Reconstruct the deterministic upload path; refuse if the file is gone
    # rather than enqueue a job that would immediately fail again.
    store = LocalFileStore()
    file_path = store.upload_path(str(proj_uuid), str(doc_uuid))
    if not file_path.exists():
        raise HTTPException(
            status_code=409,
            detail="source file is no longer available; re-upload the document",
        )

    doc.status = new_status
    doc.stage = None
    doc.progress = None
    doc.error_code = None
    doc.error_message = None
    await session.commit()
    await session.refresh(doc)

    await _enqueue_ingest(str(doc_uuid), str(proj_uuid), str(file_path))
    return _to_out(doc)
