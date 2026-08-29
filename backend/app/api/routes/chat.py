"""Chat streaming routes — REAL grounded RAG stream (Build Sheet §4/§5.4).

* ``POST /v1/chat/streams`` — runs the real ``rag.answer_stream`` (project-scoped
  hybrid retrieval → LLM streaming → server-dereferenced citations) and renders
  the ``GenEvent`` sequence to AI SDK UI Message Stream frames via
  ``app/streaming/render_aisdk.py`` (the route never hand-writes wire frames).
* ``POST /v1/chat/streams/{stream_id}/stop`` — idempotent Stop. Sets a Redis flag
  (``ex=settings.stop_flag_ttl_s``); the producer polls it between deltas and
  stops the LLM stream within one delta (≤500ms).

Disconnect ≠ cancel (CONTRACT.md §2): generation runs in a DETACHED
``asyncio.create_task`` writing frames into an unbounded queue; the HTTP response
generator only drains the queue. A client disconnect closes the response
generator but NOT the producer task, so generation + persistence still complete.
Persistence is wrapped in ``asyncio.shield``. Only the Stop endpoint cancels
generation.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from time import perf_counter

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.limits import (
    add_cost,
    chat_rate_limit,
    check_cost_cap,
    estimate_chat_cost,
)
from app.config import settings
from app.db.session import session_factory
from app.domain.events import (
    CitationEvent,
    ErrorEvent,
    FigureEvent,
    FinishEvent,
    GenEvent,
    TableEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)
from app.schemas import ChatRequest
from app.streaming.render_aisdk import ping, render_aisdk

router = APIRouter(prefix="/v1/chat", tags=["chat"])

# SSE response headers, mirroring `createUIMessageStreamResponse`.
_SSE_HEADERS = {
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "x-accel-buffering": "no",
    "x-vercel-ai-ui-message-stream": "v1",
}

# Idle interval after which we emit an SSE ping comment to hold the connection.
_PING_INTERVAL_S = 15.0
_SENTINEL = object()

# Strong references to detached producer tasks. Without this the event loop keeps
# only a weak reference and the task can be garbage-collected mid-generation.
_PRODUCERS: set[asyncio.Task] = set()


# --------------------------------------------------------------------------- #
# Redis stop-flag helpers
# --------------------------------------------------------------------------- #
def _stop_key(stream_id: str) -> str:
    return f"paperlens:chat:stop:{stream_id}"


async def _get_redis():
    """Return an async Redis client, or ``None`` if unavailable (best-effort)."""
    try:
        from redis.asyncio import Redis
    except ImportError:  # pragma: no cover - redis is a declared dep
        return None
    try:
        return Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:  # pragma: no cover - defensive
        return None


async def _is_stopped(client, stream_id: str) -> bool:
    """Poll the stop flag on an already-open client. Missing Redis => not stopped."""
    if client is None:
        return False
    try:
        return bool(await client.exists(_stop_key(stream_id)))
    except Exception:  # pragma: no cover - defensive; treat as not stopped
        return False


# --------------------------------------------------------------------------- #
# History extraction
# --------------------------------------------------------------------------- #
def _split_history(req: ChatRequest) -> tuple[str, list[dict[str, str]]]:
    """Return (latest_user_question, prior_history_turns)."""
    question = req.latest_user_text()
    history: list[dict[str, str]] = []
    if req.messages:
        # Everything up to (but not including) the last user turn is history.
        msgs = [{"role": m.role, "content": m.text()} for m in req.messages]
        # Drop the trailing user turn (the current question).
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "user":
                history = msgs[:i]
                break
    return question, history


# --------------------------------------------------------------------------- #
# Persistence (route-owned; answer_stream never writes Postgres)
# --------------------------------------------------------------------------- #
async def _persist(
    *,
    project_id: str,
    conversation_id: str | None,
    question: str,
    assistant_parts: list[dict],
    model: str | None,
    ttft_ms: int | None,
    latency_ms: int | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    finish_reason: str | None,
) -> None:
    """Create/reuse a conversation and store the user + assistant messages.

    Best-effort and defensive: any failure (e.g. an invalid project uuid) must not
    crash the detached task. Message parts are stored verbatim as UIMessage parts.
    """
    from app.db.models import Conversation, Message

    try:
        proj_uuid = uuid.UUID(str(project_id))
    except (ValueError, TypeError):
        return

    async with session_factory() as session:
        async with session.begin():
            conv = None
            if conversation_id:
                try:
                    conv = await session.get(Conversation, uuid.UUID(conversation_id))
                except (ValueError, TypeError):
                    conv = None
                if conv is not None and conv.project_id != proj_uuid:
                    conv = None  # do not cross project scope
            if conv is None:
                conv = Conversation(project_id=proj_uuid, title=question[:120] or None)
                session.add(conv)
                await session.flush()

            session.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    parts=[{"type": "text", "text": question}],
                )
            )
            session.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    parts=assistant_parts,
                    model=model,
                    ttft_ms=ttft_ms,
                    latency_ms=latency_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    finish_reason=finish_reason,
                )
            )


# --------------------------------------------------------------------------- #
# Producer: run answer_stream, instrument, stop-check, render, persist
# --------------------------------------------------------------------------- #
async def _produce(
    *,
    frame_queue: asyncio.Queue,
    req: ChatRequest,
    request_id: str,
    stream_id: str,
    slow: bool,
) -> None:
    """Detached task body. Puts rendered SSE frame strings into ``frame_queue``."""
    from app.rag import answer_stream
    from app.rag.generate import ACTUAL_MODEL

    question, history = _split_history(req)
    t_start = perf_counter()

    # Accumulators surfaced to persistence after the stream ends.
    state: dict = {"ttft_ms": None, "finish": None}
    text_acc: list[str] = []
    citations: dict[str, CitationEvent] = {}
    # Figures/tables keyed by id so the final (cited/overwritten) object wins —
    # persisted so a reloaded conversation still shows its figures and tables.
    figures: dict[str, FigureEvent] = {}
    tables: dict[str, TableEvent] = {}

    redis = await _get_redis()

    async def _events() -> AsyncIterator[GenEvent]:
        gen = answer_stream(
            project_id=req.project_id,
            question=question,
            history=history,
            session_factory=session_factory,
            document_ids=req.document_ids,
            trace_id=request_id,
        )
        try:
            async for ev in gen:
                if isinstance(ev, TextStartEvent):
                    yield ev
                    if slow:  # test hook: exercise the ping keep-alive path
                        await asyncio.sleep(20)
                    continue

                if isinstance(ev, TextDeltaEvent):
                    if state["ttft_ms"] is None:
                        state["ttft_ms"] = int((perf_counter() - t_start) * 1000)
                    text_acc.append(ev.delta)
                    yield ev
                    # Stop check between deltas (≤ one delta of latency).
                    if await _is_stopped(redis, stream_id):
                        yield TextEndEvent()
                        stop_fin = FinishEvent(finish_reason="stop")
                        state["finish"] = stop_fin
                        yield stop_fin
                        await gen.aclose()
                        return
                    continue

                if isinstance(ev, CitationEvent):
                    # Overwrite by id so the final map holds the verified object.
                    citations[ev.id] = ev
                elif isinstance(ev, FigureEvent):
                    # Overwrite by id so a cited re-emit replaces the candidate.
                    figures[ev.id] = ev
                elif isinstance(ev, TableEvent):
                    tables[ev.id] = ev
                elif isinstance(ev, FinishEvent):
                    state["finish"] = ev
                yield ev
        finally:
            await gen.aclose()

    try:
        async for frame in render_aisdk(_events()):
            await frame_queue.put(frame)
    except Exception:
        # Never leak provider/stack; emit a typed, sanitized error then finish.
        err = ErrorEvent(
            error_type="upstream_unavailable",
            message="The assistant is temporarily unavailable. Please try again.",
        )
        async for frame in render_aisdk(_error_tail(err)):
            await frame_queue.put(frame)
    finally:
        # Unblock the client immediately with all frames delivered ([DONE] already
        # queued). Persistence below keeps running in this detached task and must
        # NOT gate stream completion.
        await frame_queue.put(_SENTINEL)

    # ---- persistence (shielded: a disconnect must not cancel the write) --- #
    finish: FinishEvent | None = state["finish"]

    # Cost cap accounting (Build Sheet §2): accumulate this generation's estimated
    # spend onto today's Redis counter so the pre-check on the next request can
    # trip the daily cap. Best-effort — a limiter failure must not crash this
    # detached task, and this runs in the detached producer (the route already
    # returned) which is the only place the token counts exist.
    try:
        if finish is not None and finish.finish_reason == "error":
            # Nothing was generated (e.g. an upstream outage) — don't bill the cap
            # for a failure, or a provider blip could self-inflict a cap lockout.
            cost = 0.0
        else:
            cost = estimate_chat_cost(
                ACTUAL_MODEL.get(),
                finish.prompt_tokens if finish else None,
                finish.completion_tokens if finish else None,
            )
        await add_cost(cost)
    except Exception:  # pragma: no cover - cost accounting is best-effort
        pass

    assistant_parts: list[dict] = [
        {
            "type": "data-citation",
            "id": cid,
            "data": cev.model_dump(mode="json", exclude={"type"}),
        }
        for cid, cev in citations.items()
    ]
    # Figures then tables (verbatim UIMessage parts) so a reload renders them too.
    assistant_parts += [
        {
            "type": "data-figure",
            "id": fid,
            "data": fev.model_dump(mode="json", exclude={"type"}),
        }
        for fid, fev in figures.items()
    ]
    assistant_parts += [
        {
            "type": "data-table",
            "id": tid,
            "data": tev.model_dump(mode="json", exclude={"type"}),
        }
        for tid, tev in tables.items()
    ]
    assistant_parts.append({"type": "text", "text": "".join(text_acc)})
    try:
        await asyncio.shield(
            _persist(
                project_id=req.project_id,
                conversation_id=req.conversation_id,
                question=question,
                assistant_parts=assistant_parts,
                model=ACTUAL_MODEL.get(),
                ttft_ms=state["ttft_ms"],
                latency_ms=int((perf_counter() - t_start) * 1000),
                prompt_tokens=finish.prompt_tokens if finish else None,
                completion_tokens=finish.completion_tokens if finish else None,
                finish_reason=finish.finish_reason if finish else None,
            )
        )
    except Exception:  # pragma: no cover - persistence is best-effort
        pass
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # pragma: no cover
                pass


async def _error_tail(err: ErrorEvent) -> AsyncIterator[GenEvent]:
    yield err
    yield FinishEvent(finish_reason="error")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post(
    "/streams",
    # Priority #1 (Build Sheet §2): protect LLM credits. Rate limit is checked
    # first so an abusive IP gets 429 (not 503); the cost cap pre-check then
    # blocks new generations once the daily budget is spent.
    dependencies=[Depends(chat_rate_limit), Depends(check_cost_cap)],
)
async def create_stream(
    req: ChatRequest,
    slow: bool = Query(
        default=False,
        description="Test hook: sleep 20s before the first token to exercise ping.",
    ),
) -> StreamingResponse:
    """Return a REAL Protocol-v1 grounded answer stream (AI SDK UI Message Stream)."""
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    stream_id = f"str-{uuid.uuid4().hex[:12]}"

    frame_queue: asyncio.Queue = asyncio.Queue()
    # Detached: a client disconnect cancels the response body, NOT this task. A
    # strong reference is held in _PRODUCERS until the task completes.
    task = asyncio.create_task(
        _produce(
            frame_queue=frame_queue,
            req=req,
            request_id=request_id,
            stream_id=stream_id,
            slow=slow,
        )
    )
    _PRODUCERS.add(task)
    task.add_done_callback(_PRODUCERS.discard)

    async def _body() -> AsyncIterator[str]:
        while True:
            try:
                frame = await asyncio.wait_for(
                    frame_queue.get(), timeout=_PING_INTERVAL_S
                )
            except asyncio.TimeoutError:
                yield ping()  # keep-alive; ignored by every SSE parser
                continue
            if frame is _SENTINEL:
                break
            yield frame

    headers = dict(_SSE_HEADERS)
    headers["x-paperlens-stream-id"] = stream_id
    return StreamingResponse(
        _body(), media_type="text/event-stream", headers=headers
    )


@router.post("/streams/{stream_id}/stop", status_code=200)
async def stop_stream(stream_id: str) -> dict[str, object]:
    """Idempotent Stop. Sets a Redis flag; always returns 200.

    Returns 200 whether or not the stream exists or Redis is reachable. The
    producer polls the flag between deltas and stops the LLM stream.
    """
    client = await _get_redis()
    if client is not None:
        try:
            await client.set(_stop_key(stream_id), "1", ex=settings.stop_flag_ttl_s)
        except Exception:  # pragma: no cover - soft: never fail the Stop call
            pass
        finally:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass
    return {"stream_id": stream_id, "stopped": True}
