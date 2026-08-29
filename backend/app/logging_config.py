"""Structured JSON logging with a per-request/job ``trace_id`` (observability tier 0).

Build Sheet §7 (monitoring tier 0): structured JSON logs + a ``trace_id`` generated
at the BFF and propagated to the worker via ARQ kwargs. This module wires structlog
to emit JSON and to stamp every log line with the ambient ``trace_id``.

Usage:
    from app.logging_config import configure_logging, bind_trace_id, new_trace_id

    configure_logging()                 # once, at process startup
    bind_trace_id(incoming_trace_id)    # per request / per job (from BFF or ARQ kwargs)
    log = structlog.get_logger()
    log.info("event", key="value")      # -> JSON line carrying trace_id

The BFF mints the ``trace_id``; the API binds it per request and forwards it to the
worker as an ARQ kwarg, where the job binds it again so ingest logs share the id.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid

import structlog

# Ambient trace id, bound per request (API) or per job (worker). Empty until bound.
_trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def new_trace_id() -> str:
    """Mint a fresh trace id.

    The BFF normally supplies the id; the API uses this only as a fallback when a
    request arrives without one so no log line is ever left untraceable.
    """
    return uuid.uuid4().hex


def bind_trace_id(trace_id: str | None = None) -> str:
    """Bind ``trace_id`` to the current context and return the effective value.

    Pass the id received from the BFF (HTTP header) or from ARQ job kwargs. When
    ``None`` or empty, a new id is minted so downstream logs stay correlated.
    """
    effective = trace_id or new_trace_id()
    _trace_id_ctx.set(effective)
    return effective


def get_trace_id() -> str:
    """Return the trace id bound to the current context (empty string if unbound)."""
    return _trace_id_ctx.get()


def _add_trace_id(_logger: object, _method: str, event_dict: dict) -> dict:
    """structlog processor: stamp each event with the ambient trace id, if present."""
    trace_id = _trace_id_ctx.get()
    if trace_id:
        event_dict["trace_id"] = trace_id
    return event_dict


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog + stdlib logging to emit JSON to stdout.

    Idempotent enough to call once at API and worker startup. Routes the stdlib
    root logger through structlog so third-party loggers share the JSON format.
    """
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_trace_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, arq) through the same JSON renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processor=structlog.processors.JSONRenderer(),
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
