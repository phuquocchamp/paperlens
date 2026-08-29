# PaperLens backend — one image, two entrypoints (modular monolith).
#   api:    uvicorn app.main:app
#   worker: arq app.worker.WorkerSettings
# The compose file picks the command per service; the image is identical.

FROM python:3.11-slim

# OMP_NUM_THREADS is overridable at build time (default 2) and re-set per service
# at runtime in compose. Caps native thread pools (Docling / numpy) so the worker
# stays within its memory/CPU budget.
ARG OMP_NUM_THREADS=2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Docling / HuggingFace model cache lives on the shared /data volume.
    HF_HOME=/data/models \
    OMP_NUM_THREADS=${OMP_NUM_THREADS}

# System deps: build toolchain for wheels + libs Docling/PyMuPDF need at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the whole backend context (app/, alembic/, alembic.ini, pyproject.toml).
# Editable install of the hatchling package needs the source present, so the
# dependency and source layers are installed together here. pyproject.toml is the
# pinned dependency source of truth — do not add deps at the Dockerfile level.
COPY . .

# Editable install exposes the console scripts (paperlens-api / paperlens-worker)
# and installs the full pinned dependency set.
RUN pip install -e .

# Shared data volume: uploads/ · figures/ · models/ (HF_HOME points inside it).
# The real named volume is declared/mounted by compose (appdata:/data); we only
# create the mount point here and do NOT declare VOLUME, so one-shot containers
# like `migrate` (which mount no /data) don't spawn stray anonymous volumes.
# NOTE: P0 does NOT pre-download Docling models — warm-up happens at worker
# on_startup in P1.
RUN mkdir -p /data/models

EXPOSE 8000

# Default command runs the API; the worker service overrides this in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
