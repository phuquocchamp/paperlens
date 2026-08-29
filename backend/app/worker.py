"""ARQ worker entrypoint — real ingestion task (Build Sheet §4).

The worker is the batch half of the modular monolith: it drains the ARQ ingestion
queue on Redis while the API serves the latency-sensitive query path. Both run from
the same image (see ``backend/Dockerfile``); this module is the ``paperlens-worker``
console-script target.

``ingest_document_task`` owns the ``documents`` state machine (txn1 -> PROCESSING,
txn4 -> TEXT_READY) and maps ``rag.ingest_document``'s typed exceptions to it:

    ParseError | TooLarge | Blocked        -> REJECTED, task RETURNS (no retry)
    Embedding | VectorStore | Storage      -> FAILED,   task RAISES (ARQ retries)
    asyncio.CancelledError                 -> QUEUED,   re-raised (re-queue)

Each progress callback is its own short, committed transaction so polling shows
real movement instead of 0% -> 100%.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select, update

from app.config import settings
from app.db.models import Document, IngestionJob, Project
from app.db.session import session_factory
from app.domain.status import DocumentStatus, IllegalTransition, transition
from app.logging_config import configure_logging

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Documents state-machine helpers (short committed txns)
# --------------------------------------------------------------------------- #
async def _load_status(doc_id: uuid.UUID) -> str | None:
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        return doc.status if doc else None


async def _load_doc_state(doc_id: uuid.UUID) -> tuple[str, datetime | None] | None:
    """Return ``(status, deleted_at)`` for the delete-race guard, or None.

    Read together, immediately before a status write, so a soft-delete that
    landed mid-PROCESSING is seen. ``deleted_at`` is checked in addition to the
    status because the route may stamp ``deleted_at`` and the DELETED status in
    separate statements — either signal means "do not write / stop".
    """
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            return None
        return doc.status, doc.deleted_at


def _is_deleted(state: tuple[str, datetime | None] | None) -> bool:
    """True iff the row is gone or soft-deleted (status DELETED or deleted_at set)."""
    if state is None:
        return True  # row vanished — treat as deleted, never resurrect
    status, deleted_at = state
    return deleted_at is not None or status == DocumentStatus.DELETED.value


async def _set_fields(doc_id: uuid.UUID, **fields) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(Document).where(Document.id == doc_id).values(**fields)
            )


def _guarded(current: str, target: DocumentStatus) -> str:
    """Return the validated target value; fall back to target on unknown current."""
    try:
        return transition(DocumentStatus(current), target).value
    except (IllegalTransition, ValueError):
        return target.value


async def _make_progress(doc_id: uuid.UUID):
    async def progress(stage: str, fraction: float) -> None:
        pct = max(0, min(100, int(round(fraction * 100))))
        await _set_fields(doc_id, stage=stage, progress=pct)

    return progress


# --------------------------------------------------------------------------- #
# The task
# --------------------------------------------------------------------------- #
async def ingest_document_task(
    ctx: dict, *, document_id: str, project_id: str, file_path: str
) -> dict:
    """Run the full ingest pipeline for one document."""
    from app.rag import ingest_document
    from app.rag.errors import FAILED_ERRORS, REJECTED_ERRORS, IngestError
    from app.rag.storage import LocalFileStore

    doc_id = uuid.UUID(str(document_id))
    t0 = time.monotonic()

    # ---- txn1: -> PROCESSING -------------------------------------------- #
    current = await _load_status(doc_id)
    if current is None:
        log.warning("ingest.missing_document", document_id=document_id)
        return {"status": "missing"}
    await _set_fields(
        doc_id,
        status=_guarded(current, DocumentStatus.PROCESSING),
        stage="parsing",
        progress=0,
        started_at=datetime.now(UTC),
        error_code=None,
        error_message=None,
    )

    progress = await _make_progress(doc_id)

    try:
        result = await ingest_document(
            document_id=str(doc_id),
            project_id=str(project_id),
            file_path=file_path,
            session_factory=session_factory,
            file_store=LocalFileStore(),
            progress=progress,
        )
    except asyncio.CancelledError:
        # Never swallow cancellation — re-queue the document and propagate.
        cur = await _load_status(doc_id)
        if cur:
            await _set_fields(
                doc_id, status=_guarded(cur, DocumentStatus.QUEUED), stage=None
            )
        raise
    except REJECTED_ERRORS as exc:
        code = getattr(exc, "code", "REJECTED")
        cur = await _load_status(doc_id)
        await _set_fields(
            doc_id,
            status=_guarded(cur or "processing", DocumentStatus.REJECTED),
            stage=None,
            error_code=code,
            error_message=str(exc),
        )
        await _record_job(doc_id, failed_step=code, ok=False)
        log.info("ingest.rejected", document_id=document_id, code=code)
        return {"status": "rejected", "code": code}  # RETURN -> no ARQ retry
    except Exception as exc:  # FAILED_ERRORS + any unexpected error -> retryable
        _ = FAILED_ERRORS  # documented mapping; all land here as FAILED
        code = exc.code if isinstance(exc, IngestError) else "INGEST_FAILED"
        cur = await _load_status(doc_id)
        await _set_fields(
            doc_id,
            status=_guarded(cur or "processing", DocumentStatus.FAILED),
            stage=None,
            error_code=code,
            error_message=str(exc),
        )
        await _record_job(doc_id, failed_step=code, ok=False)
        log.warning("ingest.failed", document_id=document_id, code=code, error=str(exc))
        raise  # RAISE -> ARQ retries

    # ---- txn4: -> TEXT_READY -------------------------------------------- #
    # Delete-race guard: the parse/embed window is long enough for a soft-delete
    # to land while we were off doing network work. Re-read status AND deleted_at
    # right before the write — _guarded's fallback would otherwise happily write
    # deleted -> text_ready and resurrect the document. If it is gone, skip the
    # write AND stop further processing (no captioning).
    state = await _load_doc_state(doc_id)
    if _is_deleted(state):
        log.info("ingest.deleted_race_skip", document_id=document_id, at="text_ready")
        return {"status": "deleted"}
    cur = state[0]
    await _set_fields(
        doc_id,
        status=_guarded(cur, DocumentStatus.TEXT_READY),
        stage=None,
        progress=100,
        chunk_count=result.chunk_count,
        figure_count=result.figure_count,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    await _record_job(
        doc_id,
        ok=True,
        timings={"total_ms": elapsed_ms},
        usage={
            "billed_tokens": result.billed_tokens,
            "cached_count": result.cached_count,
            "cost_usd": round(result.cost_usd, 6),
            "chunk_count": result.chunk_count,
            "point_count": result.embedded_count,
            "avg_chunk_tokens": round(result.avg_chunk_tokens, 1),
        },
    )
    log.info(
        "ingest.text_ready",
        document_id=document_id,
        chunks=result.chunk_count,
        points=result.embedded_count,
        cost_usd=round(result.cost_usd, 6),
        elapsed_ms=elapsed_ms,
    )

    # ---- captioning stage: TEXT_READY -> FULL_READY (background, non-fatal) - #
    # The document is already queryable; captioning is a best-effort enrichment.
    # A captioning failure must NOT fail the document — per-figure errors are
    # recorded as vlm_status='failed' inside caption_figures and we still advance
    # to FULL_READY. Only CancelledError is allowed to propagate (re-queue).
    cap = await _caption_stage(
        doc_id, document_id, base_chunk_count=result.chunk_count
    )

    return {
        # Keep "status" as text_ready for callers that assert the ingest result;
        # final_status carries the captioning outcome (FULL_READY on success).
        "status": "text_ready",
        "final_status": cap["final_status"],
        "chunks": result.chunk_count + cap["new_chunks"],
        "figures_captioned": cap["captioned"],
    }


async def _caption_stage(
    doc_id: uuid.UUID, document_id: str, *, base_chunk_count: int
) -> dict:
    """Run VLM captioning, then transition TEXT_READY -> FULL_READY.

    Non-fatal by contract. Captioning may add shadow chunks for figures that had
    no Docling caption, so ``documents.chunk_count`` is corrected at the
    FULL_READY write (the TEXT_READY write only knew the pre-caption count).
    Returns ``{"final_status", "captioned", "new_chunks"}``.
    """
    from app.rag.caption import caption_figures

    # Mark the sub-stage for the UI ("Captioning figure N/M"). progress stays at
    # 100 (set at TEXT_READY) so the bar never counts backwards; stage_valid's
    # CHECK includes 'captioning'.
    await _set_fields(doc_id, stage="captioning")

    captioned = 0
    new_chunks = 0
    try:
        cap = await caption_figures(
            document_id=str(doc_id), session_factory=session_factory
        )
        captioned = cap.captioned
        new_chunks = cap.new_chunks
        log.info(
            "caption.stage_done",
            document_id=document_id,
            total=cap.total,
            captioned=cap.captioned,
            failed=cap.failed,
            cached=cap.cached,
            reembedded=cap.reembedded,
            new_chunks=cap.new_chunks,
            caption_cost_usd=round(cap.cost_usd, 6),
        )
    except asyncio.CancelledError:
        # Re-queue on cancellation — never swallow it.
        cur = await _load_status(doc_id)
        if cur:
            await _set_fields(
                doc_id, status=_guarded(cur, DocumentStatus.QUEUED), stage=None
            )
        raise
    except Exception as exc:  # captioning must never fail an already-ready doc
        log.warning(
            "caption.stage_failed", document_id=document_id, error=str(exc)
        )

    # Re-read status AND deleted_at right before the write: the multi-minute
    # captioning window is long enough for a soft-delete to land. Only advance a
    # still-TEXT_READY, non-deleted document, so _guarded's fallback can never
    # resurrect a DELETED one (the deleted_at check also covers a delete that
    # stamped deleted_at without yet flipping status).
    state = await _load_doc_state(doc_id)
    cur = state[0] if state is not None else None
    if cur == DocumentStatus.TEXT_READY.value and not _is_deleted(state):
        await _set_fields(
            doc_id,
            status=_guarded(cur, DocumentStatus.FULL_READY),
            stage=None,
            progress=100,
            # Correct the denormalised counter: captioning may have added shadow
            # chunks for figures that had no Docling caption.
            chunk_count=base_chunk_count + new_chunks,
        )
        return {
            "final_status": DocumentStatus.FULL_READY.value,
            "captioned": captioned,
            "new_chunks": new_chunks,
        }

    # Document moved on (deleted / re-queued) — leave it and clear the sub-stage.
    if cur is not None and cur != DocumentStatus.DELETED.value:
        await _set_fields(doc_id, stage=None)
    return {"final_status": cur, "captioned": captioned, "new_chunks": new_chunks}


async def _record_job(
    doc_id: uuid.UUID,
    *,
    ok: bool,
    failed_step: str | None = None,
    timings: dict | None = None,
    usage: dict | None = None,
) -> None:
    """Best-effort ingestion_jobs telemetry (ARQ keeps no history — §3)."""
    try:
        async with session_factory() as session:
            async with session.begin():
                session.add(
                    IngestionJob(
                        document_id=doc_id,
                        attempt=1,
                        failed_step=failed_step,
                        step_timings_ms=timings,
                        usage=usage,
                        config_snapshot={
                            "embedding_model": settings.embedding_model,
                            "embedding_dims": settings.embedding_dims,
                            "chunk_target": settings.chunk_target,
                            "chunk_max": settings.chunk_max,
                        },
                    )
                )
    except Exception:  # pragma: no cover - telemetry must not break the task
        log.warning("ingest.job_record_failed", document_id=str(doc_id))


# --------------------------------------------------------------------------- #
# Operational cron jobs (Build Sheet §8 P4)
# --------------------------------------------------------------------------- #
async def sweep_stuck_documents(ctx: dict) -> dict:
    """Cron (~every 10 min): fail documents wedged in PROCESSING (§3 sweep).

    A worker that dies mid-ingest leaves its document in PROCESSING forever, so
    the UI shows an eternal spinner instead of Failed + Retry. This flips any
    PROCESSING document whose ``started_at`` is older than
    ``settings.sweep_stuck_after_minutes`` to FAILED with error_code
    ``STUCK_TIMEOUT``.

    One short committed set-based UPDATE, guarded in the WHERE clause:
      - ``status = 'processing'`` asserts the source state (race-free vs a worker
        committing TEXT_READY at the same instant — only one write wins);
      - ``deleted_at IS NULL`` never resurrects a soft-deleted doc;
      - ``started_at IS NOT NULL AND started_at < cutoff`` — a NULL started_at
        must not match.
    ``processing -> failed`` is a legal edge; transition() asserts that honestly.
    """
    # Legal-edge assertion (pure guard, no I/O) — keeps the sweep on the state
    # machine even though the UPDATE enforces the source state in SQL.
    failed_value = transition(DocumentStatus.PROCESSING, DocumentStatus.FAILED).value
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.sweep_stuck_after_minutes)

    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                update(Document)
                .where(
                    Document.status == DocumentStatus.PROCESSING.value,
                    Document.deleted_at.is_(None),
                    Document.started_at.is_not(None),
                    Document.started_at < cutoff,
                )
                .values(
                    status=failed_value,
                    stage=None,
                    error_code="STUCK_TIMEOUT",
                    error_message=(
                        "Ingestion exceeded "
                        f"{settings.sweep_stuck_after_minutes} min "
                        "in PROCESSING and was swept to FAILED."
                    ),
                )
                .returning(Document.id)
            )
            swept = [str(row[0]) for row in result.all()]

    if swept:
        log.warning(
            "sweep.stuck_documents",
            count=len(swept),
            document_ids=swept,
            after_minutes=settings.sweep_stuck_after_minutes,
        )
    else:
        log.info("sweep.stuck_documents", count=0)
    return {"swept": len(swept), "document_ids": swept}


async def reconcile_all_task(ctx: dict) -> dict:
    """Nightly cron: reconcile Qdrant vs Postgres across every project (§3).

    Runs ``rag.reconcile`` once per project so each project gets its own summary
    log line (orphan=info, missing/stale=warning inside reconcile), then logs a
    global rollup. Never writes either store.
    """
    from app.rag import reconcile

    async with session_factory() as session:
        result = await session.execute(select(Project.id))
        project_ids = [str(row[0]) for row in result.all()]

    totals = {
        "hydrated": 0,
        "retrieved": 0,
        "orphan_vectors": 0,
        "missing_vectors": 0,
        "stale_vectors": 0,
    }
    for pid in project_ids:
        summary = await reconcile(project_id=pid, session_factory=session_factory)
        for key in totals:
            totals[key] += summary.get(key, 0)

    log.info(
        "reconcile.nightly_rollup", projects=len(project_ids), **totals
    )
    return {"projects": len(project_ids), **totals}


# --------------------------------------------------------------------------- #
# Worker lifecycle
# --------------------------------------------------------------------------- #
async def on_startup(ctx: dict) -> None:
    """Warm the Docling models so the first ingest does not pay the load cost."""
    configure_logging()
    log.info("worker.startup", max_jobs=settings.worker_max_jobs, redis_url=settings.redis_url)
    try:
        from app.rag.parse import warm_models

        await asyncio.get_running_loop().run_in_executor(None, warm_models)
        log.info("worker.docling_warm")
    except Exception as exc:  # pragma: no cover - warm-up is best-effort
        log.warning("worker.docling_warm_failed", error=str(exc))


async def on_shutdown(ctx: dict) -> None:
    """Worker shutdown hook."""
    log.info("worker.shutdown")


class WorkerSettings:
    """ARQ worker configuration.

    ``max_jobs=1`` at v1: Docling peaks at ~6.2 GB RSS, so one concurrent ingest
    per worker (mem_limit 8G in compose). Raise only after measuring real peak (P1).
    """

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.worker_max_jobs
    on_startup = on_startup
    on_shutdown = on_shutdown
    functions = [ingest_document_task]
    # Operational cron jobs (§8 P4). ``cron`` defaults second=0, unique=True, so
    # only one worker fires each job even when several are running.
    cron_jobs = [
        # Stuck-document sweep: every 10 minutes on the :00/:10/.../:50 mark.
        cron(sweep_stuck_documents, minute={0, 10, 20, 30, 40, 50}),
        # Reconcile: nightly at 03:00.
        cron(reconcile_all_task, hour=3, minute=0),
    ]


def main() -> None:
    """Console-script entrypoint (``paperlens-worker``)."""
    from arq import run_worker

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
