"""``answer_stream`` — the real grounded-answer generator (Build Sheet §5.4).

Yields protocol-neutral :class:`GenEvent` objects in the exact CONTRACT.md §2
order. Does NOT write Postgres and never holds an ``AsyncSession`` across a
network call (persistence + the disconnect≠cancel detached task live in the
route, ``app/api/routes/chat.py``).

Frame order emitted here::

    start
     → data-status  retrieving
     → data-status  generating
     → data-citation ×N   (pending; quote dereferenced verbatim from chunks.content)
     → text-start
     → text-delta  ×n     ([C#] anchors kept verbatim)
     → text-end
     → data-citation ×used (re-emit SAME id, verify_status grounded|weak)
     → finish

Abstain path (retrieval empty or below the off-topic gate): a citation-free
"not enough evidence" answer — the core differentiator; we never fabricate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from sqlalchemy import text

from app.config import settings
from app.domain.events import (
    CitationEvent,
    FigureDisplay,
    FigureEvent,
    FinishEvent,
    GenEvent,
    Stage,
    StartEvent,
    StatusEvent,
    TableEvent,
    TableStructure,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    VerifyStatus,
)
from app.rag import generate
from app.rag.retrieval import RetrievedChunk, retrieve

# Conservative default for the uncalibrated off-topic gate (settings value is
# None until end-of-P2 calibration). text-embedding-3-large@1024 puts in-corpus
# and out-of-corpus cosines in a 0.28-0.35 overlap band, so we gate LOW and let
# the prompt-level abstain carry the rest (build sheet: abstain is a hard metric,
# calibrated later from labelled in/out-of-corpus sets).
_DEFAULT_OFFTOPIC_THRESHOLD = 0.28

# Max characters of chunk content to surface as a citation quote. A prefix of the
# canonical content is still a verbatim substring, so citation_quote_match stays
# 1.0. Truncation happens at a word boundary.
_QUOTE_MAX_CHARS = 360


def _offtopic_threshold() -> float:
    return (
        settings.offtopic_threshold
        if settings.offtopic_threshold is not None
        else _DEFAULT_OFFTOPIC_THRESHOLD
    )


def _quote_from_content(content: str) -> str:
    """Verbatim quote: the content, truncated at a word boundary if too long."""
    text = content.strip()
    if len(text) <= _QUOTE_MAX_CHARS:
        return text
    cut = text[:_QUOTE_MAX_CHARS]
    sp = cut.rfind(" ")
    if sp > _QUOTE_MAX_CHARS // 2:
        cut = cut[:sp]
    return cut.rstrip()


def _pending_citation(anchor: generate.Anchor) -> CitationEvent:
    return CitationEvent(
        id=f"cit-{anchor.marker}",
        marker=anchor.marker,
        quote=_quote_from_content(anchor.content),
        document_id=anchor.document_id,
        document_title=anchor.document_title or anchor.filename,
        filename=anchor.filename,
        page=anchor.page if anchor.page is not None else 0,
        section_path=anchor.section_path,
        content_hash=anchor.content_hash,
        verify_status=VerifyStatus.PENDING,
    )


def _figure_image_url(document_id: str, figure_id: str) -> str:
    """Served URL for a figure crop (frontend proxies /static/* to the backend)."""
    return f"/static/figures/{document_id}/{figure_id}.png"


_FIGURES_SQL = text(
    """
    SELECT id, document_id, label, caption, page, image_path
    FROM figures
    WHERE id = ANY(CAST(:ids AS uuid[])) AND image_path IS NOT NULL
    """
)

_TABLES_SQL = text(
    """
    SELECT id, document_id, label, caption, page, structure_kind, html, markdown_flat
    FROM tables
    WHERE id = ANY(CAST(:ids AS uuid[]))
    """
)


async def _hydrate_media(
    session_factory, chunks: list[RetrievedChunk]
) -> tuple[list[generate.FigureAnchor], list[generate.TableAnchor]]:
    """Resolve figure/table parents for the retrieved context, in ranked order.

    Figures come from ``chunk_type='figure'`` shadow chunks (only those with a
    saved crop are returned — emitting a figure with no image is pointless).
    Tables come from ``chunk_type='table_child'`` chunks. Figures are capped at
    ``settings.max_figures_in_context``. Anchor numbering (F1.., T1..) follows the
    first-appearance order of the linking chunks.
    """
    fig_ids: list[str] = []
    tbl_ids: list[str] = []
    for ch in chunks:
        if ch.chunk_type == "figure" and ch.figure_id and ch.figure_id not in fig_ids:
            fig_ids.append(ch.figure_id)
        elif (
            ch.chunk_type == "table_child"
            and ch.table_id
            and ch.table_id not in tbl_ids
        ):
            tbl_ids.append(ch.table_id)

    if not fig_ids and not tbl_ids:
        return [], []

    fig_rows: dict[str, dict] = {}
    tbl_rows: dict[str, dict] = {}
    async with session_factory() as session:
        if fig_ids:
            res = await session.execute(_FIGURES_SQL, {"ids": fig_ids})
            fig_rows = {str(r["id"]): dict(r) for r in res.mappings().all()}
        if tbl_ids:
            res = await session.execute(_TABLES_SQL, {"ids": tbl_ids})
            tbl_rows = {str(r["id"]): dict(r) for r in res.mappings().all()}

    figures: list[generate.FigureAnchor] = []
    for fid in fig_ids:
        row = fig_rows.get(fid)
        if row is None:  # no crop (image_path NULL) → not emittable
            continue
        if len(figures) >= settings.max_figures_in_context:
            break
        n = len(figures) + 1
        figures.append(
            generate.FigureAnchor(
                marker=f"F{n}",
                figure_id=fid,
                event_id=f"fig-{fid}",
                label=row["label"] or "Figure",
                caption=row["caption"],
                page=row["page"],
                image_url=_figure_image_url(str(row["document_id"]), fid),
            )
        )

    tables: list[generate.TableAnchor] = []
    for tid in tbl_ids:
        row = tbl_rows.get(tid)
        if row is None:
            continue
        n = len(tables) + 1
        tables.append(
            generate.TableAnchor(
                marker=f"T{n}",
                table_id=tid,
                event_id=f"tbl-{tid}",
                label=row["label"] or "Table",
                caption=row["caption"],
                page=row["page"],
                structure_kind=row["structure_kind"] or "flat",
                html=row["html"],
                markdown=row["markdown_flat"],
            )
        )
    return figures, tables


def _figure_event(anchor: generate.FigureAnchor, display: FigureDisplay) -> FigureEvent:
    return FigureEvent(
        id=anchor.event_id,
        marker=anchor.marker if display == FigureDisplay.CITED else None,
        label=anchor.label,
        image_url=anchor.image_url,
        page=anchor.page if anchor.page is not None else 0,
        caption=anchor.caption,
        display=display,
    )


def _table_event(anchor: generate.TableAnchor) -> TableEvent:
    kind = (
        TableStructure.SPANNED
        if anchor.structure_kind == "spanned"
        else TableStructure.FLAT
    )
    return TableEvent(
        id=anchor.event_id,
        marker=anchor.marker,
        label=anchor.label,
        page=anchor.page if anchor.page is not None else 0,
        caption=anchor.caption,
        structure_kind=kind,
        html=anchor.html if kind == TableStructure.SPANNED else None,
        markdown=anchor.markdown if kind == TableStructure.FLAT else None,
    )


async def _abstain(request_id: str) -> AsyncIterator[GenEvent]:
    """Emit a citation-free abstain answer with a valid frame sequence."""
    yield StartEvent(request_id=request_id, model=settings.llm_model_default)
    yield StatusEvent(stage=Stage.RETRIEVING, detail="Searching the corpus…")
    yield TextStartEvent()
    yield TextDeltaEvent(delta=generate.ABSTAIN_TEXT)
    yield TextEndEvent()
    yield FinishEvent(finish_reason="stop")


async def answer_stream(
    *,
    project_id: str,
    question: str,
    history: Sequence[dict[str, str]],
    session_factory,
    document_ids: Sequence[str] | None = None,
    top_k: int = 8,
    trace_id: str | None = None,
) -> AsyncIterator[GenEvent]:
    """Stream a grounded answer as :class:`GenEvent` objects (see module docstring)."""
    request_id = trace_id or f"req-{project_id[:8]}"
    doc_ids = list(document_ids) if document_ids else None

    # ---- retrieval ------------------------------------------------------- #
    yield StartEvent(request_id=request_id, model=settings.llm_model_default)
    n_docs = len(doc_ids) if doc_ids else 0
    detail = (
        f"Searching {n_docs} document{'s' if n_docs != 1 else ''}…"
        if n_docs
        else "Searching the corpus…"
    )
    yield StatusEvent(stage=Stage.RETRIEVING, detail=detail)

    result = await retrieve(
        project_id=project_id,
        question=question,
        session_factory=session_factory,
        document_ids=doc_ids,
        top_k=top_k,
    )

    # ---- abstain gate ---------------------------------------------------- #
    if not result.chunks or result.max_dense_cosine < _offtopic_threshold():
        yield StatusEvent(stage=Stage.GENERATING, detail="No sufficient evidence")
        yield TextStartEvent()
        yield TextDeltaEvent(delta=generate.ABSTAIN_TEXT)
        yield TextEndEvent()
        yield FinishEvent(finish_reason="stop")
        return

    # ---- build anchors + pending citations ------------------------------- #
    anchors = generate.build_anchors(result.chunks)
    anchor_by_marker = {a.marker: a for a in anchors}
    pending = {a.marker: _pending_citation(a) for a in anchors}

    # Resolve figures/tables linked to the retrieved context.
    figures, tables = await _hydrate_media(session_factory, result.chunks)
    figure_by_marker = {f.marker: f for f in figures}

    yield StatusEvent(stage=Stage.GENERATING, detail="Generating answer…")

    # Sources render BEFORE text (contract invariant), in the order
    # citations → figures → tables → text-start (build sheet §4 SSE block). Every
    # candidate is cited-eligible; the model is given exactly these anchor ids, so
    # any [C#]/[F#]/[T#] it emits references a real retrieved item.
    for cit in pending.values():
        yield cit
    for fig in figures:
        yield _figure_event(fig, FigureDisplay.CANDIDATE)
    for tbl in tables:
        yield _table_event(tbl)

    # ---- stream the answer ---------------------------------------------- #
    messages = generate.build_messages(
        question, anchors, history, figures=figures, tables=tables
    )
    usage = generate.StreamUsage()

    yield TextStartEvent()
    answer_parts: list[str] = []
    async for delta in generate.stream_completion(messages, usage):
        answer_parts.append(delta)
        yield TextDeltaEvent(delta=delta)  # [C#] anchors kept verbatim
    yield TextEndEvent()

    answer_text = "".join(answer_parts)

    # ---- re-emit ONLY the anchors the model actually used --------------- #
    all_markers = set(anchor_by_marker) | set(figure_by_marker)
    used_markers = _used_markers(answer_text, all_markers)
    for marker in used_markers:
        if marker in anchor_by_marker:
            anchor = anchor_by_marker[marker]
            status = generate.grounding_status(answer_text, anchor, marker)
            verified = pending[marker].model_copy(
                update={
                    "verify_status": VerifyStatus.GROUNDED
                    if status == "grounded"
                    else VerifyStatus.WEAK
                }
            )
            yield verified
        elif marker in figure_by_marker:
            # Promotion: re-emit the SAME fig-{uuid} id with display="cited" and
            # the marker set, so the client folds it into the answer flow.
            yield _figure_event(figure_by_marker[marker], FigureDisplay.CITED)

    yield FinishEvent(
        finish_reason=usage.finish_reason,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
    )


def _used_markers(answer_text: str, known: object) -> list[str]:
    """Return the anchor markers actually cited in the answer, in first-use order."""
    import re

    known_set = set(known)
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in re.finditer(settings.marker_re, answer_text):
        for token in m.group(1).split(","):
            marker = token.strip()
            if marker in known_set and marker not in seen_set:
                seen.append(marker)
                seen_set.add(marker)
    return seen


__all__ = ["answer_stream"]
