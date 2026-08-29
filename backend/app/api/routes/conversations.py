"""Conversation (chat history) routes — M7.

* ``POST /v1/projects/{pid}/conversations``   create a conversation (title optional)
* ``GET  /v1/projects/{pid}/conversations``   list conversations (sidebar), newest first
* ``GET  /v1/conversations/{cid}``            one conversation + ordered messages (reload)
* ``PATCH  /v1/conversations/{cid}``          rename
* ``DELETE /v1/conversations/{cid}``          soft-delete (deleted_at)

Design notes
------------
* The chat route (``routes/chat.py``) persists user+assistant ``messages`` with
  their UIMessage ``parts`` JSONB verbatim, creating/reusing a ``Conversation``.
  It does NOT expose the new conversation id to the client, so the client
  pre-creates a conversation here on the first turn of a NEW chat and passes that
  id back as ``ChatRequest.conversation_id`` — that is how the id round-trips
  without touching the (out-of-scope) chat route.
* ``message_count`` is computed with a SQL aggregate (never ``len(conv.messages)``
  — every relationship in this schema is ``lazy="raise"``). The list uses an
  INNER JOIN so empty conversations (a pre-create the user abandoned before the
  answer persisted) never leak into the sidebar.
* ``messages.created_at`` is ``server_default=now()`` and the chat route writes the
  user + assistant rows in ONE transaction, so both share an identical timestamp.
  The detail query therefore adds a deterministic role tiebreak (user before
  assistant) so reload order is stable.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, Project
from app.db.session import get_session
from app.schemas.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationMessageOut,
    ConversationRename,
    ConversationSummary,
)

router = APIRouter(prefix="/v1", tags=["conversations"])


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field}") from exc


# --------------------------------------------------------------------------- #
# Create / list (project-scoped)
# --------------------------------------------------------------------------- #
@router.post(
    "/projects/{project_id}/conversations",
    response_model=ConversationSummary,
    status_code=201,
)
async def create_conversation(
    project_id: str,
    body: ConversationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSummary:
    proj_uuid = _parse_uuid(project_id, "project_id")
    project = await session.get(Project, proj_uuid)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    title = (body.title or "").strip()[:120] or None
    conv = Conversation(project_id=proj_uuid, title=title)
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return ConversationSummary(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=0,
    )


@router.get(
    "/projects/{project_id}/conversations",
    response_model=list[ConversationSummary],
)
async def list_conversations(
    project_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ConversationSummary]:
    proj_uuid = _parse_uuid(project_id, "project_id")

    # INNER JOIN => only conversations that actually have messages. Newest first.
    msg_count = func.count(Message.id).label("message_count")
    stmt = (
        select(
            Conversation.id,
            Conversation.title,
            Conversation.created_at,
            Conversation.updated_at,
            msg_count,
        )
        .join(Message, Message.conversation_id == Conversation.id)
        .where(
            Conversation.project_id == proj_uuid,
            Conversation.deleted_at.is_(None),
        )
        .group_by(Conversation.id)
        .order_by(Conversation.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        ConversationSummary(
            id=str(r.id),
            title=r.title,
            created_at=r.created_at,
            updated_at=r.updated_at,
            message_count=r.message_count,
        )
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Detail / rename / soft-delete (conversation-scoped)
# --------------------------------------------------------------------------- #
async def _load_live_conversation(
    session: AsyncSession, conversation_id: str
) -> Conversation:
    """Fetch a non-deleted conversation whose project still exists, else 404."""
    conv_uuid = _parse_uuid(conversation_id, "conversation_id")
    conv = await session.get(Conversation, conv_uuid)
    if conv is None or conv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="conversation not found")
    project = await session.get(Project, conv.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationDetail:
    conv = await _load_live_conversation(session, conversation_id)

    # Explicit query (relationship is lazy="raise"). User-before-assistant
    # tiebreak because both rows of a turn share an identical created_at.
    role_rank = case((Message.role == "user", 0), else_=1)
    rows = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.asc(), role_rank.asc())
        )
    ).scalars()
    messages = [
        ConversationMessageOut(
            id=str(m.id),
            role=m.role,
            parts=m.parts,
            created_at=m.created_at,
        )
        for m in rows
    ]
    return ConversationDetail(
        id=str(conv.id),
        project_id=str(conv.project_id),
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: str,
    body: ConversationRename,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSummary:
    conv = await _load_live_conversation(session, conversation_id)
    conv.title = (body.title or "").strip()[:120] or None
    await session.commit()
    await session.refresh(conv)
    count = (
        await session.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conv.id
            )
        )
    ).scalar_one()
    return ConversationSummary(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=count,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    conv = await _load_live_conversation(session, conversation_id)
    conv.deleted_at = func.now()
    await session.commit()
