"""Docling PDF parsing (Build Sheet §5.1).

Config (all from ``settings``): ``do_ocr=True`` (DEFAULT mode — only OCRs clusters
with no text cell), TableFormer ACCURATE, ``images_scale=2.0``, page cap
``settings.max_pages``. The CPU-bound ``convert`` runs in a thread executor so it
never blocks the event loop / ARQ heartbeat. Docling (torch) is imported lazily
INSIDE the function — the API process must not pay the 2-4 s torch import.

After parse we apply the text-yield gate: < ``settings.text_yield_gate`` chars per
page -> ``ParseError(PDF_NO_TEXT_LAYER)`` (a scanned PDF we refuse rather than
silently return empty chunks for).

Returns a :class:`ParsedDoc` holding the raw ``DoclingDocument`` (the chunker
walks it) plus extracted table/figure metadata.

Figures (P3): ``generate_picture_images=True`` is enabled so each ``PictureItem``
carries an embedded PIL image (``item.get_image(doc)``). The pipeline saves that
crop under ``{DATA_DIR}/figures/{document_id}/{figure_id}.png`` via
:func:`save_figure_crops` — no manual bbox math. Junk crops (tiny / thin / empty)
are filtered per the build sheet §P1 rules. The Docling caption
(``item.caption_text(doc)``) is the initial caption; the background captioning
stage (``app.rag.caption``) fills ``figures.vlm_summary`` after TEXT_READY.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.errors import ParseError, TooLarge


@dataclass
class ParsedTable:
    """One table extracted from the PDF (maps to a ``tables`` row)."""

    self_ref: str
    label: str | None
    caption: str | None
    html: str | None
    markdown_flat: str | None
    structure_kind: str  # "flat" | "spanned"
    n_rows: int | None
    n_cols: int | None
    page: int | None
    bbox: dict | None
    section_path: str | None


@dataclass
class ParsedFigure:
    """One figure extracted from the PDF (maps to a ``figures`` row)."""

    self_ref: str
    label: str | None
    caption: str | None
    page: int | None
    bbox: dict | None
    section_path: str | None
    vlm_status: str = "pending"
    image_path: str | None = None


@dataclass
class ParsedDoc:
    """Result of parsing a single PDF."""

    document: Any  # docling_core DoclingDocument (kept opaque here)
    page_count: int
    tables: list[ParsedTable] = field(default_factory=list)
    figures: list[ParsedFigure] = field(default_factory=list)


def _bbox_to_dict(bbox: Any) -> dict | None:
    try:
        return {
            "l": float(bbox.l),
            "t": float(bbox.t),
            "r": float(bbox.r),
            "b": float(bbox.b),
            "coord_origin": getattr(bbox.coord_origin, "value", str(bbox.coord_origin)),
        }
    except Exception:  # pragma: no cover - defensive
        return None


def _first_prov(item: Any) -> tuple[int | None, dict | None]:
    prov = getattr(item, "prov", None)
    if prov:
        p = prov[0]
        return getattr(p, "page_no", None), _bbox_to_dict(getattr(p, "bbox", None))
    return None, None


def _detect_structure(table_item: Any) -> str:
    """flat vs spanned (build sheet blocker #3).

    A table is ``spanned`` (keep HTML, preserve rowspan/colspan) when EITHER a
    cell spans >1 row/col OR the header occupies ≥2 rows (a multi-level header
    that markdown cannot represent). Otherwise ``flat`` (markdown).
    """
    try:
        data = table_item.data
        header_rows: set[int] = set()
        for cell in data.table_cells:
            if getattr(cell, "row_span", 1) > 1 or getattr(cell, "col_span", 1) > 1:
                return "spanned"
            if getattr(cell, "column_header", False):
                header_rows.add(int(getattr(cell, "start_row_offset_idx", 0)))
        if len(header_rows) >= 2:
            return "spanned"
    except Exception:  # pragma: no cover - defensive
        pass
    return "flat"


def _convert_sync(file_path: str, max_pages: int):
    """Blocking Docling convert. Runs in a thread executor.

    Lazy import keeps torch out of the API process import path.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = settings.do_ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = getattr(
        TableFormerMode, settings.tableformer_mode, TableFormerMode.ACCURATE
    )
    opts.images_scale = settings.images_scale
    # Figure crops ON: embeds a PIL image on each PictureItem for get_image(doc).
    # Table images stay OFF — tables render as HTML/markdown, so the raster is
    # pure memory/time cost.
    opts.generate_picture_images = True

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    try:
        result = converter.convert(file_path, max_num_pages=max_pages)
    except Exception as exc:  # Docling raises many concrete types; classify coarsely.
        msg = str(exc).lower()
        if "password" in msg or "encrypt" in msg:
            raise ParseError(f"encrypted PDF: {exc}", code="PDF_ENCRYPTED") from exc
        raise ParseError(f"docling convert failed: {exc}", code="PDF_CORRUPT") from exc
    return result.document


def _collect_metadata(doc: Any) -> tuple[list[ParsedTable], list[ParsedFigure]]:
    """Walk once to pull tables + figures with a nearest-heading section_path."""
    from docling_core.types.doc.document import (
        PictureItem,
        SectionHeaderItem,
        TableItem,
    )

    tables: list[ParsedTable] = []
    figures: list[ParsedFigure] = []
    stack: list[tuple[int, str]] = []  # (level, text)

    for item, _lvl in doc.iterate_items():
        if isinstance(item, SectionHeaderItem):
            level = int(getattr(item, "level", 1) or 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, (item.text or "").strip()))
            continue

        section_path = " > ".join(t for _, t in stack) or None

        if isinstance(item, TableItem):
            page, bbox = _first_prov(item)
            caption = (item.caption_text(doc) or "").strip() or None
            kind = _detect_structure(item)
            n_rows = getattr(item.data, "num_rows", None) if item.data else None
            n_cols = getattr(item.data, "num_cols", None) if item.data else None
            html = None
            markdown_flat = None
            try:
                if kind == "spanned":
                    html = item.export_to_html(doc)
                else:
                    markdown_flat = item.export_to_markdown(doc)
            except Exception:  # pragma: no cover - fall back to markdown
                try:
                    markdown_flat = item.export_to_markdown(doc)
                except Exception:
                    markdown_flat = None
            tables.append(
                ParsedTable(
                    self_ref=item.self_ref,
                    label=(getattr(item, "label", None) and "Table") or None,
                    caption=caption,
                    html=html,
                    markdown_flat=markdown_flat,
                    structure_kind=kind,
                    n_rows=n_rows,
                    n_cols=n_cols,
                    page=page,
                    bbox=bbox,
                    section_path=section_path,
                )
            )
        elif isinstance(item, PictureItem):
            page, bbox = _first_prov(item)
            caption = (item.caption_text(doc) or "").strip() or None
            figures.append(
                ParsedFigure(
                    self_ref=item.self_ref,
                    label=None,
                    caption=caption,
                    page=page,
                    bbox=bbox,
                    section_path=section_path,
                )
            )
    return tables, figures


async def parse_pdf(file_path: str, *, max_pages: int | None = None) -> ParsedDoc:
    """Parse a PDF into a :class:`ParsedDoc`.

    Raises ``ParseError`` (REJECTED) on no-text-layer / corrupt / encrypted, and
    ``TooLarge`` if the page count blows past a hard multiple of the cap.
    """
    cap = max_pages or settings.max_pages
    loop = asyncio.get_running_loop()
    doc = await loop.run_in_executor(None, _convert_sync, file_path, cap)

    # Page count from the parsed document.
    page_count = len(getattr(doc, "pages", {}) or {}) or 0

    # Text-yield gate: refuse scans up front.
    text_len = len((doc.export_to_text() or "").strip())
    per_page = text_len / page_count if page_count else 0
    if page_count == 0:
        raise ParseError("no pages parsed", code="PDF_CORRUPT")
    if per_page < settings.text_yield_gate:
        raise ParseError(
            f"low text yield ({per_page:.0f} chars/page < "
            f"{settings.text_yield_gate}); likely a scan with no text layer",
            code="PDF_NO_TEXT_LAYER",
        )

    tables, figures = _collect_metadata(doc)
    return ParsedDoc(
        document=doc, page_count=page_count, tables=tables, figures=figures
    )


def figure_label(caption: str | None, fallback: str | None = None) -> str:
    """Human label for a figure. Never ``None`` (FigureEvent.label is required).

    Prefers a ``Figure N`` prefix parsed from the caption, then any stored label,
    then a bare ``"Figure"``.
    """
    if caption:
        m = re.match(r"\s*(fig(?:ure)?\.?\s*\d+[a-z]?)", caption, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".").replace("Fig ", "Figure ")
    return (fallback or "Figure").strip() or "Figure"


def save_figure_crops(
    doc: Any,
    jobs: list[tuple[str, str]],
    dest_dir: str,
) -> dict[str, str]:
    """Save PIL crops for the given ``(figure_id, self_ref)`` jobs.

    BLOCKING (PIL encode + file writes) — call via ``run_in_executor``. For each
    job resolves the ``PictureItem`` by ref, pulls its embedded image
    (``get_image``), applies the junk filter (min side / aspect / byte size from
    ``settings``), and writes ``{dest_dir}/{figure_id}.png``. Returns a map of
    ``figure_id -> absolute image path`` for the crops that survived. A picture
    with no image, or one filtered as junk, is simply absent from the map (the
    Figure row keeps ``image_path=None`` and is never emitted for display).
    """
    from docling_core.types.doc.document import RefItem

    out: dict[str, str] = {}
    dpath = Path(dest_dir)
    for fig_id, self_ref in jobs:
        try:
            item = RefItem(cref=self_ref).resolve(doc)
            image = item.get_image(doc)
            if image is None:
                continue
            w, h = image.size
            if w < settings.figure_min_px or h < settings.figure_min_px:
                continue
            long_side, short_side = max(w, h), max(1, min(w, h))
            if long_side / short_side > settings.figure_max_aspect:
                continue
            dpath.mkdir(parents=True, exist_ok=True)
            target = dpath / f"{fig_id}.png"
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.save(target, format="PNG")
            if target.stat().st_size < settings.figure_min_bytes:
                target.unlink(missing_ok=True)
                continue
            out[fig_id] = str(target)
        except Exception:  # pragma: no cover - one bad crop must not fail ingest
            continue
    return out


def warm_models() -> None:
    """Force Docling to load its models (called from the worker's on_startup).

    Converts a tiny in-memory blank PDF so the first real ingest does not pay the
    model download/load cost. Best-effort: never raises into startup.
    """
    try:
        import pymupdf

        doc = pymupdf.open()
        doc.new_page(width=200, height=200)
        pdf_bytes = doc.tobytes()
        doc.close()

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tf:
            tf.write(pdf_bytes)
            tf.flush()
            _convert_sync(tf.name, 1)
    except Exception:  # pragma: no cover - warm-up must never break startup
        pass


__all__ = [
    "ParsedDoc",
    "ParsedTable",
    "ParsedFigure",
    "parse_pdf",
    "save_figure_crops",
    "figure_label",
    "warm_models",
    "TooLarge",
]
