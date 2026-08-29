"""FastAPI application factory and the ``paperlens-api`` entrypoint.

The app object is importable as ``app.main:app`` (uvicorn / tests). Cross-agent
imports (``logging_config`` from Agent A) are guarded so the app stays importable
even while other workstreams are still landing their files.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import chat, conversations, documents, health

# Bundled static assets (fake figure fixtures for the P0 demo stream). In P1 the
# worker also writes real figure crops under the data volume; this directory is
# the stable mount the frontend proxies via its /static rewrite.
STATIC_DIR = Path(__file__).parent / "static"

# Agent A owns logging_config; guard the import so a missing/partial module never
# blocks `app.main:app`.
try:
    from app.logging_config import configure_logging
except Exception:  # pragma: no cover - defensive during parallel P0 build

    def configure_logging(level: int | None = None) -> None:  # type: ignore[misc]
        return None


def _cors_origins() -> list[str]:
    """Frontend origins allowed to call the API.

    Overridable via ``PAPERLENS_CORS_ORIGINS`` (comma-separated). Defaults cover
    the Next.js dev server.
    """
    raw = os.getenv("PAPERLENS_CORS_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging()

    app = FastAPI(
        title="PaperLens API",
        version="0.1.0",
        description="Project-scoped, page-verifiable Q&A over scientific papers.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Let the browser read the stream id returned by POST /v1/chat/streams.
        expose_headers=["x-paperlens-stream-id"],
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)

    # Serve real figure crops from the data volume at /static/figures, mounted
    # BEFORE the bundled /static mount so the more specific path wins (Starlette
    # matches mounts in registration order). check_dir=False + a guarded mkdir
    # keep the app importable even when DATA_DIR does not exist yet (tests import
    # app.main:app without a data volume).
    from app.config import settings

    figures_dir = Path(settings.data_dir) / "figures"
    try:
        figures_dir.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - e.g. default /data not writable locally
        pass
    app.mount(
        "/static/figures",
        StaticFiles(directory=figures_dir, check_dir=False),
        name="figures",
    )

    # Serve figure crops / fake fixtures at /static (frontend proxies this path).
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()


def main() -> None:
    """Console-script entrypoint (`paperlens-api`) — run uvicorn."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("PAPERLENS_HOST", "0.0.0.0"),
        port=int(os.getenv("PAPERLENS_PORT", "8000")),
        reload=bool(os.getenv("PAPERLENS_RELOAD")),
    )


if __name__ == "__main__":
    main()
