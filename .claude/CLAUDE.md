# PaperLens — working conventions

PaperLens is a project-scoped, page-verifiable Q&A system over scientific papers.
Differentiators: (A) table/figure fidelity, (B) page-verifiable citations that
abstain when evidence is missing. See `README.md` and `CONTRACT.md`.

## Conventions (apply to all code)

- **Comments and docs in English**, always — even though the build sheet spec is
  in Vietnamese. Translate intent into English.
- **`app/config.py` is the single source of truth for constants.** Never
  hard-code a value that lives there (chunk sizes, RRF_K, marker regex, rate
  limits, thresholds…). Import `from app.config import settings`.
- **Calibrated values stay `None` until measured** (off-topic threshold, BM25
  avg_len, fuzzy badge operating point). Hard-coding them early repeats the
  SCORE_FLOOR blocker bug.
- **Module boundaries:** only `app/rag/` touches Qdrant. `rag/` never reads or
  writes the `documents` table; the ARQ task owns those writes. Callers inject a
  `session_factory`, never a live `AsyncSession` held across a network call.
- **Streaming:** `rag.answer_stream` yields protocol-neutral `GenEvent`
  (`app/domain/events.py`); only `app/streaming/render_aisdk.py` knows about the
  Vercel AI SDK wire format. Citation-marker regexes are shared verbatim with the
  client — see `CONTRACT.md` §3.
- **Failure semantics (ARQ):** REJECTED → `return` (don't retry); FAILED →
  `raise` (ARQ retries); CancelledError → re-queue (never swallow).

## Architecture

Modular monolith, two entrypoints from one image: `app.main` (FastAPI api,
latency-sensitive query path) and `app.worker` (ARQ batch ingestion). Postgres is
the source of truth; Qdrant holds vectors; Redis backs the ARQ queue and caches.
