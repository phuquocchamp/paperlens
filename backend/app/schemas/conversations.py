"""Request/response DTOs for the conversation (chat history) routes (M7).

Kept in a separate module (not ``schemas/__init__.py``) to respect file
ownership, mirroring ``schemas/documents.py``. ``parts`` is the UIMessage.parts
JSONB stored verbatim by the chat route — surfaced untouched so the client can
reload citations exactly as they streamed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConversationCreate(BaseModel):
    """Body of ``POST /v1/projects/{pid}/conversations``.

    The client pre-creates a conversation on the first turn of a NEW chat (so it
    learns the id up front) and passes ``title`` = the first question. Optional
    so an empty conversation can also be created.
    """

    title: str | None = None


class ConversationRename(BaseModel):
    """Body of ``PATCH /v1/conversations/{id}``."""

    title: str | None = None


class ConversationSummary(BaseModel):
    """One row of the sidebar chat list (project-scoped, newest first)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0


class ConversationMessageOut(BaseModel):
    """A stored message. ``parts`` is the UIMessage.parts JSONB, verbatim."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    parts: list[Any]
    created_at: datetime | None = None


class ConversationDetail(BaseModel):
    """Full conversation with its ordered messages (chat reload)."""

    id: str
    project_id: str
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages: list[ConversationMessageOut]
