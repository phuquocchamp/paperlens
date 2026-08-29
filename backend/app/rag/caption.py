"""VLM figure captioning — the TEXT_READY -> FULL_READY background stage.

Ingestion crops each figure to a PNG and creates a *shadow chunk*
(``chunk_type='figure'``) that embeds only the Docling caption. That leaves
captioned figures reachable in retrieval solely by exact caption matches. This
module fills ``figures.vlm_summary`` with a concise VLM description of the crop
and (optionally) folds that summary back into the shadow chunk so figures surface
for far more questions.

Design decisions (Build Sheet §5.1 + the figure-hazard rules):

* Concurrency is capped by ``settings.caption_concurrency`` (2 at v1 — raise only
  after measuring 429-rate). Each crop is sent to the OpenAI vision model
  (``settings.llm_model_fallback`` = gpt-4o-mini; DeepSeek chat is text-only).
* Captions are expensive to regenerate, so every result is cached in Redis under
  ``vlmcap:{sha256(png)}`` (keyed on the *file bytes*, so identity is stable).
  The durable copy lives in Postgres (``figures.vlm_summary``); Redis is the
  cross-document / re-ingest cache.
* CRITICAL HAZARD: the prompt forbids inventing numbers from axis positions or
  unlabeled points. A VLM that misreads an axis would launder a fabricated number
  through the caption into a "grounded" citation. "Not clearly labeled" is the
  correct answer — see ``_CAPTION_PROMPT``.
* Non-fatal: a per-figure failure sets ``vlm_status='failed'`` and never raises;
  the document is already queryable at TEXT_READY. Only ``CancelledError`` is
  allowed to propagate (the worker re-queues on cancellation).

Re-embedding: after captioning, the figure shadow chunks are rebuilt as
``caption + vlm_summary`` and re-embedded (dense + sparse) with an *upsert-only*
Qdrant write by point id — NEVER a delete-by-document (that would wipe the text
vectors). Figures that had no Docling caption (hence no shadow chunk) get a fresh
shadow chunk created so their ``vlm_summary`` is retrievable too.

This module NEVER reads or writes the ``documents`` table (the ARQ task owns that
state machine). Heavy imports (openai, redis, numpy) stay lazy so importing
``app.rag`` / ``app.main`` stays cheap.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy import func, select, update

from app.config import settings
from app.db.models import Chunk, Figure
from app.rag import chat_supports_vision
from app.rag.chunker import make_embed_text
from app.rag.embed import Embedder
from app.rag.qdrant_store import (
    build_payload,
    ensure_collection,
    get_client,
    upsert_points,
)

log = structlog.get_logger(__name__)

# gpt-4o-mini list price (USD / 1M tokens), for cost reporting only. Kept next to
# the value it derives so no dollar figure is ever hard-coded at a call site
# (mirrors ``EMBED_PRICE_PER_1M_USD`` in embed.py).
CAPTION_PRICE_INPUT_PER_1M_USD = 0.15
CAPTION_PRICE_OUTPUT_PER_1M_USD = 0.60

# Ceiling on caption length. 1-3 sentences ~= 120 output tokens is plenty; the cap
# is a cost guardrail against a runaway generation, not a target.
_CAPTION_MAX_TOKENS = 200

# The anti-hallucination prompt (Build Sheet §1 figure-hazard rule). Do NOT soften
# the "no unlabeled numbers" instruction — it is the barrier that stops a misread
# axis from laundering a fabricated number into a grounded citation.
_CAPTION_PROMPT = (
    "You are describing a figure cropped from a scientific paper for a retrieval "
    "index. In 1-3 concise sentences, describe what the figure shows: the type of "
    "chart or diagram, its axes and their labels, its components, and what it "
    "compares or illustrates. "
    "Do NOT state any numeric value unless it is explicitly printed as a text "
    "label in the figure. If a value is not clearly labeled, do not guess it — "
    "describe the trend, shape, or relationship instead of inventing a number. "
    "Do not add preamble; return only the description."
)


@dataclass
class CaptionResult:
    """Summary of a captioning pass (logged by the worker for the cost proof)."""

    total: int = 0            # figures considered (image_path set, status pending)
    captioned: int = 0        # figures that now have a vlm_summary
    failed: int = 0           # figures whose VLM call errored
    cached: int = 0           # captions served from the Redis cache
    reembedded: int = 0       # existing shadow chunks rebuilt with the summary
    new_chunks: int = 0       # shadow chunks created for previously-uncaptioned figs
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * CAPTION_PRICE_INPUT_PER_1M_USD
            + self.completion_tokens / 1_000_000 * CAPTION_PRICE_OUTPUT_PER_1M_USD
        )


def _png_cache_key(png: bytes) -> str:
    return f"vlmcap:{hashlib.sha256(png).hexdigest()}"


async def caption_figures(
    *,
    document_id: str,
    session_factory,
) -> CaptionResult:
    """Caption every pending, cropped figure of a document and surface it.

    Loads figures with ``image_path`` set and ``vlm_status='pending'``, captions
    each crop via the OpenAI vision model (concurrency-limited, Redis-cached),
    stores the result in ``figures.vlm_summary`` / ``vlm_status``, then folds each
    summary into the figure's shadow chunk (re-embedded, upsert-only).

    Non-fatal by contract: individual figure failures are recorded as
    ``vlm_status='failed'`` and never raise. ``CancelledError`` propagates.
    """
    doc_id = uuid.UUID(str(document_id))

    # ---- load candidates ------------------------------------------------- #
    async with session_factory() as session:
        figs = (
            (
                await session.execute(
                    select(Figure).where(
                        Figure.document_id == doc_id,
                        Figure.image_path.isnot(None),
                        Figure.vlm_status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )

    result = CaptionResult(total=len(figs))
    if not figs:
        return result

    # A vision-incapable model (or a missing key) means we cannot caption. Leave
    # the figures 'pending' so a future run can pick them up; never fail the doc.
    model = settings.llm_model_fallback
    if not settings.openai_api_key or not chat_supports_vision(model):
        log.warning(
            "caption.skipped_no_vision",
            document_id=document_id,
            model=model,
            has_key=bool(settings.openai_api_key),
        )
        return result

    # ---- caption each crop (semaphore-bounded, Redis-cached) ------------- #
    sem = asyncio.Semaphore(settings.caption_concurrency)
    redis = await _redis_client()
    client = _openai_client()

    # fig_id -> (status, summary_or_None, prompt_tokens, completion_tokens, from_cache)
    outcomes: dict[uuid.UUID, tuple[str, str | None, int, int, bool]] = {}

    async def _work(fig: Figure) -> None:
        async with sem:
            try:
                png = await asyncio.get_running_loop().run_in_executor(
                    None, Path(str(fig.image_path)).read_bytes
                )
                key = _png_cache_key(png)
                cached = await _cache_get(redis, key)
                if cached is not None:
                    outcomes[fig.id] = ("done", cached, 0, 0, True)
                    return
                summary, ptoks, ctoks = await _vision_caption(client, model, png)
                if summary:
                    await _cache_set(redis, key, summary)
                    outcomes[fig.id] = ("done", summary, ptoks, ctoks, False)
                else:
                    outcomes[fig.id] = ("failed", None, ptoks, ctoks, False)
            except asyncio.CancelledError:
                raise  # never swallow cancellation
            except Exception as exc:  # one bad crop must not fail the document
                log.warning(
                    "caption.figure_failed",
                    document_id=document_id,
                    figure_id=str(fig.id),
                    error=str(exc),
                )
                outcomes[fig.id] = ("failed", None, 0, 0, False)

    try:
        # Plain gather (no return_exceptions): _work swallows normal errors itself
        # and re-raises only CancelledError, so cancellation still propagates.
        await asyncio.gather(*(_work(f) for f in figs))
    finally:
        await _aclose(client)
        await _aclose_redis(redis)

    # ---- tally + persist figures ----------------------------------------- #
    captioned: dict[uuid.UUID, str] = {}
    for fig_id, (status, summary, ptoks, ctoks, from_cache) in outcomes.items():
        result.prompt_tokens += ptoks
        result.completion_tokens += ctoks
        if status == "done" and summary:
            result.captioned += 1
            if from_cache:
                result.cached += 1
            captioned[fig_id] = summary
        else:
            result.failed += 1

    async with session_factory() as session:
        async with session.begin():
            for fig_id, (status, summary, *_rest) in outcomes.items():
                if status == "done" and summary:
                    await session.execute(
                        update(Figure)
                        .where(Figure.id == fig_id)
                        .values(vlm_summary=summary, vlm_status="done")
                    )
                else:
                    await session.execute(
                        update(Figure)
                        .where(Figure.id == fig_id)
                        .values(vlm_status="failed")
                    )

    # ---- fold summaries into shadow chunks + re-embed (upsert-only) ------ #
    if captioned:
        try:
            reembedded, new_chunks = await _resurface_shadow_chunks(
                doc_id=doc_id, captioned=captioned, session_factory=session_factory
            )
            result.reembedded = reembedded
            result.new_chunks = new_chunks
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # re-embed is a retrieval nicety, never fatal
            log.warning(
                "caption.reembed_failed", document_id=document_id, error=str(exc)
            )

    log.info(
        "caption.done",
        document_id=document_id,
        total=result.total,
        captioned=result.captioned,
        failed=result.failed,
        cached=result.cached,
        reembedded=result.reembedded,
        new_chunks=result.new_chunks,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=round(result.cost_usd, 6),
    )
    return result


# --------------------------------------------------------------------------- #
# Shadow-chunk resurfacing
# --------------------------------------------------------------------------- #
async def _resurface_shadow_chunks(
    *,
    doc_id: uuid.UUID,
    captioned: dict[uuid.UUID, str],
    session_factory,
) -> tuple[int, int]:
    """Rebuild figure shadow chunks as ``caption + vlm_summary`` and re-embed.

    For a figure that already has a shadow chunk (it had a Docling caption) the
    chunk's ``content`` / ``embed_text`` / ``content_hash`` are rebuilt from the
    figure's caption/label plus the summary (idempotent — never appended to the
    live content). For a figure that had no caption (hence no shadow chunk) a new
    shadow chunk is created so the summary is still retrievable.

    Returns ``(reembedded_count, new_chunk_count)``. Writes are upsert-only by
    point id — the text vectors are never touched.
    """
    # Load existing shadow chunks for the captioned figures.
    async with session_factory() as session:
        shadow_rows = (
            (
                await session.execute(
                    select(Chunk).where(
                        Chunk.document_id == doc_id,
                        Chunk.chunk_type == "figure",
                        Chunk.figure_id.in_(list(captioned.keys())),
                    )
                )
            )
            .scalars()
            .all()
        )
        # Figure rows carry the caption/label/page needed to rebuild the surface.
        fig_rows = (
            (
                await session.execute(
                    select(Figure).where(Figure.id.in_(list(captioned.keys())))
                )
            )
            .scalars()
            .all()
        )
        # Next chunk_index for any brand-new shadow chunks.
        max_idx = (
            await session.execute(
                select(func.max(Chunk.chunk_index)).where(
                    Chunk.document_id == doc_id
                )
            )
        ).scalar_one()

    figs_by_id = {f.id: f for f in fig_rows}
    have_shadow = {c.figure_id for c in shadow_rows}
    next_idx = (max_idx or -1) + 1

    # Pending re-embed work: rows to UPDATE and rows to INSERT.
    updates: list[tuple[Chunk, str, str, str]] = []  # (row, content, embed_text, hash)
    inserts: list[Chunk] = []

    for chunk in shadow_rows:
        fig = figs_by_id.get(chunk.figure_id)
        summary = captioned[chunk.figure_id]
        base = _figure_base(fig)
        content = _combine(base, summary)
        embed_text = make_embed_text(content, chunk.section_path)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        updates.append((chunk, content, embed_text, content_hash))

    # Figures with a caption/summary but no shadow chunk -> create one.
    proj_id = None
    if shadow_rows:
        proj_id = shadow_rows[0].project_id
    for fig_id, summary in captioned.items():
        if fig_id in have_shadow:
            continue
        fig = figs_by_id.get(fig_id)
        if proj_id is None:
            proj_id = await _project_id_for_document(doc_id, session_factory)
        base = _figure_base(fig)
        content = _combine(base, summary)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        row = Chunk(
            id=uuid.uuid4(),
            document_id=doc_id,
            project_id=proj_id,
            chunk_index=next_idx,
            content=content,
            embed_text=make_embed_text(content, None),
            content_hash=content_hash,
            section_path=None,
            heading_levels=None,
            page_start=fig.page if fig else None,
            page_end=fig.page if fig else None,
            chunk_type="figure",
            is_embedded=False,
            figure_id=fig_id,
            table_id=None,
        )
        next_idx += 1
        inserts.append(row)

    # Assemble the ordered embed batch (updates first, then inserts).
    embed_ids: list[uuid.UUID] = [u[0].id for u in updates] + [r.id for r in inserts]
    embed_texts: list[str] = [u[2] for u in updates] + [r.embed_text for r in inserts]
    payload_meta: list[tuple[int, int | None, str, str]] = [
        (u[0].chunk_index, u[0].page_start, u[0].section_path or "", u[3])
        for u in updates
    ] + [
        (r.chunk_index, r.page_start, "", r.content_hash) for r in inserts
    ]
    if not embed_ids:
        return 0, 0

    # Embed (dense + sparse) — cache hit is likely on re-ingest.
    embedder = Embedder()
    try:
        emb = await embedder.embed(embed_texts)
    finally:
        await embedder.aclose()

    # Upsert-only Qdrant write by point id. NEVER delete-by-document here.
    client = get_client()
    try:
        await ensure_collection(client)
        payloads = [
            build_payload(
                project_id=str(proj_id),
                document_id=str(doc_id),
                modality="figure",
                chunk_type="figure",
                page=page,
                section_path=(section or None),
                chunk_index=cidx,
                content_hash=chash,
                ingest_generation=0,
            )
            for (cidx, page, section, chash) in payload_meta
        ]
        await upsert_points(
            client,
            ids=[str(i) for i in embed_ids],
            dense=emb.dense,
            sparse=emb.sparse,
            payloads=payloads,
        )
    finally:
        await client.close()

    # Persist chunk rows: UPDATE existing, INSERT new (all is_embedded=True).
    async with session_factory() as session:
        async with session.begin():
            for row, content, embed_text, chash in updates:
                await session.execute(
                    update(Chunk)
                    .where(Chunk.id == row.id)
                    .values(
                        content=content,
                        embed_text=embed_text,
                        content_hash=chash,
                        is_embedded=True,
                    )
                )
            for row in inserts:
                row.is_embedded = True
            if inserts:
                session.add_all(inserts)

    return len(updates), len(inserts)


def _figure_base(fig: Figure | None) -> str:
    """The non-summary surface of a figure (caption, else label)."""
    if fig is None:
        return ""
    return (fig.caption or fig.label or "").strip()


def _combine(base: str, summary: str) -> str:
    """Join a figure's base surface with its VLM summary (idempotent build)."""
    base = (base or "").strip()
    summary = (summary or "").strip()
    if base and summary:
        return f"{base}\n\n{summary}"
    return base or summary


async def _project_id_for_document(doc_id: uuid.UUID, session_factory) -> uuid.UUID:
    """Fetch project_id via an existing chunk (RAG must not read ``documents``)."""
    async with session_factory() as session:
        pid = (
            await session.execute(
                select(Chunk.project_id).where(Chunk.document_id == doc_id).limit(1)
            )
        ).scalar_one_or_none()
    if pid is None:
        raise ValueError(f"no chunk to derive project_id for document {doc_id}")
    return pid


# --------------------------------------------------------------------------- #
# OpenAI vision + Redis helpers (lazy, best-effort)
# --------------------------------------------------------------------------- #
def _openai_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=settings.openai_api_key)


async def _vision_caption(client, model: str, png: bytes) -> tuple[str, int, int]:
    """Return ``(caption, prompt_tokens, completion_tokens)`` for one crop."""
    import base64

    b64 = base64.b64encode(png).decode("ascii")
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _CAPTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        max_tokens=_CAPTION_MAX_TOKENS,
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    ptoks = getattr(usage, "prompt_tokens", 0) or 0
    ctoks = getattr(usage, "completion_tokens", 0) or 0
    return text, ptoks, ctoks


async def _redis_client():
    import redis.asyncio as redis

    # decode_responses=False mirrors embed.py: values are bytes, encode/decode utf-8.
    return redis.from_url(settings.redis_url, decode_responses=False)


async def _cache_get(redis, key: str) -> str | None:
    try:
        blob = await redis.get(key)
    except Exception:  # cache is best-effort; a Redis blip must not fail captioning
        return None
    if blob is None:
        return None
    try:
        return blob.decode("utf-8")
    except Exception:  # pragma: no cover - defensive
        return None


async def _cache_set(redis, key: str, value: str) -> None:
    try:
        await redis.set(key, value.encode("utf-8"))
    except Exception:  # pragma: no cover - cache write is best-effort
        pass


async def _aclose(client) -> None:
    try:
        await client.close()
    except Exception:  # pragma: no cover
        pass


async def _aclose_redis(redis) -> None:
    try:
        await redis.aclose()
    except Exception:  # pragma: no cover
        pass


__all__ = ["caption_figures", "CaptionResult", "CAPTION_PRICE_INPUT_PER_1M_USD"]
