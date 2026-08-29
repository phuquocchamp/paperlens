# PaperLens — P0 Integration Contract

This file is the **fixed interface** between the four parallel P0 workstreams.
Agents fill in bodies against these pinned contracts; do not redefine them.
All code comments and docs are in **English** (the build sheet is Vietnamese —
translate intent).

Source of truth for the full plan: `README.md` (architecture) and the Build
Sheet Rev 4 text at
`/Users/qtphoang/.claude/projects/-Users-qtphoang-Dev-Labs-projects-rag-handbook-paperlens/97a68972-0314-4a13-ad58-e33634cce92a/tool-results/bglgyrb6d.txt`
(sections referenced below as §N).

---

## 1. Repo layout & file ownership (P0)

```
paperlens/
  backend/
    pyproject.toml            [PINNED]  full dependency list — do not overwrite
    Dockerfile                (A)
    app/
      __init__.py             [PINNED empty]
      config.py               [PINNED]  §2 params table, single source of truth
      main.py                 (C)       FastAPI api entrypoint
      worker.py               (A)       ARQ worker entrypoint (skeleton)
      logging_config.py       (A)       structlog JSON + trace_id
      db/
        base.py               (B)       declarative Base + naming_convention
        session.py            (B)       async engine + session_factory
        models.py             (B)       11 tables (§3)
      domain/
        __init__.py           [PINNED empty]
        events.py             [PINNED]  GenEvent types (§4)
        status.py             (B)       DocumentStatus state machine (§3)
      schemas/                (C)       pydantic request/response models
      rag/
        __init__.py           (C)       6 public function signatures (§4), stubs
      api/
        routes/
          chat.py             (C)       fake SSE stream + POST /streams/{id}/stop
          health.py           (C)       /healthz, /readyz
      streaming/
        render_aisdk.py       (C)       GenEvent → AI SDK UI Message Stream frames
    alembic/                  (B)       env.py + versions/0001_initial.py
    alembic.ini               (B)
    tests/                    (B, C)    owner writes tests for their module
  frontend/                   (D)       Next.js 15 app (see §6)
  docker-compose.yml          (A)       base
  docker-compose.override.yml (A)       dev
  .env.example                (A)       expand with all keys from config.py
```

**Ownership rule:** each file has exactly one owner (letter). Read others' pinned
files; never edit a file you do not own. If you need a value, import it from
`app.config.settings`.

---

## 2. SSE — PaperLens Stream Protocol v1 (AI SDK UI Message Stream)

`rag.answer_stream` yields `GenEvent` objects (`app/domain/events.py`).
`app/streaming/render_aisdk.py` is the **only** module that maps them to AI SDK
frames. The fake endpoint (C) emits this exact ordering:

```
start                     (messageMetadata: request_id + model)
 ├ data-status   ×k        transient  {"stage":"retrieving","detail":"Searching 4 documents…"}
 ├ data-citation ×N        id="cit-C3"  verify_status="pending"   ← sole source of truth
 ├ data-figure   ×M        id="fig-{uuid}"  display="candidate"   (client preloads)
 ├ data-table    ×K        id="tbl-{uuid}"
 ├ text-start
 ├ text-delta    ×n        markers like [C3] stay verbatim in the text
 ├ text-end
 ├ data-citation ×N (2nd)  SAME id, whole-object overwrite: pending → grounded|weak|unverifiable
 ├ data-figure   re-emit   SAME id, display="cited"  when the model cites [F2] (promotion)
 ├ data-notice   ×0..1  ·  data-error (typed) only on error
finish → [DONE]
```

**Seven invariants (keep all):** append-only; never retract (overwrite whole
object instead); `data-status` is the only transient part; client `switch` has a
default `return null`; `errorText` never leaks provider/stack; sources come before
text; `finish` closes with `[DONE]`.

- `ping_message_factory` = SSE comment line (`: ping\n\n`) — P0 test: sleep(20) before first token.
- **Disconnect ≠ cancel:** generation runs in a detached `asyncio.create_task`,
  persisted via `asyncio.shield`. Only `POST /v1/chat/streams/{id}/stop`
  (idempotent, Redis flag `ex=600`) actually stops it.

### AI SDK frame mapping (render_aisdk.py)
- `start` → stream start with `messageMetadata`.
- `status` → transient `data-status` part.
- `citation` / `figure` / `table` → `data-citation` / `data-figure` / `data-table`
  parts, keyed by `id` (re-emit with same id to overwrite).
- `text-start` / `text-delta` / `text-end` → AI SDK text part lifecycle.
- `finish` → finish frame then `[DONE]`.

---

## 3. Shared citation-marker regexes — COPY VERBATIM, both server & client

Rev 3's silent bug was the client regex diverging from the server's. Both sides
copy these from `app.config.settings`; neither re-authors them.

```
MARKER_RE        \[\s*([CFT]\d{1,3}(?:\s*,\s*[CFT]\d{1,3})*)\s*\]
PARTIAL_MARKER_RE \[[CFT]?\d{0,3}$        # dangling marker mid-stream; hide the tail
MAX_HOLD         24                        # hold-back buffer (chars)
```

Markers reference: `C` = citation, `F` = figure, `T` = table. `[Figure 2]` is
scrubbed to the literal text "Figure 2" — it must NEVER become `[F2]`.

---

## 4. `rag/__init__.py` public API (C writes stubs for P0)

Six functions; P0 = correct signatures + types, bodies raise `NotImplementedError`
(except the fake path, which lives in `api/routes/chat.py`, not here). Caller
injects `session_factory`, never a live session.

```python
async def ingest_document(*, document_id, project_id, file_path, session_factory,
                          file_store, progress=None, max_pages=None,
                          force_reindex=False) -> IngestResult: ...
async def answer_stream(*, project_id, question, history, session_factory,
                        document_ids=None, top_k=8,
                        trace_id=None) -> AsyncIterator[GenEvent]: ...
async def delete_document(...): ...
async def delete_project(...): ...
async def reconcile(...): ...
async def reindex_document(...): ...
```

---

## 5. Deferred from iteration 1 (do NOT build now)
- Pydantic → JSON Schema → TS codegen + CI diff check.
- `import-linter` contract config (dependency listed; wiring later).
- Golden-file Vitest replay through the real `ai` parser.
- MarkerFilter server implementation (P2) — P0 only needs the fake stream to emit
  whole markers, so the client remark plugin can be exercised.

## 6. Frontend notes for agent D
- Next.js 15 App Router. `ai` package **v7** (bumped from v5 — real API break;
  resolve `/vercel/ai` via context7 and query the v7 `useChat` + SSE data-parts
  API before writing).
- shadcn `Sidebar` (264px expanded / 64px icon-only), collapse state in cookie
  `sidebar_state` read in a Server Component (not localStorage — avoids hydration
  flash). Resolve shadcn sidebar docs via context7.
- BFF proxy route: `runtime='nodejs'`, `dynamic='force-dynamic'`, `maxDuration=300`,
  `duplex:'half'`, pass `req.signal`, return upstream `body` directly, header
  `x-accel-buffering: no`.
- Routes: `app/chat/page.tsx` + `app/chat/[chatId]/page.tsx` (NOT optional
  catch-all). Evidence panel is a context primitive (opens on citation/figure
  click, closes on X/Esc), not a fixed 3rd column.
- `remark-inline-citation` plugin at the mdast layer using MARKER_RE from §3;
  hide dangling tails with PARTIAL_MARKER_RE.

## 7. P0 Definition of Done (verified by orchestrator, not agents)
`docker compose up` is clean · FE streams a fake answer with citation badges ·
state-machine transition tests green.
