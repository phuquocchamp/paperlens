"""All 11 PaperLens tables (Build Sheet §3).

Backend-owned: projects, documents, conversations, messages, ingestion_jobs,
message_feedback, collection_registry.
RAG-owned:     chunks, figures, tables, chunk_figures.

Cross-cutting conventions (see also base.py / session.py):
  - UUID primary keys, generated client-side (``default=uuid.uuid4``) so we do
    not depend on a specific Postgres ``gen_random_uuid()`` availability.
  - Server-side, timezone-aware timestamps (``server_default=func.now()``).
  - Deletes are controlled cascades via application logic (§3 projects note),
    so NO FK carries ``ON DELETE CASCADE``. Soft delete uses ``deleted_at``.
  - ``documents.status`` / ``documents.stage`` and the RAG discriminator
    columns are TEXT + CHECK, never DB enums (§3). The only real DB enum is
    ``messages.role``.
  - Every relationship sets ``lazy="raise"`` — no implicit lazy I/O.
  - Every CheckConstraint is explicitly named (required by the ``ck`` naming
    convention template).

Two columns are added beyond §3's prose because §3's own SQL / the pinned
domain events require them (logged as decisions in the delivery report):
  - ``documents.title``   — selected by the join-back query (§3 line 145) and
    carried by ``CitationEvent`` (events.py).
  - ``chunks.table_id``   — §3 tables note: "child chunk trỏ về qua table_id".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.status import STAGE_VALUES, STATUS_VALUES

# --------------------------------------------------------------------------- #
# Discriminator vocabularies (TEXT + CHECK). Kept as module constants so the
# CHECK literals in the ORM and in 0001_initial come from one place.
# --------------------------------------------------------------------------- #
CHUNK_TYPE_VALUES: tuple[str, ...] = (
    "text",
    "table_child",
    "table_parent",
    "fig_caption",
    "figure",
)
VLM_STATUS_VALUES: tuple[str, ...] = ("pending", "done", "failed")
STRUCTURE_KIND_VALUES: tuple[str, ...] = ("flat", "spanned")
LINK_TYPE_VALUES: tuple[str, ...] = ("explicit_ref", "caption", "same_page")
MESSAGE_ROLE_VALUES: tuple[str, ...] = ("user", "assistant", "system")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """Build a ``col IN ('a', 'b', ...)`` CHECK expression."""
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


# Reusable column type aliases.
_UUID = UUID(as_uuid=True)
_TS = TIMESTAMP(timezone=True)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(_UUID, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(_TS, nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------- #
# Backend-owned tables
# --------------------------------------------------------------------------- #
class Project(Base):
    """A project — the tenant scope for documents, chunks and conversations.

    Deletion is a controlled cascade via ``rag.delete_project()`` (§3), never a
    blind ``ON DELETE CASCADE``.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    documents: Mapped[list[Document]] = relationship(
        back_populates="project", lazy="raise"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="project", lazy="raise"
    )


class Document(Base):
    """An uploaded PDF and its ingestion state.

    ``status`` follows the DocumentStatus state machine; ``stage`` is the
    PROCESSING sub-stage (NULL outside PROCESSING). ``started_at`` feeds the
    stuck-document sweep. Dedup is enforced by a partial unique index on
    ``(project_id, file_sha256) WHERE deleted_at IS NULL``.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id"), nullable=False
    )
    # Added beyond §3 prose: required by the join-back query and CitationEvent.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'uploaded'")
    )
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Semantic percentage 0-100 (SmallInteger + CHECK). NULL until processing.
    progress: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    # Monotonic re-ingest counter; matches Qdrant payload.ingest_generation.
    ingest_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Denormalised counters.
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    figure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # M9 summary card
    deleted_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    project: Mapped[Project] = relationship(
        back_populates="documents", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint(_in_clause("status", STATUS_VALUES), name="status_valid"),
        CheckConstraint(
            f"stage IS NULL OR {_in_clause('stage', STAGE_VALUES)}",
            name="stage_valid",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="progress_range",
        ),
        # Dedup: one live document per (project, sha256).
        Index(
            "uq_documents_project_id_file_sha256",
            "project_id",
            "file_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Polling index for pending/in-flight documents.
        Index(
            "ix_documents_pending",
            "status",
            postgresql_where=text(
                "status IN ('uploaded', 'queued', 'processing') "
                "AND deleted_at IS NULL"
            ),
        ),
    )


class Conversation(Base):
    """A chat conversation, scoped to a project. Soft-deleted via deleted_at."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    project: Mapped[Project] = relationship(
        back_populates="conversations", lazy="raise"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", lazy="raise"
    )


class Message(Base):
    """A single chat message.

    ``parts`` is the UIMessage.parts JSONB, verbatim — the single source of
    truth for rendering (§3). Citations live inside ``parts`` (no separate
    render table). ``role`` is the one real DB enum in the schema. The
    remaining columns are the tier-0 observability fields.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        SAEnum(*MESSAGE_ROLE_VALUES, name="message_role"), nullable=False
    )
    parts: Mapped[list] = mapped_column(JSONB, nullable=False)

    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = _created_at()

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", lazy="raise"
    )


class IngestionJob(Base):
    """Per-attempt ingestion telemetry (ARQ keeps no history — §3)."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("documents.id"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    failed_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_timings_ms: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class MessageFeedback(Base):
    """Human feedback on a message. Postgres is the home; Langfuse is optional."""

    __tablename__ = "message_feedback"

    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("messages.id"), nullable=False
    )
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_to_langfuse: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created_at()


class CollectionRegistry(Base):
    """Registry of Qdrant collections. Exactly one is active at a time.

    The DB guarantees the invariant with a partial unique index
    (``WHERE is_active``).
    """

    __tablename__ = "collection_registry"

    id: Mapped[uuid.UUID] = _pk()
    collection_name: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    dims: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index(
            "uq_collection_registry_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


# --------------------------------------------------------------------------- #
# RAG-owned tables
# --------------------------------------------------------------------------- #
class Figure(Base):
    """A figure cropped from the PDF, with an (optionally) VLM-generated summary.

    ``vlm_summary`` is expensive to regenerate, so it lives in Postgres (not
    Redis).
    """

    __tablename__ = "figures"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("documents.id"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)  # "Figure 2"
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)  # from Docling
    vlm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    vlm_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            _in_clause("vlm_status", VLM_STATUS_VALUES), name="vlm_status_valid"
        ),
    )


class Table(Base):
    """A table extracted from the PDF.

    ``structure_kind`` drives the render/inject router: spanned tables keep
    HTML (rowspan/colspan), flat tables use markdown. The parent row is not
    embedded; child chunks reference it via ``chunks.table_id``.
    """

    __tablename__ = "tables"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("documents.id"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)  # when spanned
    markdown_flat: Mapped[str | None] = mapped_column(Text, nullable=True)  # when flat
    structure_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'flat'")
    )
    n_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_cols: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            _in_clause("structure_kind", STRUCTURE_KIND_VALUES),
            name="structure_kind_valid",
        ),
    )


class Chunk(Base):
    """A retrieval unit.

    ``content`` is canonical (displayed + verified against). ``embed_text`` is
    ``prefix + content`` — the string that actually gets embedded. Keeping them
    separate is mandatory: mixing them breaks quote-match. There is deliberately
    NO ``embed_model`` column (txn3 blocker fix); model/version live in the
    Qdrant payload + collection_registry. A figure gets a shadow row
    (``chunk_type='figure'``) to preserve the "2 Postgres queries" join-back
    invariant. A table-parent row has ``is_embedded=false``.
    """

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("documents.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    embed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. [[1, "Methods"], [3, "Loss"]]
    heading_levels: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    chunk_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'text'")
    )
    is_embedded: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    is_quarantined: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    # Shadow-row / parent links (nullable). No ON DELETE CASCADE.
    figure_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("figures.id"), nullable=True
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("tables.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            _in_clause("chunk_type", CHUNK_TYPE_VALUES), name="chunk_type_valid"
        ),
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_project_id", "project_id"),
    )


class ChunkFigure(Base):
    """N-M edge between chunks and figures, with attributes on the edge.

    ``link_type`` records how the link was found; ``confidence`` scores it. The
    same (chunk, figure) pair may legitimately be linked by more than one
    ``link_type`` (e.g. both explicit_ref and same_page), so the uniqueness key
    includes ``link_type``; P3 merges by MAX(confidence).
    """

    __tablename__ = "chunk_figures"

    id: Mapped[uuid.UUID] = _pk()
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("chunks.id"), nullable=False
    )
    figure_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("figures.id"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            _in_clause("link_type", LINK_TYPE_VALUES), name="link_type_valid"
        ),
        UniqueConstraint(
            "chunk_id", "figure_id", "link_type", name="chunk_figure_link"
        ),
    )
