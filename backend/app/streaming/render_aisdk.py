"""GenEvent -> Vercel AI SDK "UI Message Stream" SSE frames.

This is the ONLY module in the backend that knows the Vercel AI SDK wire format
exists (CONTRACT.md §2 / build sheet §4). Everything upstream stays protocol
neutral; if the wire format changes, only this file changes.

Targeted wire format (AI SDK / `ai` package v5+, used by v7 `useChat`)
======================================================================
Transport: Server-Sent Events. Each chunk is a single line::

    data: <json>\n\n

The stream ends with the literal sentinel::

    data: [DONE]\n\n

Response headers set by the route (mirroring `createUIMessageStreamResponse`)::

    content-type: text/event-stream
    cache-control: no-cache
    connection: keep-alive
    x-accel-buffering: no
    x-vercel-ai-ui-message-stream: v1   # transport discriminator — required

Chunk shapes (the `type` discriminator drives `useChat`'s reducer):

    {"type":"start","messageId":"<id>","messageMetadata":{...}}
    {"type":"data-status","data":{...},"transient":true}     # transient: onData only
    {"type":"data-citation","id":"cit-C3","data":{...}}      # persistent; id => reconcile
    {"type":"data-figure","id":"fig-<uuid>","data":{...}}
    {"type":"data-table","id":"tbl-<uuid>","data":{...}}
    {"type":"data-notice","id":"notice-1","data":{...}}      # persistent
    {"type":"text-start","id":"<textId>"}
    {"type":"text-delta","id":"<textId>","delta":"..."}
    {"type":"text-end","id":"<textId>"}
    {"type":"data-error","id":"err-1","data":{...}}          # typed, persistent
    {"type":"error","errorText":"..."}                       # native SDK error frame
    {"type":"finish","finishReason":"stop","messageMetadata":{...}}

Reconciliation rule: a `data-*` chunk carrying the SAME `id` overwrites the whole
previous object client-side (append-only on the wire, never a retraction). Only
`data-status` is transient (no `id`, `transient: true`); it is delivered via the
client `onData` callback and never lands in `message.parts`.

Envelope keys are camelCase (`messageMetadata`, `finishReason`, `delta`, `id`);
the opaque inner `data` payload keeps the snake_case field names of the domain
model (`verify_status`, `document_id`, `image_url`, ...). Consumers hand-write
those TS types for P0 (codegen is deferred, CONTRACT.md §5).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.domain.events import (
    CitationEvent,
    ErrorEvent,
    FigureEvent,
    FinishEvent,
    GenEvent,
    NoticeEvent,
    StartEvent,
    StatusEvent,
    TableEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)

# SSE terminator understood by the `ai` package client transport.
DONE = "data: [DONE]\n\n"


def _frame(obj: dict[str, Any]) -> str:
    """Serialize one chunk object as an SSE ``data:`` line."""
    return f"data: {json.dumps(obj, ensure_ascii=False, separators=(',', ':'))}\n\n"


def ping() -> str:
    """An SSE comment used as a keep-alive heartbeat.

    A comment line (leading ``:``) is ignored by every SSE parser, so it holds the
    connection open without producing a message. Never prefix it with ``data:``.
    """
    return ": ping\n\n"


def _data_part(name: str, event: GenEvent, *, part_id: str | None) -> str:
    """Build a persistent ``data-<name>`` chunk from a domain event.

    ``mode="json"`` renders enums (Stage / VerifyStatus / FigureDisplay / ...) as
    plain strings. ``exclude={"type"}`` drops the redundant inner discriminator so
    it cannot shadow the ``data-<name>`` envelope type.
    """
    payload: dict[str, Any] = {
        "type": f"data-{name}",
        "data": event.model_dump(mode="json", exclude={"type"}),
    }
    if part_id is not None:
        payload["id"] = part_id
    return _frame(payload)


async def render_aisdk(events: AsyncIterator[GenEvent]) -> AsyncIterator[str]:
    """Map a stream of :class:`GenEvent` to AI SDK UI Message Stream SSE frames.

    Yields ready-to-write ``str`` frames (each already ``\\n\\n``-terminated). The
    caller writes them straight into a ``StreamingResponse``. Emits the ``[DONE]``
    sentinel exactly once, right after the ``finish`` frame.
    """
    # One stable id ties text-start / text-delta* / text-end together. The SDK
    # requires the triplet to share an id.
    text_id = f"txt-{uuid.uuid4().hex[:12]}"
    finished = False

    async for event in events:
        if isinstance(event, StartEvent):
            yield _frame(
                {
                    "type": "start",
                    "messageId": event.request_id,
                    "messageMetadata": {
                        "request_id": event.request_id,
                        "model": event.model,
                    },
                }
            )

        elif isinstance(event, StatusEvent):
            # The only transient part: no id, delivered via onData, never stored.
            yield _frame(
                {
                    "type": "data-status",
                    "data": event.model_dump(mode="json", exclude={"type"}),
                    "transient": True,
                }
            )

        elif isinstance(event, CitationEvent):
            # id is pinned to "cit-<marker>" so the client can resolve [C3] -> part.
            yield _data_part("citation", event, part_id=event.id)

        elif isinstance(event, FigureEvent):
            # Figures resolve by marker (id is a uuid); re-emit same id to promote.
            yield _data_part("figure", event, part_id=event.id)

        elif isinstance(event, TableEvent):
            yield _data_part("table", event, part_id=event.id)

        elif isinstance(event, NoticeEvent):
            # Persistent (has an id) — lands in message.parts. Not transient.
            yield _data_part("notice", event, part_id="notice-1")

        elif isinstance(event, TextStartEvent):
            yield _frame({"type": "text-start", "id": text_id})

        elif isinstance(event, TextDeltaEvent):
            yield _frame({"type": "text-delta", "id": text_id, "delta": event.delta})

        elif isinstance(event, TextEndEvent):
            yield _frame({"type": "text-end", "id": text_id})

        elif isinstance(event, ErrorEvent):
            # Typed part (for rich UI) followed by the native SDK error frame.
            # errorText carries only the sanitized message — never provider/stack.
            yield _data_part("error", event, part_id="err-1")
            yield _frame({"type": "error", "errorText": event.message})

        elif isinstance(event, FinishEvent):
            metadata: dict[str, Any] = {}
            if event.prompt_tokens is not None:
                metadata["prompt_tokens"] = event.prompt_tokens
            if event.completion_tokens is not None:
                metadata["completion_tokens"] = event.completion_tokens
            frame: dict[str, Any] = {
                "type": "finish",
                "finishReason": event.finish_reason,
            }
            if metadata:
                frame["messageMetadata"] = metadata
            yield _frame(frame)
            yield DONE
            finished = True

        else:  # pragma: no cover - exhaustive over the GenEvent union
            continue

    # Defensive close: if the producer ended without an explicit FinishEvent the
    # client still needs the sentinel to complete the message.
    if not finished:
        yield _frame({"type": "finish", "finishReason": "stop"})
        yield DONE
