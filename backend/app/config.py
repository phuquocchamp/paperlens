"""Single source of truth for every constant in PaperLens.

This mirrors section 2 ("Bảng thông số chốt") of the Build Sheet Rev 4. Any value
that other modules need MUST be read from here, never hard-coded at the call site.

Two categories of value live here:
  - CHOSEN: fixed by evidence in the build sheet.
  - CALIBRATE: intentionally not fixed yet; resolved from a calibration file
    (see section 9). These default to ``None`` here and are loaded at runtime.
    Hard-coding a calibrated value early repeats the SCORE_FLOOR blocker bug.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Look for .env both in the current working dir and one level up (repo root),
    # so running from backend/ still finds the repo-root .env. OS env vars still
    # take precedence over the file.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ------------------------------------------------------------------ #
    # Infrastructure connections
    # ------------------------------------------------------------------ #
    database_url: str = "postgresql+asyncpg://paperlens:paperlens@postgres:5432/paperlens"
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection_alias: str = "chunks"

    # Storage volume (uploads/ · figures/ · models/)
    data_dir: str = "/data"
    hf_home: str = "/data/models"  # Docling model cache

    # ------------------------------------------------------------------ #
    # LLM / embedding providers
    # ------------------------------------------------------------------ #
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model_default: str = "deepseek-chat"        # DeepSeek default
    llm_model_fallback: str = "gpt-4o-mini"         # OpenAI via circuit breaker

    # Langfuse (optional; observability tier 1, wired at P2)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ------------------------------------------------------------------ #
    # Chunking (section 5.2) — CHOSEN. Overlap applies ONLY at forced
    # boundaries (see build sheet §5.2).
    # ------------------------------------------------------------------ #
    chunk_target: int = 900
    chunk_max: int = 1200
    chunk_overlap: int = 130
    chunk_min_merge: int = 120

    # ------------------------------------------------------------------ #
    # Embedding & sparse (section 5.3) — CHOSEN
    # ------------------------------------------------------------------ #
    embedding_model: str = "text-embedding-3-large"
    embedding_dims: int = 1024  # pass dimensions=1024 in the API call (no manual truncation)
    # BM25: Modifier.IDF is MANDATORY; avg_len is measured from the real corpus at P1.
    bm25_avg_len: float | None = None  # CALIBRATE (P1) — do not default to 256

    # ------------------------------------------------------------------ #
    # Retrieval / fusion (section 2 + 5.4)
    # ------------------------------------------------------------------ #
    prefetch_dense: int = 40
    prefetch_sparse: int = 40
    rrf_k: int = 10  # CALIBRATE start; measure k ∈ {2, 10, 20} via mrr@10 (P2)
    fused_top_k: int = 20
    per_doc_quota: int = 4  # relaxed unconditionally when underfilled
    context_top_k: int = 8
    # Two-stage fusion weights when query rewrite fires.
    fusion_weight_original: float = 0.6
    fusion_weight_rewrite: float = 0.4

    # Off-topic gate (blocker fix #1): gate on max dense cosine, NOT the RRF score.
    offtopic_metric: str = "max_dense_cosine"
    offtopic_threshold: float | None = None  # CALIBRATE (end P2) from a calibration file

    # ------------------------------------------------------------------ #
    # Figures (section 2)
    # ------------------------------------------------------------------ #
    max_figures_in_context: int = 4
    figure_candidate: int = 6
    fig_score_floor_factor: float = 0.7  # ~0.7 × text threshold; CALIBRATE with the gate
    caption_concurrency: int = 2  # raise to 5 only after measuring throughput + 429-rate (P1)
    # Junk-figure filter (build sheet P1: w/h<150, ratio>8:1, <5KB). Crops that
    # fail any of these are dropped — decorative rules, logos, hairlines.
    figure_min_px: int = 150
    figure_max_aspect: float = 8.0
    figure_min_bytes: int = 5120

    # ------------------------------------------------------------------ #
    # Streaming / citation markers (section 2 + 4) — MUST match the client.
    # See CONTRACT.md; the frontend copies these verbatim, neither side re-authors.
    # ------------------------------------------------------------------ #
    # Full marker, e.g. "[C3]", "[C3, F2]".
    marker_re: str = r"\[\s*([CFT]\d{1,3}(?:\s*,\s*[CFT]\d{1,3})*)\s*\]"
    # Partial (dangling) marker still being streamed, e.g. "[C" — used to hide the tail.
    partial_marker_re: str = r"\[[CFT]?\d{0,3}$"
    max_hold: int = 24  # hold-back buffer (chars); past this, flush as literal, keep "["

    # ------------------------------------------------------------------ #
    # Parsing (section 5.1) — CHOSEN
    # ------------------------------------------------------------------ #
    max_pages: int = 80
    do_ocr: bool = True  # DEFAULT mode only OCRs clusters with no text cell
    tableformer_mode: str = "ACCURATE"
    images_scale: float = 2.0
    text_yield_gate: int = 200  # < 200 chars/page → REJECTED(PDF_NO_TEXT_LAYER)

    # ------------------------------------------------------------------ #
    # Worker sizing (section 2) — sized to measured 6.2 GB peak RSS
    # ------------------------------------------------------------------ #
    worker_max_jobs: int = 1
    # NOTE: build sheet §2 pins this at 2, but measured evidence (P1 e2e) shows
    # OMP_NUM_THREADS=2 makes Docling/onnxruntime nondeterministically drop
    # 30–60% of a PDF's text. 1 is deterministic and complete. Keep at 1 until
    # the OpenMP threading interaction is root-caused.
    omp_num_threads: int = 1

    # ------------------------------------------------------------------ #
    # Rate limiting & cost cap (section 2) — priority #1
    # ------------------------------------------------------------------ #
    rate_limit_messages: int = 20  # per 10 minutes per IP
    rate_limit_messages_window_s: int = 600
    rate_limit_uploads: int = 10  # per hour per IP
    rate_limit_uploads_window_s: int = 3600
    daily_cost_cap_usd: float = 5.0  # Redis-tracked; over cap → 503

    # ------------------------------------------------------------------ #
    # History truncation (section 2)
    # ------------------------------------------------------------------ #
    history_max_turns: int = 6
    history_max_tokens: int = 2000

    # ------------------------------------------------------------------ #
    # Timeouts
    # ------------------------------------------------------------------ #
    sweep_stuck_after_minutes: int = 45  # PROCESSING older than this → FAILED + Retry
    stop_flag_ttl_s: int = 600  # Redis stop flag expiry for POST /streams/{id}/stop


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Convenience singleton for modules that just need the values.
settings = get_settings()
