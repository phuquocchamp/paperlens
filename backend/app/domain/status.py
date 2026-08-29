"""DocumentStatus state machine — the ingestion lifecycle (Build Sheet §3).

This encodes the state machine drawn in §3 (lines 112-123 of the build sheet):

    UPLOADED -> QUEUED -> PROCESSING(stage: parsing -> cropping -> captioning*
        -> chunking -> embedding -> indexing)
        -> TEXT_READY -> FULL_READY
    PROCESSING -> REJECTED  (ParseError | TooLarge | ContentBlocked — NO retry)
    PROCESSING -> FAILED    (Embedding | VectorStore | Storage — ARQ retry)
    PROCESSING -> QUEUED    (CancelledError — re-queue)
    any        -> DELETED   (soft)

    TEXT_READY arrives right after indexing the text chunks — chat opens there.
    Captioning continues in the background; when done -> FULL_READY.
    Sweep cron: PROCESSING older than 45 min (by started_at) -> FAILED + Retry.

Design notes carried into code:
  - REJECTED is terminal (no retry): the failure is intrinsic to the file
    (a bad parse, too large, blocked content). It never returns to QUEUED.
  - FAILED is retryable: a transient infra failure (embedding provider,
    vector store, object storage). ARQ / the "Retry" button re-queues it,
    hence the FAILED -> QUEUED edge. The REJECTED/FAILED asymmetry is the
    whole reason the two states are separate.
  - PROCESSING -> QUEUED handles a CancelledError mid-job (worker killed,
    job cancelled): the document is re-queued rather than lost.
  - any -> DELETED is the soft-delete edge, reachable from every state and
    idempotent (DELETED -> DELETED is allowed).

`IngestStage` is the PROCESSING sub-state vocabulary. It is deliberately NOT
named ``Stage`` to avoid colliding with ``app.domain.events.Stage`` (the
answer-stream status stage). ``documents.stage`` uses these values.
"""

from __future__ import annotations

from enum import Enum


class DocumentStatus(str, Enum):
    """Top-level ingestion status of a document.

    Stored in ``documents.status`` as TEXT + CHECK constraint (never a DB
    enum, per §3). Values are lowercase to match the ``events.py`` style.
    """

    UPLOADED = "uploaded"        # file stored, not yet queued
    QUEUED = "queued"            # waiting for an ARQ worker
    PROCESSING = "processing"    # a worker is running the pipeline (see IngestStage)
    TEXT_READY = "text_ready"    # text chunks indexed — chat can open here
    FULL_READY = "full_ready"    # captioning finished — figures fully available
    REJECTED = "rejected"        # intrinsic failure, terminal, no retry
    FAILED = "failed"            # transient failure, retryable via ARQ / Retry
    DELETED = "deleted"          # soft-deleted


class IngestStage(str, Enum):
    """PROCESSING sub-stage, surfaced in ``documents.stage`` for polling.

    NULL outside of PROCESSING. Named ``IngestStage`` (not ``Stage``) to
    avoid collision with ``app.domain.events.Stage``.
    """

    PARSING = "parsing"
    CROPPING = "cropping"
    CAPTIONING = "captioning"    # runs in the background; continues past TEXT_READY
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"


class IllegalTransition(ValueError):
    """Raised when a status transition is not allowed by the state machine."""


# --------------------------------------------------------------------------- #
# Transition table
# --------------------------------------------------------------------------- #
# Explicit allowed target states for each source state. ``any -> DELETED`` is
# applied uniformly below (every state may be soft-deleted, including DELETED
# itself, which makes soft delete idempotent).
_ALLOWED: dict[DocumentStatus, set[DocumentStatus]] = {
    DocumentStatus.UPLOADED: {DocumentStatus.QUEUED},
    DocumentStatus.QUEUED: {DocumentStatus.PROCESSING},
    DocumentStatus.PROCESSING: {
        DocumentStatus.TEXT_READY,   # indexing text chunks done
        DocumentStatus.REJECTED,     # ParseError | TooLarge | ContentBlocked
        DocumentStatus.FAILED,       # Embedding | VectorStore | Storage
        DocumentStatus.QUEUED,       # CancelledError — re-queue
    },
    DocumentStatus.TEXT_READY: {DocumentStatus.FULL_READY},
    DocumentStatus.FULL_READY: set(),
    DocumentStatus.REJECTED: set(),  # terminal — no retry
    DocumentStatus.FAILED: {DocumentStatus.QUEUED},  # ARQ / Retry re-queues
    DocumentStatus.DELETED: set(),
}

# Soft delete is reachable from every state (including DELETED -> DELETED).
for _state in DocumentStatus:
    _ALLOWED[_state].add(DocumentStatus.DELETED)


def can_transition(current: DocumentStatus, target: DocumentStatus) -> bool:
    """Return True iff ``current -> target`` is a legal transition."""
    return target in _ALLOWED[current]


def transition(current: DocumentStatus, target: DocumentStatus) -> DocumentStatus:
    """Validate and return ``target``, or raise :class:`IllegalTransition`.

    This is a pure guard: it performs no I/O. Callers persist the returned
    status themselves.
    """
    if not can_transition(current, target):
        raise IllegalTransition(
            f"Illegal DocumentStatus transition: {current.value} -> {target.value}"
        )
    return target


# Values used to build the ``documents.status`` CHECK constraint in models.py
# and migration 0001. Kept here so the CHECK literals and the enum never drift.
STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in DocumentStatus)
STAGE_VALUES: tuple[str, ...] = tuple(s.value for s in IngestStage)
