# PaperLens Eval Tier-0 harness

A lightweight, **deterministic** quality gate for the RAG pipeline (Build Sheet
§7). It ingests a fixed 2-paper corpus, runs the **real** `rag.answer_stream`
over a golden question set, and computes metrics that guard **retrieval**,
**answer/citation validity**, and **abstain** behaviour — no LLM-as-judge, no
fuzzy grading. It exits non-zero when a hard gate fails, so it can become a CI
gate later.

This package is **additive and isolated**: it creates only files under
`backend/eval/` and never edits app code, existing tests, or the frontend.

## What it measures

| Metric | Threshold | How it is computed |
|---|---|---|
| `recall@k` | hard ≥ 0.90 | Is `gold_text_anchor` (a ~15-word verbatim phrase) a normalized substring of any **retrieved** chunk? Anchor-based, so it survives re-chunking. `k` is the hydrated-context size, capped at `context_top_k` (**8** in §2) — reported as `recall@k (k≈8)`, not the stale "@10" from §7 which predates the 20→4→8 pin. |
| `citation_id_valid` (C) | hard = 1.00 | Every inline `[C#]` marker in the answer maps to an emitted `CitationEvent` id. F/T marker validity is reported alongside but is **not** the hard gate. |
| `citation_quote_match` | hard = 1.00 | Every emitted citation's displayed `quote` is a **verbatim** (exact) substring of real `chunks.content`. 1.0 by construction — the server dereferences the quote, the LLM never writes it (blocker fix #2). |
| `abstain_correct` | hard ≥ 0.90 | `expect:"abstain"` items must emit the abstain sentinel **and** cite nothing; `expect:"answer"` items must **not** abstain. Abstain is judged from the answer text (the gate can pass and still yield *pending* citations before the model abstains), so it is `ABSTAIN_TEXT present AND zero inline C/F/T markers`. |

The runner also prints the per-item `max_dense_cosine` distribution
(in-corpus vs out-of-corpus) — this golden set doubles as the
`OFFTOPIC_THRESHOLD` calibration set (§7 / §9).

## Corpus

Two deterministic, born-digital open-access PDFs, in a dedicated eval project:

* `1503.02531` — *Distilling the Knowledge in a Neural Network* (numeric + conceptual; 5 tables)
* `2309.06180v1` — *Efficient Memory Management for LLM Serving with PagedAttention* (figures + a table)

Ingestion is **idempotent**: the project id and each document id are
deterministic (`uuid5`), and a paper is (re)ingested only when it is not already
`TEXT_READY`/`FULL_READY` with points in Qdrant. Re-runs reuse the corpus. The
underlying `rag.ingest_document` is itself idempotent, so `--force-ingest` is
always safe.

## Golden set

16 questions in `golden.jsonl`, across four groups (averages hide numeric
failures, so groups are reported separately):

* **conceptual** ×4, **numeric** ×4, **figure** ×3
* **abstain** ×5: 3 out-of-corpus (pizza recipe, 2022 World Cup, Apple stock
  price) + 2 borderline in-corpus that clear the gate but the papers can't
  answer (OPT-175B training cost in dollars; Geoffrey Hinton's birth year).

Each item: `{id, group, question, expect: "answer"|"abstain", gold_text_anchor?}`.
Anchors are verbatim phrases drawn from the papers (verified as normalized
substrings of the real corpus), chosen to answer the question — **not** copied
from retriever output, so recall is not tautological.

## How to run

From `backend/`, with the live services and OS-env host overrides (pydantic
loads the real DeepSeek + OpenAI keys from `../.env` — do **not** set
`OPENAI_API_KEY` here):

```bash
DATABASE_URL="postgresql+asyncpg://paperlens:paperlens@127.0.0.1:5432/paperlens" \
REDIS_URL="redis://127.0.0.1:6379/0" \
QDRANT_URL="http://127.0.0.1:6333" \
DATA_DIR="/tmp/paperlens-data" \
HF_HOME="/tmp/paperlens-data/models" \
OMP_NUM_THREADS=1 \
.venv/bin/python -m eval.run_eval          # add --detail for per-item output
```

Flags: `--no-ingest` (skip the corpus check when it's already ingested),
`--force-ingest` (re-ingest both papers), `--detail` (per-item table),
`--golden PATH` (alternate golden file).

First run downloads the two PDFs (`curl`) and Docling models into `HF_HOME`, then
ingests (~75s total). Subsequent runs reuse the corpus (~30–60s for 16 LLM
calls). Docling/RapidOCR log lines and a benign httpx teardown message may appear
on stderr; the report table and exit code are on stdout.

## Current results

Measured end-to-end against the live stack (DeepSeek chat + OpenAI embeddings):

```
group        n   recall   id_valid(C)    quote   abstain
--------------------------------------------------------
numeric      4     1.00          1.00     1.00      1.00
figure       3     1.00          1.00     1.00      1.00
conceptual   4     0.75          1.00     1.00      1.00
abstain      5      -             -       1.00      1.00
--------------------------------------------------------
OVERALL     16     0.91          1.00     1.00      1.00

  [PASS]  recall@k (answerable)     0.91  (>= 0.90)
  [PASS]  citation_id_valid (C)     1.00  (>= 1.00)
  [PASS]  citation_quote_match      1.00  (>= 1.00)
  [PASS]  abstain_correct           1.00  (>= 0.90)

max_dense_cosine (OFFTOPIC_THRESHOLD calibration):
  in-corpus   : min=0.535  mean=0.630  max=0.747
  out/borderln: min=0.147  mean=0.221  max=0.392
  active gate : 0.280
```

**Findings**

* All four hard gates pass; overall `recall@k = 0.91` sits just above the 0.90
  bar. The single miss is `concept-distillation`: its anchor chunk (the
  Introduction's "transfer the knowledge … suitable for deployment") does not
  land in the top-8 for that phrasing, though the answer is still correctly
  produced and cited from adjacent chunks. This is a genuine `recall@8`
  observation, not a metric bug — exactly what the group-level split exists to
  surface (`conceptual = 0.75`).
* `citation_quote_match = 1.00` and `citation_id_valid = 1.00` confirm the
  anchor-citation architecture: every displayed quote is verbatim chunk content,
  and every `[C#]`/`[F#]` the model emits references a real retrieved item.
* `abstain_correct = 1.00`: all 5 abstain items (including the 2 borderline
  in-corpus ones that clear the 0.28 gate) correctly abstain with zero
  citations. The cosine gap (in-corpus min 0.535 vs out-of-corpus max 0.392)
  shows a clean, well-separated calibration band for `OFFTOPIC_THRESHOLD`.

## Files

* `golden.jsonl` — the golden question set.
* `corpus.py` — deterministic, idempotent corpus ingest (dedicated eval project).
* `metrics.py` — pure deterministic metric primitives.
* `run_eval.py` — runner: ingest check → real `answer_stream` → metrics → table
  → non-zero exit on hard-gate failure. Entry point: `python -m eval.run_eval`.
