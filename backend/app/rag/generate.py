"""Prompt construction, LLM streaming (DeepSeek → OpenAI breaker), grounding.

Anchor-based citations (build sheet blocker fix #2, "Anthropic Citations" style):
the LLM only emits short anchor ids (``[C1]``, ``[C3]``) that map to retrieved
chunks — it NEVER writes the displayed quote. The server dereferences every quote
verbatim from ``chunks.content`` (see ``query.py``), so ``citation_quote_match``
is 1.0 by construction, not by a fuzzy threshold.

The prompt groups the retrieved chunks into a ``<documents>`` block by document,
tags each chunk with its anchor id, and instructs the model to answer ONLY from
those documents, to cite inline with ``[C#]`` markers (matching ``MARKER_RE``),
and to abstain when the evidence is insufficient.
"""

from __future__ import annotations

import contextvars
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from app.config import settings

# The actual model used for a run (default or fallback), surfaced to the route for
# ``messages.model`` persistence via the same task's context.
ACTUAL_MODEL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "actual_model", default=settings.llm_model_default
)

ABSTAIN_TEXT = (
    "I don't have enough evidence in this corpus to answer that. "
    "Try rephrasing, or add documents that cover this topic."
)

# Marker used to strip old citation anchors out of prior assistant turns.
_MARKER = re.compile(settings.marker_re)


@dataclass
class Anchor:
    """One ``C#`` anchor bound to a retrieved chunk."""

    marker: str          # "C1"
    chunk_id: str
    content: str
    document_id: str
    document_title: str | None
    filename: str
    page: int | None
    section_path: str | None
    content_hash: str


@dataclass
class FigureAnchor:
    """One ``F#`` anchor bound to a figure row (only figures with a saved crop)."""

    marker: str          # "F1"
    figure_id: str       # figures.id (uuid)
    event_id: str        # "fig-{uuid}" — stable id across candidate/cited emits
    label: str           # e.g. "Figure 2" (never None)
    caption: str | None
    page: int | None
    image_url: str       # "/static/figures/{document_id}/{figure_id}.png"


@dataclass
class TableAnchor:
    """One ``T#`` anchor bound to a table row."""

    marker: str          # "T1"
    table_id: str        # tables.id (uuid)
    event_id: str        # "tbl-{uuid}"
    label: str
    caption: str | None
    page: int | None
    structure_kind: str  # "flat" | "spanned"
    html: str | None
    markdown: str | None


def build_anchors(chunks) -> list[Anchor]:
    """Assign ``C1..Cn`` anchors to hydrated chunks in ranked order."""
    anchors: list[Anchor] = []
    for i, ch in enumerate(chunks, start=1):
        anchors.append(
            Anchor(
                marker=f"C{i}",
                chunk_id=ch.chunk_id,
                content=ch.content,
                document_id=ch.document_id,
                document_title=ch.document_title,
                filename=ch.filename,
                page=ch.page,
                section_path=ch.section_path,
                content_hash=ch.content_hash,
            )
        )
    return anchors


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_documents_block(anchors: list[Anchor]) -> str:
    """Render the ``<documents>`` context block grouped by document."""
    by_doc: dict[str, list[Anchor]] = {}
    order: list[str] = []
    for a in anchors:
        if a.document_id not in by_doc:
            by_doc[a.document_id] = []
            order.append(a.document_id)
        by_doc[a.document_id].append(a)

    lines: list[str] = ["<documents>"]
    for doc_id in order:
        group = by_doc[doc_id]
        title = _xml_escape(group[0].document_title or group[0].filename)
        lines.append(f'  <document title="{title}" filename="{_xml_escape(group[0].filename)}">')
        for a in group:
            page = "" if a.page is None else f' page="{a.page}"'
            section = (
                f' section="{_xml_escape(a.section_path)}"' if a.section_path else ""
            )
            lines.append(f'    <chunk id="{a.marker}"{page}{section}>')
            lines.append(_xml_escape(a.content.strip()))
            lines.append("    </chunk>")
        lines.append("  </document>")
    lines.append("</documents>")
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You are PaperLens, a research assistant that answers questions strictly from "
    "the provided documents.\n"
    "Rules:\n"
    "1. Answer ONLY using information in the <documents> block. Do not use outside "
    "knowledge.\n"
    "2. After every claim, cite the supporting chunk(s) with their anchor id in "
    "square brackets, e.g. [C1] or [C2, C3]. Use the exact anchor ids shown on the "
    "<chunk> tags. Never invent an anchor id that is not present.\n"
    "3. Do NOT quote or copy long passages; write the answer in your own words and "
    "let the [C#] anchors point to the evidence.\n"
    "4. When a figure ([F#]) or table ([T#]) in the <figures>/<tables> block is "
    "relevant to your answer, you MAY cite it with its anchor, e.g. [F1] or [T2]. "
    "Only cite figure/table anchors that are actually present.\n"
    "4b. A request phrased as a command to display — 'show the figure', 'show me "
    "the diagram', 'diagram of X', 'show the table' — is answered by DESCRIBING the "
    "relevant figure/table in words and citing its anchor ([F1], [T2]). The "
    "interface renders the actual image alongside your text, so you never need to "
    "produce an image or apologize for not producing one — inability to literally "
    "display an artifact is NEVER a reason to abstain. First identify which figure/"
    "table the request means by matching its label and caption to what was asked, "
    "then explain what it depicts using the surrounding document text [C#]. Only if "
    "no present figure/table matches the request should you fall back to rule 6.\n"
    "5. Never state a numeric value whose only source is a figure caption or figure "
    "description — figures can be misread. Numbers must come from the document text "
    "or a table.\n"
    "6. Abstain ONLY when the <documents>/<figures>/<tables> block contains nothing "
    "that addresses the question. A request you cannot literally perform (displaying "
    "an image, rendering a diagram) is NOT grounds to abstain — see rule 4b. When "
    "you do abstain, reply with exactly: " + ABSTAIN_TEXT + " (and cite nothing).\n"
    "7. Be concise and factual."
)


def build_media_block(
    figures: list[FigureAnchor], tables: list[TableAnchor]
) -> str:
    """Render the ``<figures>`` / ``<tables>`` context blocks (may be empty).

    Figures expose only their caption text (never pixel-level numbers — rule 5).
    Tables expose their flat markdown / structured HTML body so the model can read
    real values from them.
    """
    lines: list[str] = []
    if figures:
        lines.append("<figures>")
        for f in figures:
            page = "" if f.page is None else f' page="{f.page}"'
            cap = _xml_escape(f.caption or f.label)
            lines.append(f'  <figure id="{f.marker}" label="{_xml_escape(f.label)}"{page}>')
            lines.append(f"  {cap}")
            lines.append("  </figure>")
        lines.append("</figures>")
    if tables:
        lines.append("<tables>")
        for t in tables:
            page = "" if t.page is None else f' page="{t.page}"'
            body = (t.markdown or t.html or "").strip()
            lines.append(f'  <table id="{t.marker}"{page}>')
            if t.caption:
                lines.append(f"  {_xml_escape(t.caption)}")
            if body:
                lines.append(_xml_escape(body))
            lines.append("  </table>")
        lines.append("</tables>")
    return "\n".join(lines)


def truncate_history(history: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Truncate to the last N turns / token budget, stripping old anchors.

    Old citation anchors are stripped from prior assistant text so a stale ``[C3]``
    from a previous turn cannot collide with this turn's anchor numbering.
    """
    if not history:
        return []
    turns = list(history)[-2 * settings.history_max_turns :]
    budget = settings.history_max_tokens
    out_rev: list[dict[str, str]] = []
    for msg in reversed(turns):
        role = msg.get("role", "user")
        content = msg.get("content", "") or ""
        if role == "assistant":
            content = _MARKER.sub("", content).strip()
        # ~4 chars/token heuristic (matches the ingest-side token estimate).
        cost = max(1, len(content) // 4)
        if budget - cost < 0 and out_rev:
            break
        budget -= cost
        if content:
            out_rev.append({"role": role, "content": content})
    return list(reversed(out_rev))


def build_messages(
    question: str,
    anchors: list[Anchor],
    history: Sequence[dict[str, str]],
    figures: list[FigureAnchor] | None = None,
    tables: list[TableAnchor] | None = None,
) -> list[dict[str, str]]:
    """Assemble the chat messages array for the LLM call."""
    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(truncate_history(history))
    block = build_documents_block(anchors)
    media = build_media_block(figures or [], tables or [])
    content = block
    if media:
        content = f"{block}\n\n{media}"
    messages.append(
        {
            "role": "user",
            "content": f"{content}\n\nQuestion: {question.strip()}",
        }
    )
    return messages


# --------------------------------------------------------------------------- #
# Streaming with a DeepSeek -> OpenAI circuit breaker
# --------------------------------------------------------------------------- #
@dataclass
class StreamUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str = settings.llm_model_default
    finish_reason: str = "stop"


def _deepseek_client():
    from openai import AsyncOpenAI

    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    return AsyncOpenAI(
        api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url
    )


def _openai_client():
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def stream_completion(
    messages: list[dict[str, str]], usage: StreamUsage
) -> AsyncIterator[str]:
    """Yield text deltas from the LLM, with a DeepSeek → OpenAI breaker.

    The breaker only trips BEFORE the first delta: if DeepSeek fails after any
    text has been emitted, we do NOT restart on OpenAI (that would duplicate
    text) — the error propagates and the caller closes the turn. ``usage`` is
    mutated in place with the real model + token counts; the model is also
    published to :data:`ACTUAL_MODEL` for persistence by the route.
    """
    try:
        async for delta in _stream_one(
            _deepseek_client(), settings.llm_model_default, messages, usage
        ):
            yield delta
        return
    except Exception:
        # Only safe to fall back if nothing has streamed yet.
        if usage.completion_tokens or usage.prompt_tokens is not None:
            raise
    # Fallback provider (circuit breaker). Records the REAL model used.
    async for delta in _stream_one(
        _openai_client(), settings.llm_model_fallback, messages, usage
    ):
        yield delta


async def _stream_one(
    client, model: str, messages: list[dict[str, str]], usage: StreamUsage
) -> AsyncIterator[str]:
    usage.model = model
    ACTUAL_MODEL.set(model)
    first = True
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            temperature=0.2,
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            if chunk.usage is not None:
                usage.prompt_tokens = chunk.usage.prompt_tokens
                usage.completion_tokens = chunk.usage.completion_tokens
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                usage.finish_reason = choice.finish_reason
            piece = choice.delta.content if choice.delta else None
            if piece:
                if first:
                    first = False
                yield piece
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# Grounding badge (build sheet: badge-only, NOT a hard gate)
# --------------------------------------------------------------------------- #
def grounding_status(answer_text: str, anchor: Anchor, marker: str) -> str:
    """Return "grounded" | "weak" for a used anchor (never "unverifiable" here).

    Badge-only heuristic (build sheet line 38 shows partial_ratio does not cleanly
    separate grounded from fabricated, so this only softens the badge; the hard
    gate is ``citation_quote_match == 1.0``, guaranteed because the quote is the
    server-dereferenced chunk content). We take the answer sentence(s) adjacent to
    the ``[marker]`` and fuzzy-match them against the chunk content.
    """
    from rapidfuzz import fuzz, utils

    window = _text_around_marker(answer_text, marker)
    if not window:
        return "weak"
    score = fuzz.partial_ratio(
        window, anchor.content, processor=utils.default_process
    )
    return "grounded" if score >= 60.0 else "weak"


def _text_around_marker(answer_text: str, marker: str) -> str:
    """Extract the answer text immediately preceding a ``[marker]`` occurrence."""
    # Find the bracket group containing the marker and grab up to ~240 chars before.
    for m in _MARKER.finditer(answer_text):
        if marker in m.group(1).replace(" ", ""):
            start = max(0, m.start() - 240)
            return answer_text[start : m.start()].strip()
    return ""


__all__ = [
    "Anchor",
    "FigureAnchor",
    "TableAnchor",
    "ABSTAIN_TEXT",
    "ACTUAL_MODEL",
    "StreamUsage",
    "build_anchors",
    "build_documents_block",
    "build_media_block",
    "build_messages",
    "truncate_history",
    "stream_completion",
    "grounding_status",
]
