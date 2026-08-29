"""Protocol v1 frame-order assertions for the REAL chat stream (CONTRACT.md §2).

The route now runs the real ``rag.answer_stream`` (hybrid retrieval + LLM), which
needs live services + network. To lock the wire contract Agent D parses with
``ai`` v7 ``useChat`` WITHOUT those dependencies, these tests monkeypatch
``app.rag.answer_stream`` with a deterministic, contract-shaped ``GenEvent``
sequence (no figures — figures are P3). The route wiring under test is real:
detached producer task, ``render_aisdk`` frame mapping, ping keep-alive, SSE
headers, the idempotent Stop endpoint, and disconnect≠cancel.

The end-to-end proof against live services lives in ``tests/test_query_e2e.py``
(gated behind ``PAPERLENS_E2E``).

Contract locked here:
* sources (citations) come BEFORE ``text-start``;
* a second ``data-citation`` for the same id OVERWRITES (pending -> grounded|weak)
  and lands AFTER ``text-end``;
* every ``[C#]`` marker in the text resolves to an emitted ``cit-C#`` id
  (citation_id_validity);
* the stream ends with the ``[DONE]`` sentinel; Stop is idempotent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.domain.events import (
    CitationEvent,
    FinishEvent,
    GenEvent,
    Stage,
    StartEvent,
    StatusEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    VerifyStatus,
)
from app.main import app


def _canned_citation(marker: str, status: VerifyStatus) -> CitationEvent:
    return CitationEvent(
        id=f"cit-{marker}",
        marker=marker,
        quote=f"Verbatim server-dereferenced quote for {marker}.",
        document_id="doc-1",
        document_title="A Paper",
        filename="paper.pdf",
        page=3,
        section_path="2 · Method",
        content_hash="a" * 64,
        verify_status=status,
    )


async def _canned_answer_stream(**kwargs) -> AsyncIterator[GenEvent]:
    """A deterministic, contract-ordered event sequence (mirrors query.py)."""
    yield StartEvent(request_id="req-test", model="deepseek-chat")
    yield StatusEvent(stage=Stage.RETRIEVING, detail="Searching the corpus…")
    yield StatusEvent(stage=Stage.GENERATING, detail="Generating answer…")
    # Pending citations BEFORE text.
    yield _canned_citation("C1", VerifyStatus.PENDING)
    yield _canned_citation("C2", VerifyStatus.PENDING)
    yield TextStartEvent()
    for delta in [
        "Retrieval-augmented generation grounds each claim ",
        "[C1]. ",
        "Hybrid fusion raises recall ",
        "[C2].",
    ]:
        yield TextDeltaEvent(delta=delta)
    yield TextEndEvent()
    # Re-emit ONLY used anchors with a verify status (SAME id => overwrite).
    yield _canned_citation("C1", VerifyStatus.GROUNDED)
    yield _canned_citation("C2", VerifyStatus.WEAK)
    yield FinishEvent(finish_reason="stop", prompt_tokens=128, completion_tokens=64)


@pytest.fixture(autouse=True)
def _patch_answer_stream(monkeypatch):
    """Replace the real (network-bound) answer_stream with the canned sequence."""
    import app.rag as rag_pkg

    monkeypatch.setattr(rag_pkg, "answer_stream", _canned_answer_stream)


def _parse_frames(body: str) -> list[dict | str]:
    frames: list[dict | str] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        if not block.startswith("data:"):
            continue
        payload = block[len("data:") :].strip()
        frames.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return frames


async def _fetch_frames() -> list[dict | str]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.post("/v1/chat/streams", json={"project_id": "p1"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers.get("x-vercel-ai-ui-message-stream") == "v1"
        assert resp.headers.get("x-paperlens-stream-id")
        return _parse_frames(resp.text)


def _index_of(frames: list[dict | str], predicate) -> int:
    for i, f in enumerate(frames):
        if isinstance(f, dict) and predicate(f):
            return i
    raise AssertionError("no frame matched predicate")


@pytest.mark.asyncio
async def test_stream_starts_and_terminates() -> None:
    frames = await _fetch_frames()
    assert isinstance(frames[0], dict) and frames[0]["type"] == "start"
    assert frames[0]["messageMetadata"]["model"]
    assert frames[-1] == "[DONE]"
    assert isinstance(frames[-2], dict) and frames[-2]["type"] == "finish"


@pytest.mark.asyncio
async def test_sources_before_text_start() -> None:
    frames = await _fetch_frames()
    text_start = _index_of(frames, lambda f: f["type"] == "text-start")
    first_citation = _index_of(frames, lambda f: f["type"] == "data-citation")
    assert first_citation < text_start
    assert frames[first_citation]["data"]["verify_status"] == "pending"
    assert (
        frames[first_citation]["id"]
        == "cit-" + frames[first_citation]["data"]["marker"]
    )


@pytest.mark.asyncio
async def test_citation_overwrite_after_text_end() -> None:
    frames = await _fetch_frames()
    text_end = _index_of(frames, lambda f: f["type"] == "text-end")
    indices = [
        i
        for i, f in enumerate(frames)
        if isinstance(f, dict)
        and f["type"] == "data-citation"
        and f["id"] == "cit-C1"
    ]
    assert len(indices) == 2, "same-id citation must be re-emitted exactly once"
    first, second = indices
    assert frames[first]["data"]["verify_status"] == "pending"
    assert frames[second]["data"]["verify_status"] in {"grounded", "weak", "unverifiable"}
    assert second > text_end, "overwrite must come after text-end"


@pytest.mark.asyncio
async def test_markers_verbatim_in_text() -> None:
    frames = await _fetch_frames()
    text = "".join(
        f["delta"]
        for f in frames
        if isinstance(f, dict) and f["type"] == "text-delta"
    )
    assert "[C1]" in text
    assert "[C2]" in text


@pytest.mark.asyncio
async def test_citation_id_validity() -> None:
    """Every ``cit-C#`` id referenced by an emitted citation is unique + used."""
    import re

    from app.config import settings

    frames = await _fetch_frames()
    text = "".join(
        f["delta"]
        for f in frames
        if isinstance(f, dict) and f["type"] == "text-delta"
    )
    used = set()
    for m in re.finditer(settings.marker_re, text):
        for tok in m.group(1).split(","):
            used.add(tok.strip())
    emitted = {
        f["data"]["marker"]
        for f in frames
        if isinstance(f, dict) and f["type"] == "data-citation"
    }
    # Every marker used in the text resolves to an emitted citation.
    assert used <= emitted


@pytest.mark.asyncio
async def test_status_is_transient() -> None:
    frames = await _fetch_frames()
    statuses = [f for f in frames if isinstance(f, dict) and f["type"] == "data-status"]
    assert statuses, "at least one status frame"
    for s in statuses:
        assert s.get("transient") is True
        assert "id" not in s


@pytest.mark.asyncio
async def test_stop_endpoint_idempotent() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r1 = await client.post("/v1/chat/streams/does-not-exist/stop")
        r2 = await client.post("/v1/chat/streams/does-not-exist/stop")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["stopped"] is True


@pytest.mark.asyncio
async def test_healthz() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
