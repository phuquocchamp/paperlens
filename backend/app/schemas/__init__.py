"""Pydantic request/response DTOs for the HTTP API (P0, minimal).

Kept intentionally small: only what the fake chat stream needs. Retrieval/ingest
DTOs are added by their owners as those routes land.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MessagePart(BaseModel):
    """A single AI SDK UIMessage part. We only read text parts."""

    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None


class ChatMessage(BaseModel):
    """One turn of conversation history.

    Accepts both the simple ``{role, content}`` shape and the AI SDK v7
    ``useChat`` UIMessage shape ``{role, parts:[{type:"text", text:...}]}``.
    Unknown fields (id, metadata, …) are ignored.
    """

    model_config = ConfigDict(extra="ignore")

    role: str
    content: str | None = None
    parts: list[MessagePart] | None = None

    def text(self) -> str:
        """Flatten to plain text from ``content`` or text ``parts``."""
        if self.content:
            return self.content
        if self.parts:
            return "".join(p.text or "" for p in self.parts if p.type == "text")
        return ""


class ChatRequest(BaseModel):
    """Body of ``POST /v1/chat/streams``.

    Either ``message`` (single new user turn) or ``messages`` (full history, AI
    SDK ``useChat`` style) may be supplied; the route prefers ``messages`` when
    present and falls back to ``message``. Unknown top-level fields sent by the
    AI SDK transport (id, trigger, messageId, …) are ignored.
    """

    model_config = ConfigDict(extra="ignore")

    project_id: str
    message: str | None = None
    messages: list[ChatMessage] | None = None
    document_ids: list[str] | None = None
    conversation_id: str | None = None

    def latest_user_text(self) -> str:
        """Best-effort extraction of the newest user question."""
        if self.messages:
            for msg in reversed(self.messages):
                if msg.role == "user":
                    return msg.text()
        return self.message or ""


class StopResponse(BaseModel):
    """Response of ``POST /v1/chat/streams/{stream_id}/stop`` (idempotent)."""

    stream_id: str
    stopped: bool = True


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    """Aggregate readiness. Per-dependency values are ``ok`` / ``unavailable`` /
    ``unknown`` (soft in P0)."""

    status: Literal["ready", "degraded"] = "ready"
    checks: dict[str, str] = Field(default_factory=dict)
