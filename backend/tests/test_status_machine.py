"""Unit tests for the DocumentStatus state machine.

These must run WITHOUT a database — they import only ``app.domain.status``.
"""

from __future__ import annotations

import pytest

from app.domain.status import (
    STAGE_VALUES,
    STATUS_VALUES,
    DocumentStatus,
    IllegalTransition,
    IngestStage,
    can_transition,
    transition,
)

S = DocumentStatus


# --------------------------------------------------------------------------- #
# Legal transitions (the happy path through the pipeline)
# --------------------------------------------------------------------------- #
LEGAL = [
    (S.UPLOADED, S.QUEUED),
    (S.QUEUED, S.PROCESSING),
    (S.PROCESSING, S.TEXT_READY),   # text chunks indexed — chat opens
    (S.TEXT_READY, S.FULL_READY),   # captioning finished
    (S.PROCESSING, S.REJECTED),     # ParseError | TooLarge | ContentBlocked
    (S.PROCESSING, S.FAILED),       # Embedding | VectorStore | Storage
    (S.PROCESSING, S.QUEUED),       # CancelledError — re-queue
    (S.FAILED, S.QUEUED),           # ARQ retry / "Retry" button
]


@pytest.mark.parametrize(("src", "dst"), LEGAL)
def test_legal_transitions(src: DocumentStatus, dst: DocumentStatus) -> None:
    assert can_transition(src, dst) is True
    assert transition(src, dst) is dst


# --------------------------------------------------------------------------- #
# Illegal transitions
# --------------------------------------------------------------------------- #
ILLEGAL = [
    # REJECTED is terminal — no retry. This is the core asymmetry vs FAILED.
    (S.REJECTED, S.QUEUED),
    (S.REJECTED, S.PROCESSING),
    # FULL_READY is terminal.
    (S.FULL_READY, S.QUEUED),
    (S.FULL_READY, S.PROCESSING),
    # Cannot skip states.
    (S.UPLOADED, S.PROCESSING),
    (S.UPLOADED, S.TEXT_READY),
    (S.QUEUED, S.TEXT_READY),
    (S.QUEUED, S.FULL_READY),
    # Cannot go backwards.
    (S.TEXT_READY, S.PROCESSING),
    (S.PROCESSING, S.UPLOADED),
    (S.FULL_READY, S.TEXT_READY),
    # FAILED only re-queues; it does not jump straight back into PROCESSING.
    (S.FAILED, S.PROCESSING),
    (S.FAILED, S.TEXT_READY),
    # A soft-deleted document does not resurrect.
    (S.DELETED, S.QUEUED),
    (S.DELETED, S.PROCESSING),
    (S.DELETED, S.UPLOADED),
]


@pytest.mark.parametrize(("src", "dst"), ILLEGAL)
def test_illegal_transitions(src: DocumentStatus, dst: DocumentStatus) -> None:
    assert can_transition(src, dst) is False
    with pytest.raises(IllegalTransition):
        transition(src, dst)


# --------------------------------------------------------------------------- #
# The REJECTED / FAILED asymmetry — the whole reason the two states are split
# --------------------------------------------------------------------------- #
def test_failed_requeues_but_rejected_does_not() -> None:
    assert can_transition(S.FAILED, S.QUEUED) is True
    assert can_transition(S.REJECTED, S.QUEUED) is False


# --------------------------------------------------------------------------- #
# The CancelledError re-queue edge
# --------------------------------------------------------------------------- #
def test_processing_requeues_on_cancel() -> None:
    assert transition(S.PROCESSING, S.QUEUED) is S.QUEUED


# --------------------------------------------------------------------------- #
# Soft delete: reachable from EVERY state, and idempotent
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("src", list(DocumentStatus))
def test_any_state_can_soft_delete(src: DocumentStatus) -> None:
    assert can_transition(src, S.DELETED) is True
    assert transition(src, S.DELETED) is S.DELETED


def test_soft_delete_is_idempotent() -> None:
    # DELETED -> DELETED must be allowed (re-issuing a soft delete is a no-op).
    assert can_transition(S.DELETED, S.DELETED) is True


# --------------------------------------------------------------------------- #
# No non-DELETED self-transitions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("src", [s for s in DocumentStatus if s is not S.DELETED])
def test_no_self_transition_except_deleted(src: DocumentStatus) -> None:
    assert can_transition(src, src) is False


# --------------------------------------------------------------------------- #
# Vocabulary sanity — the constants exported for the CHECK constraints
# --------------------------------------------------------------------------- #
def test_status_values_cover_enum() -> None:
    assert set(STATUS_VALUES) == {s.value for s in DocumentStatus}
    assert "text_ready" in STATUS_VALUES
    assert "full_ready" in STATUS_VALUES


def test_stage_values_cover_enum() -> None:
    assert set(STAGE_VALUES) == {s.value for s in IngestStage}
    # The build-sheet stage order (parsing -> ... -> indexing).
    assert STAGE_VALUES == (
        "parsing",
        "cropping",
        "captioning",
        "chunking",
        "embedding",
        "indexing",
    )
