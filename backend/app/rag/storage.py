"""Local file storage for uploads and figure crops.

The upload route and the ingest pipeline share one ``LocalFileStore`` rooted at
``settings.data_dir`` (``/data`` in the container, overridable via ``DATA_DIR``
for local runs). Layout mirrors the volume described in config.py::

    {data_dir}/uploads/{project_id}/{document_id}.pdf
    {data_dir}/figures/{document_id}/{figure_id}.png   (P3 — VLM crops)

Only the pieces the P1 text-first slice needs are implemented: a stable path
builder, sha256 hashing, and a free-space guard. Figure crop writing is left as
a best-effort stub (see the pipeline TODO).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.config import settings


def sha256_bytes(data: bytes) -> str:
    """Return the hex sha256 of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Stream a file through sha256 without loading it fully into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class LocalFileStore:
    """Filesystem-backed store rooted at ``settings.data_dir``."""

    def __init__(self, data_dir: str | None = None) -> None:
        self.root = Path(data_dir or settings.data_dir)
        self.uploads = self.root / "uploads"
        self.figures = self.root / "figures"

    def ensure_dirs(self) -> None:
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.figures.mkdir(parents=True, exist_ok=True)

    def upload_path(self, project_id: str, document_id: str) -> Path:
        """Deterministic destination for an uploaded PDF."""
        return self.uploads / str(project_id) / f"{document_id}.pdf"

    def figures_dir(self, document_id: str) -> Path:
        return self.figures / str(document_id)

    def save_upload(self, project_id: str, document_id: str, data: bytes) -> Path:
        """Persist raw PDF bytes and return the absolute path."""
        dest = self.upload_path(project_id, document_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def check_free_space(self, needed_bytes: int) -> bool:
        """True if the volume has at least ``needed_bytes`` free.

        The upload route calls this with ``size * 3`` (raw PDF + parsed artifacts
        + crops headroom) before accepting a file.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        return free >= needed_bytes
