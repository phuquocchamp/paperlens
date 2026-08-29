"""Request/response DTOs for the project + document routes (P1 ingestion).

Kept in a separate module (not ``schemas/__init__.py``) to respect file
ownership. Response models mirror only the columns the frontend polls.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    created_at: datetime | None = None


class DocumentOut(BaseModel):
    """Status-polling view of a document (Build Sheet §6 Documents screen)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str | None = None
    filename: str
    file_sha256: str | None = None
    page_count: int | None = None
    status: str
    stage: str | None = None
    progress: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    chunk_count: int = 0
    figure_count: int = 0
    created_at: datetime | None = None


class DuplicateDocument(BaseModel):
    """409 body: points the UI at the existing document."""

    detail: str = "document already exists in this project"
    existing_document_id: str
