"""Unit tests for P4 operational robustness (no database / no services).

Covers the two pure decision points that carry the correctness of the
stuck-sweep and reconcile jobs:

  - ``app.rag.reconcile._classify`` — the orphan / missing / stale split.
  - ``app.worker._is_deleted``     — the delete-race guard predicate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings
from app.domain.status import DocumentStatus
from app.rag.reconcile import _classify
from app.worker import _is_deleted


def _payload(content_hash: str, model: str | None = None) -> dict:
    return {
        "content_hash": content_hash,
        "embedding_model": model or settings.embedding_model,
    }


# --------------------------------------------------------------------------- #
# reconcile classification
# --------------------------------------------------------------------------- #
def test_classify_all_matched_is_clean() -> None:
    live = {"a": "h1", "b": "h2"}
    points = {"a": _payload("h1"), "b": _payload("h2")}
    out = _classify(live, points)
    assert out == {
        "hydrated": 2,
        "retrieved": 2,
        "orphan_vectors": 0,
        "missing_vectors": 0,
        "stale_vectors": 0,
    }


def test_classify_orphan_point_has_no_live_chunk() -> None:
    # Point "z" has no live chunk -> orphan (harmless). "a" is fine.
    live = {"a": "h1"}
    points = {"a": _payload("h1"), "z": _payload("h9")}
    out = _classify(live, points)
    assert out["orphan_vectors"] == 1
    assert out["missing_vectors"] == 0
    assert out["stale_vectors"] == 0


def test_classify_missing_vector_for_live_chunk() -> None:
    # Live chunk "b" has no point -> missing (alert). Driven by the live set.
    live = {"a": "h1", "b": "h2"}
    points = {"a": _payload("h1")}
    out = _classify(live, points)
    assert out["missing_vectors"] == 1
    assert out["orphan_vectors"] == 0
    assert out["stale_vectors"] == 0


def test_classify_stale_on_content_hash_mismatch() -> None:
    live = {"a": "h1"}
    points = {"a": _payload("STALE_HASH")}
    out = _classify(live, points)
    assert out["stale_vectors"] == 1
    assert out["orphan_vectors"] == 0
    assert out["missing_vectors"] == 0


def test_classify_stale_on_embedding_model_drift() -> None:
    live = {"a": "h1"}
    points = {"a": _payload("h1", model="text-embedding-ada-002")}
    out = _classify(live, points)
    assert out["stale_vectors"] == 1


def test_classify_asymmetry_missing_and_orphan_coexist() -> None:
    # "a" matches, "b" is missing (live, no point), "z" is orphan (point, no chunk).
    live = {"a": "h1", "b": "h2"}
    points = {"a": _payload("h1"), "z": _payload("h9")}
    out = _classify(live, points)
    assert out["missing_vectors"] == 1
    assert out["orphan_vectors"] == 1
    assert out["hydrated"] == 2
    assert out["retrieved"] == 2


# --------------------------------------------------------------------------- #
# delete-race guard predicate
# --------------------------------------------------------------------------- #
def test_is_deleted_missing_row_treated_as_deleted() -> None:
    # A vanished row must never be resurrected.
    assert _is_deleted(None) is True


def test_is_deleted_on_deleted_status() -> None:
    assert _is_deleted((DocumentStatus.DELETED.value, None)) is True


def test_is_deleted_on_deleted_at_even_if_status_not_flipped() -> None:
    # deleted_at stamped but status still processing (separate-statement race).
    assert _is_deleted((DocumentStatus.PROCESSING.value, datetime.now(UTC))) is True


def test_is_deleted_false_for_live_processing_row() -> None:
    assert _is_deleted((DocumentStatus.PROCESSING.value, None)) is False


def test_is_deleted_false_for_text_ready_row() -> None:
    assert _is_deleted((DocumentStatus.TEXT_READY.value, None)) is False
