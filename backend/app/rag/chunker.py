"""Custom 3-tier chunker (Build Sheet §5.2).

Why not ``HybridChunker``: it has no target size, its overlap is always 0, and its
merge key is wrong. We walk the tree ourselves (keeping provenance) and control
merge / split / overlap explicitly.

    Tier 0  walk ``doc.iterate_items()`` keeping a heading stack WITH levels;
            emit a ``RawPiece`` per text/heading item.
    Tier 1  greedy-merge pieces sharing ``section_path[:2]`` (hard boundary at
            H2; sibling H3s merge) up to CHUNK_TARGET, never exceeding CHUNK_MAX;
            force-split any single oversize piece; OVERLAP is applied ONLY at
            those forced boundaries (~20-30% of boundaries — keep the gain, drop
            the tax of overlapping every chunk).

Each emitted chunk carries ``content`` (canonical, displayed + verified against)
and ``embed_text`` (``[Paper][section path]`` prefix + content — the string that
is actually embedded). ``content_hash`` is sha256 of ``content``.

No Docling import at module scope (kept lazy inside the walk) so importing this
module stays cheap.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.config import settings

# Docling labels whose text is body content we chunk. Captions belong to their
# figure/table (handled by the pipeline); headers/footers/footnotes are dropped.
_BODY_LABELS = {"text", "paragraph", "list_item", "formula", "code", "reference"}


def count_tokens(text: str) -> int:
    """Approximate token count.

    tiktoken is intentionally not a dependency (pyproject is pinned); the classic
    ~4-chars-per-token heuristic is more than accurate enough here — even a 3x
    error on CHUNK_MAX=1200 stays far under the 8192 embedding input limit. One
    swappable function so a real tokenizer can drop in later.
    """
    return max(1, len(text) // 4)


def make_embed_text(content: str, section_path: str | None) -> str:
    """Prepend the ``[Paper][section path]`` retrieval prefix.

    The paper title is deliberately NOT included: ``ingest_document`` must not
    read the ``documents`` table and has no title. §9 pins OFFTOPIC_THRESHOLD
    calibration to this exact prefix format — keep it in this one function.
    """
    if section_path:
        return f"[Paper][{section_path}]\n{content}"
    return f"[Paper]\n{content}"


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class RawPiece:
    """Tier-0 unit: one text/heading item with provenance."""

    text: str
    section_path: str | None
    heading_levels: list[list]  # [[1, "Methods"], [3, "Loss"]]
    page: int | None
    kind: str  # "heading" | "text"

    @property
    def merge_key(self) -> tuple:
        # Hard boundary at H2: first two heading levels decide the group.
        return tuple(tuple(h) for h in self.heading_levels[:2])


@dataclass
class RawChunk:
    """Tier-1 output: a retrieval unit ready to persist + embed."""

    content: str
    embed_text: str
    content_hash: str
    section_path: str | None
    heading_levels: list[list]
    page_start: int | None
    page_end: int | None
    chunk_type: str = "text"
    forced_boundary: bool = False  # this chunk began at a forced split boundary
    pages: list[int] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Tier 0 — tree walk
# --------------------------------------------------------------------------- #
def walk_pieces(doc) -> list[RawPiece]:
    """Walk the DoclingDocument, emitting RawPieces with a live heading stack."""
    from docling_core.types.doc.document import SectionHeaderItem, TextItem

    pieces: list[RawPiece] = []
    stack: list[tuple[int, str]] = []

    for item, _lvl in doc.iterate_items():
        if isinstance(item, SectionHeaderItem):
            level = int(getattr(item, "level", 1) or 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            text = (item.text or "").strip()
            stack.append((level, text))
            if text:
                pieces.append(
                    RawPiece(
                        text=text,
                        section_path=_section_path(stack),
                        heading_levels=[[lv, tx] for lv, tx in stack],
                        page=_page_of(item),
                        kind="heading",
                    )
                )
            continue

        if isinstance(item, TextItem):
            label = getattr(getattr(item, "label", None), "value", None)
            if label not in _BODY_LABELS:
                continue
            text = (item.text or "").strip()
            if not text:
                continue
            pieces.append(
                RawPiece(
                    text=text,
                    section_path=_section_path(stack),
                    heading_levels=[[lv, tx] for lv, tx in stack],
                    page=_page_of(item),
                    kind="text",
                )
            )
    return pieces


def _section_path(stack: list[tuple[int, str]]) -> str | None:
    return " > ".join(t for _, t in stack if t) or None


def _page_of(item) -> int | None:
    prov = getattr(item, "prov", None)
    if prov:
        return getattr(prov[0], "page_no", None)
    return None


# --------------------------------------------------------------------------- #
# Tier 1 — assemble (merge / split / overlap)
# --------------------------------------------------------------------------- #
_WS = re.compile(r"\s+")


def _force_split(piece: RawPiece) -> list[tuple[str, bool]]:
    """Split an oversize piece into <=CHUNK_TARGET segments.

    Returns ``(text, forced_boundary)`` tuples. Every segment after the first
    starts at a forced boundary and carries CHUNK_OVERLAP tokens of tail context
    from the previous segment — the only place overlap is applied.
    """
    target_chars = settings.chunk_target * 4
    overlap_chars = settings.chunk_overlap * 4
    words = _WS.split(piece.text.strip())

    segments: list[tuple[str, bool]] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        wl = len(w) + 1
        if cur and cur_len + wl > target_chars:
            segments.append((" ".join(cur), len(segments) > 0))
            # Seed the next segment with an overlap tail of the current one.
            tail: list[str] = []
            tail_len = 0
            for tw in reversed(cur):
                if tail_len + len(tw) + 1 > overlap_chars:
                    break
                tail.insert(0, tw)
                tail_len += len(tw) + 1
            cur = list(tail)
            cur_len = tail_len
        cur.append(w)
        cur_len += wl
    if cur:
        segments.append((" ".join(cur), len(segments) > 0))
    return segments


def _make_chunk(pieces: list[RawPiece], *, forced: bool = False) -> RawChunk:
    content = "\n\n".join(p.text for p in pieces).strip()
    section_path = pieces[0].section_path
    heading_levels = pieces[0].heading_levels
    pages = [p.page for p in pieces if p.page is not None]
    return RawChunk(
        content=content,
        embed_text=make_embed_text(content, section_path),
        content_hash=_hash(content),
        section_path=section_path,
        heading_levels=heading_levels,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        forced_boundary=forced,
        pages=pages,
    )


def _make_chunk_from_text(text: str, ref: RawPiece, *, forced: bool) -> RawChunk:
    content = text.strip()
    return RawChunk(
        content=content,
        embed_text=make_embed_text(content, ref.section_path),
        content_hash=_hash(content),
        section_path=ref.section_path,
        heading_levels=ref.heading_levels,
        page_start=ref.page,
        page_end=ref.page,
        forced_boundary=forced,
        pages=[ref.page] if ref.page is not None else [],
    )


def assemble(pieces: list[RawPiece]) -> list[RawChunk]:
    """Tier-1 greedy merge with H2 boundary + forced-split overlap."""
    target = settings.chunk_target
    cmax = settings.chunk_max

    chunks: list[RawChunk] = []
    buf: list[RawPiece] = []
    buf_tokens = 0
    buf_key: tuple | None = None

    def flush() -> None:
        nonlocal buf, buf_tokens, buf_key
        if buf:
            chunks.append(_make_chunk(buf))
            buf, buf_tokens, buf_key = [], 0, None

    for piece in pieces:
        ptok = count_tokens(piece.text)

        # Oversize single piece -> force-split into its own chunks (with overlap).
        if ptok > cmax:
            flush()
            for seg_text, forced in _force_split(piece):
                chunks.append(_make_chunk_from_text(seg_text, piece, forced=forced))
            continue

        if buf and piece.merge_key != buf_key:
            flush()

        if buf and buf_tokens + ptok > cmax:
            flush()

        if not buf:
            buf_key = piece.merge_key
        buf.append(piece)
        buf_tokens += ptok

        if buf_tokens >= target:  # soft stop
            flush()

    flush()
    return _merge_tiny(chunks)


def _merge_tiny(chunks: list[RawChunk]) -> list[RawChunk]:
    """Fold chunks below CHUNK_MIN_MERGE into the previous same-section chunk."""
    if not chunks:
        return chunks
    out: list[RawChunk] = []
    for ch in chunks:
        if (
            out
            and count_tokens(ch.content) < settings.chunk_min_merge
            and out[-1].section_path == ch.section_path
            and not ch.forced_boundary
            and count_tokens(out[-1].content) + count_tokens(ch.content)
            <= settings.chunk_max
        ):
            prev = out[-1]
            merged = f"{prev.content}\n\n{ch.content}".strip()
            pages = prev.pages + ch.pages
            out[-1] = RawChunk(
                content=merged,
                embed_text=make_embed_text(merged, prev.section_path),
                content_hash=_hash(merged),
                section_path=prev.section_path,
                heading_levels=prev.heading_levels,
                page_start=min(pages) if pages else prev.page_start,
                page_end=max(pages) if pages else prev.page_end,
                forced_boundary=prev.forced_boundary,
                pages=pages,
            )
        else:
            out.append(ch)
    return out


def chunk_document(doc) -> list[RawChunk]:
    """Full 3-tier pipeline: walk -> assemble -> merge-tiny."""
    return assemble(walk_pieces(doc))


__all__ = [
    "RawPiece",
    "RawChunk",
    "count_tokens",
    "make_embed_text",
    "walk_pieces",
    "assemble",
    "chunk_document",
]
