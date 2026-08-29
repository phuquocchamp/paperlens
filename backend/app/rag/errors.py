"""Typed ingestion exceptions and their state-machine mapping (Build Sheet §4).

The ARQ task uses the exception *type* as the only signal that separates
"do not retry" (REJECTED) from "retry" (FAILED):

    ParseError | TooLarge | Blocked        -> REJECTED (task returns, no retry)
    EmbeddingError | VectorStoreError |
        StorageError                       -> FAILED   (task raises, ARQ retries)
    asyncio.CancelledError                 -> propagated (never caught -> re-queue)

Every error carries a short machine ``code`` (e.g. ``PDF_NO_TEXT_LAYER``) that the
task writes to ``documents.error_code`` for the UI.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base for all ingestion errors. ``code`` is a stable machine string."""

    code: str = "INGEST_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


# --------------------------------------------------------------------------- #
# REJECTED family — intrinsic to the file, terminal, never retried.
# --------------------------------------------------------------------------- #
class ParseError(IngestError):
    """Docling could not produce usable text.

    Distinct codes: ``PDF_ENCRYPTED``, ``PDF_CORRUPT``, ``PDF_NO_TEXT_LAYER``.
    """

    code = "PDF_PARSE_FAILED"


class TooLarge(IngestError):
    """Document exceeds a hard limit (page cap, byte size)."""

    code = "DOC_TOO_LARGE"


class Blocked(IngestError):
    """Content blocked (moderation / policy). Terminal, no retry."""

    code = "CONTENT_BLOCKED"


# --------------------------------------------------------------------------- #
# FAILED family — transient infra failure, retryable by ARQ / the Retry button.
# --------------------------------------------------------------------------- #
class EmbeddingError(IngestError):
    """Embedding provider failure (network, 5xx, quota)."""

    code = "EMBEDDING_FAILED"


class VectorStoreError(IngestError):
    """Qdrant write/index failure."""

    code = "VECTOR_STORE_FAILED"


class StorageError(IngestError):
    """Object/file storage failure (read/write of the PDF or crops)."""

    code = "STORAGE_FAILED"


# Exception tuples the worker maps against.
REJECTED_ERRORS: tuple[type[Exception], ...] = (ParseError, TooLarge, Blocked)
FAILED_ERRORS: tuple[type[Exception], ...] = (
    EmbeddingError,
    VectorStoreError,
    StorageError,
)
