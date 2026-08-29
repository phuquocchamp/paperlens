"""GenEvent — protocol-neutral domain events for the answer stream.

``rag.answer_stream`` yields these. Exactly one module (``app/streaming/render_aisdk.py``)
knows they become Vercel AI SDK "UI Message Stream" frames — everything upstream
stays protocol-neutral so the wire format can change without touching RAG code.

Frame ordering and invariants are fixed in CONTRACT.md (Build Sheet §4). The key
rules encoded by these types:
  - ``data-citation`` is the ONE source of truth for a citation (no dual-write).
  - Citations and figures are emitted BEFORE ``text-start`` (sources render first).
  - Second ``data-citation`` for the same id OVERWRITES the whole object after the
    async grounding check (pending → grounded | weak | unverifiable). Never retracts.
  - A figure is re-emitted with the SAME id and ``display="cited"`` when the model
    cites it — a promotion, not a re-selection.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Stage(str, Enum):
    """Transient pipeline stage surfaced via data-status (part is transient)."""

    MODERATING = "moderating"
    RETRIEVING = "retrieving"
    RERANKING = "reranking"
    READING_FIGURES = "reading_figures"
    GENERATING = "generating"


class VerifyStatus(str, Enum):
    PENDING = "pending"          # first emission, before grounding check
    GROUNDED = "grounded"
    WEAK = "weak"
    UNVERIFIABLE = "unverifiable"
    SOURCE_CHANGED = "source_changed"  # content_hash drift at resolve time


class FigureDisplay(str, Enum):
    CANDIDATE = "candidate"      # preloaded before text-start (no layout shift)
    CITED = "cited"             # promoted into the answer flow when [F2] is cited


class TableStructure(str, Enum):
    FLAT = "flat"                # rendered as markdown
    SPANNED = "spanned"          # rendered as HTML (rowspan/colspan preserved)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
class StartEvent(BaseModel):
    type: Literal["start"] = "start"
    request_id: str
    model: str


class StatusEvent(BaseModel):
    """Transient — cleared by the client when status != streaming."""

    type: Literal["status"] = "status"
    stage: Stage
    detail: str | None = None  # e.g. "Searching 4 documents…"


class TextStartEvent(BaseModel):
    type: Literal["text-start"] = "text-start"


class TextDeltaEvent(BaseModel):
    type: Literal["text-delta"] = "text-delta"
    delta: str  # markers like [C3] are kept verbatim in the text


class TextEndEvent(BaseModel):
    type: Literal["text-end"] = "text-end"


class FinishEvent(BaseModel):
    type: Literal["finish"] = "finish"
    finish_reason: str = "stop"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


# --------------------------------------------------------------------------- #
# Evidence parts
# --------------------------------------------------------------------------- #
class CitationEvent(BaseModel):
    """The single source of truth for a citation.

    Emitted first with status=pending (before text), then re-emitted with the
    SAME id after the grounding check, overwriting the entire object.
    """

    type: Literal["citation"] = "citation"
    id: str                       # e.g. "cit-C3"
    marker: str                   # e.g. "C3" — matches MARKER_RE group
    quote: str                    # verbatim, server-dereferenced from chunks.content
    document_id: str
    document_title: str
    filename: str
    page: int
    section_path: str | None = None
    content_hash: str             # snapshot; drift ⇒ SOURCE_CHANGED at render
    verify_status: VerifyStatus = VerifyStatus.PENDING


class FigureEvent(BaseModel):
    type: Literal["figure"] = "figure"
    id: str                       # e.g. "fig-{uuid}"
    marker: str | None = None     # e.g. "F2" once cited
    label: str                    # e.g. "Figure 2"
    image_url: str
    page: int
    caption: str | None = None
    width: int | None = None      # set dimensions ahead of load to avoid layout shift
    height: int | None = None
    display: FigureDisplay = FigureDisplay.CANDIDATE


class TableEvent(BaseModel):
    type: Literal["table"] = "table"
    id: str                       # e.g. "tbl-{uuid}"
    marker: str | None = None     # e.g. "T1"
    label: str
    page: int
    caption: str | None = None
    structure_kind: TableStructure = TableStructure.FLAT
    html: str | None = None       # populated when structure_kind == SPANNED
    markdown: str | None = None   # populated when structure_kind == FLAT


# --------------------------------------------------------------------------- #
# Side channels
# --------------------------------------------------------------------------- #
class NoticeEvent(BaseModel):
    type: Literal["notice"] = "notice"
    text: str


class ErrorEvent(BaseModel):
    """Typed error. errorText MUST NOT leak provider/stack details."""

    type: Literal["error"] = "error"
    error_type: str               # e.g. "rate_limited", "upstream_unavailable"
    message: str


GenEvent = Annotated[
    StartEvent | StatusEvent | CitationEvent | FigureEvent | TableEvent | TextStartEvent | TextDeltaEvent | TextEndEvent | NoticeEvent | ErrorEvent | FinishEvent,
    Field(discriminator="type"),
]
