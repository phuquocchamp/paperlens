"""PaperLens Eval Tier-0 runner (Build Sheet §7).

Runs the REAL ``rag.answer_stream`` for every golden question against the live
services and computes DETERMINISTIC metrics — no LLM judge, no fuzzy grading:

  * ``recall@k``            — is the gold_text_anchor a normalized substring of any
                             retrieved chunk? (answerable items). ``k`` is the
                             hydrated-context size, capped by ``context_top_k``
                             (8 in §2), so it is reported as ``recall@k (k≈8)``.
  * ``citation_id_valid``  — every inline ``[C#]`` maps to an emitted citation id
                             (hard gate = 1.0). F/T validity reported alongside.
  * ``citation_quote``     — every emitted citation quote is a VERBATIM substring
                             of real chunk content (hard gate = 1.0, by design).
  * ``abstain_correct``    — abstain items abstain with zero citations; answerable
                             items do NOT abstain (hard gate ≥ 0.9).

Per-group (numeric / figure / conceptual / abstain) AND overall, with pass/fail
against the §7 thresholds. Exits non-zero if any HARD gate fails, so it can wire
into CI later.

Run (from ``backend/``)::

    DATABASE_URL="postgresql+asyncpg://paperlens:paperlens@127.0.0.1:5432/paperlens" \
    REDIS_URL="redis://127.0.0.1:6379/0" QDRANT_URL="http://127.0.0.1:6333" \
    DATA_DIR="/tmp/paperlens-data" HF_HOME="/tmp/paperlens-data/models" \
    OMP_NUM_THREADS=1 \
    .venv/bin/python -m eval.run_eval          # add --no-ingest to skip the corpus check
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.domain.events import CitationEvent, FigureEvent, TableEvent, TextDeltaEvent
from eval import metrics
from eval.corpus import (
    EVAL_PROJECT_ID,
    all_chunk_contents,
    corpus_media_counts,
    ensure_corpus,
)

_GOLDEN_PATH = Path(__file__).with_name("golden.jsonl")

# §7 hard gates (a failure exits non-zero). ``mrr`` etc. are out of scope here.
THRESHOLDS = {
    "recall": 0.90,           # recall@k on answerable items
    "citation_id_valid": 1.00,  # C-marker validity (exact)
    "citation_quote": 1.00,   # verbatim quote match (exact)
    "abstain_correct": 0.90,  # abstain correctness
}

ANSWER_GROUPS = ("conceptual", "numeric", "figure")


@dataclass
class GoldenItem:
    id: str
    group: str
    question: str
    expect: str
    gold_text_anchor: str | None = None


@dataclass
class ItemResult:
    item: GoldenItem
    # retrieval
    retrieved_contents: list[str] = field(default_factory=list)
    max_dense_cosine: float = 0.0
    # answer stream
    answer_text: str = ""
    used_markers: list[str] = field(default_factory=list)
    pending_citations: dict = field(default_factory=dict)   # marker -> CitationEvent
    n_figures: int = 0
    table_markers: set = field(default_factory=set)
    ttft_ms: float | None = None
    # computed
    recall_hit: bool | None = None       # None => not applicable
    quote_matches: int = 0
    quote_total: int = 0
    used_c_valid: int = 0
    used_c_total: int = 0
    used_ft_valid: int = 0
    used_ft_total: int = 0
    abstained: bool = False
    abstain_correct: bool | None = None


def load_golden(path: Path) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        items.append(
            GoldenItem(
                id=d["id"],
                group=d["group"],
                question=d["question"],
                expect=d["expect"],
                gold_text_anchor=d.get("gold_text_anchor"),
            )
        )
    return items


async def _run_stream(question: str):
    """Drain ``answer_stream`` once, capturing every signal the metrics need."""
    from app.rag import answer_stream

    pending: dict = {}
    fig_ids: set = set()
    table_markers: set = set()
    parts: list[str] = []
    t0 = time.perf_counter()
    ttft = None
    async for ev in answer_stream(
        project_id=str(EVAL_PROJECT_ID),
        question=question,
        history=[],
        session_factory=_session_factory(),
    ):
        if isinstance(ev, CitationEvent):
            if ev.verify_status.value == "pending":
                pending[ev.marker] = ev
        elif isinstance(ev, FigureEvent):
            fig_ids.add(ev.id)
        elif isinstance(ev, TableEvent):
            if ev.marker:
                table_markers.add(ev.marker)
        elif isinstance(ev, TextDeltaEvent):
            if ttft is None:
                ttft = (time.perf_counter() - t0) * 1000.0
            parts.append(ev.delta)
    return {
        "text": "".join(parts),
        "pending": pending,
        "n_figures": len(fig_ids),
        "table_markers": table_markers,
        "ttft_ms": ttft,
    }


def _session_factory():
    from app.db.session import session_factory

    return session_factory


async def _retrieve_contents(question: str):
    from app.rag.retrieval import retrieve

    r = await retrieve(
        project_id=str(EVAL_PROJECT_ID),
        question=question,
        session_factory=_session_factory(),
        top_k=10,  # capped to context_top_k (=8) by the pipeline; see module docstring
    )
    return [c.content for c in r.chunks], r.max_dense_cosine


async def evaluate_item(item: GoldenItem, all_contents: list[str]) -> ItemResult:
    res = ItemResult(item=item)

    # Retrieval (explicit): recall evidence + the raw dense-cosine calibration
    # signal (§7: this golden set doubles as the OFFTOPIC_THRESHOLD set).
    res.retrieved_contents, res.max_dense_cosine = await _retrieve_contents(
        item.question
    )

    # The real answer stream.
    stream = await _run_stream(item.question)
    res.answer_text = stream["text"]
    res.pending_citations = stream["pending"]
    res.n_figures = stream["n_figures"]
    res.table_markers = stream["table_markers"]
    res.ttft_ms = stream["ttft_ms"]

    res.used_markers = metrics.inline_markers(res.answer_text)
    used = metrics.split_markers(res.used_markers)

    # --- recall@k (answerable, anchor present) --------------------------- #
    if item.expect == "answer" and item.gold_text_anchor:
        res.recall_hit = metrics.anchor_recall_hit(
            item.gold_text_anchor, res.retrieved_contents
        )

    # --- citation_quote_match (verbatim, by construction) ---------------- #
    for cit in res.pending_citations.values():
        res.quote_total += 1
        if metrics.quote_is_verbatim(cit.quote, all_contents):
            res.quote_matches += 1

    # --- citation_id_validity -------------------------------------------- #
    valid_c = set(res.pending_citations.keys())
    valid_f = {f"F{i}" for i in range(1, res.n_figures + 1)}
    valid_t = set(res.table_markers)
    for mk in used["C"]:
        res.used_c_total += 1
        if mk in valid_c:
            res.used_c_valid += 1
    for mk in used["F"] + used["T"]:
        res.used_ft_total += 1
        if mk in (valid_f | valid_t):
            res.used_ft_valid += 1

    # --- abstain correctness --------------------------------------------- #
    res.abstained = metrics.is_abstain(res.answer_text, res.used_markers)
    if item.expect == "abstain":
        res.abstain_correct = res.abstained
    else:
        res.abstain_correct = not res.abstained

    return res


# --------------------------------------------------------------------------- #
# Aggregation + reporting
# --------------------------------------------------------------------------- #
def _agg(results: list[ItemResult]) -> dict:
    recall_hits = [r for r in results if r.recall_hit is not None]
    recall_num = sum(1 for r in recall_hits if r.recall_hit)
    quote_num = sum(r.quote_matches for r in results)
    quote_den = sum(r.quote_total for r in results)
    cvalid_num = sum(r.used_c_valid for r in results)
    cvalid_den = sum(r.used_c_total for r in results)
    ftvalid_num = sum(r.used_ft_valid for r in results)
    ftvalid_den = sum(r.used_ft_total for r in results)
    abst = [r for r in results if r.abstain_correct is not None]
    abst_num = sum(1 for r in abst if r.abstain_correct)
    return {
        "n": len(results),
        "recall": metrics.fraction(recall_num, len(recall_hits)),
        "recall_den": len(recall_hits),
        "citation_quote": metrics.fraction(quote_num, quote_den),
        "quote_den": quote_den,
        "citation_id_valid": metrics.fraction(cvalid_num, cvalid_den),
        "cvalid_den": cvalid_den,
        "ft_id_valid": metrics.fraction(ftvalid_num, ftvalid_den),
        "ftvalid_den": ftvalid_den,
        "abstain_correct": metrics.fraction(abst_num, len(abst)),
        "abstain_den": len(abst),
    }


def _fmt(value: float, den: int) -> str:
    if den == 0:
        return "  -  "
    return f"{value:5.2f}"


def print_report(results: list[ItemResult]) -> bool:
    groups: dict[str, list[ItemResult]] = {}
    for r in results:
        groups.setdefault(r.item.group, []).append(r)

    order = [g for g in ("numeric", "figure", "conceptual", "abstain") if g in groups]
    order += [g for g in groups if g not in order]

    print("\n================= PaperLens Eval Tier-0 =================")
    print(f"project_id : {EVAL_PROJECT_ID}")
    print(f"golden set : {len(results)} questions across {len(groups)} groups")
    print(
        "\nmetrics: recall@k (k≈8, context_top_k) · citation_id_valid (C, hard) · "
        "citation_quote (hard) · abstain_correct\n"
    )

    header = (
        f"{'group':<11}{'n':>3}  {'recall':>7}  {'id_valid(C)':>12}  "
        f"{'quote':>7}  {'abstain':>8}"
    )
    print(header)
    print("-" * len(header))

    def row(name: str, a: dict) -> str:
        return (
            f"{name:<11}{a['n']:>3}  "
            f"{_fmt(a['recall'], a['recall_den']):>7}  "
            f"{_fmt(a['citation_id_valid'], a['cvalid_den']):>12}  "
            f"{_fmt(a['citation_quote'], a['quote_den']):>7}  "
            f"{_fmt(a['abstain_correct'], a['abstain_den']):>8}"
        )

    for g in order:
        print(row(g, _agg(groups[g])))
    print("-" * len(header))
    overall = _agg(results)
    print(row("OVERALL", overall))

    # ---- hard gate check ------------------------------------------------ #
    answerable = [r for r in results if r.item.expect == "answer"]
    ans_agg = _agg(answerable)
    gates = [
        ("recall@k (answerable)", ans_agg["recall"], THRESHOLDS["recall"], ">="),
        (
            "citation_id_valid (C)",
            overall["citation_id_valid"],
            THRESHOLDS["citation_id_valid"],
            ">=",
        ),
        (
            "citation_quote_match",
            overall["citation_quote"],
            THRESHOLDS["citation_quote"],
            ">=",
        ),
        (
            "abstain_correct",
            overall["abstain_correct"],
            THRESHOLDS["abstain_correct"],
            ">=",
        ),
    ]
    print("\n---- hard gates -------------------------------------------")
    all_pass = True
    for name, value, thr, _op in gates:
        ok = value >= thr
        all_pass = all_pass and ok
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<24} {value:5.2f}  (>= {thr:.2f})")

    # ---- calibration evidence (OFFTOPIC_THRESHOLD, §7/§9) --------------- #
    in_cos = [r.max_dense_cosine for r in results if r.item.expect == "answer"]
    out_cos = [r.max_dense_cosine for r in results if r.item.expect == "abstain"]
    print("\n---- max_dense_cosine (OFFTOPIC_THRESHOLD calibration) ----")
    if in_cos:
        print(
            f"  in-corpus   : min={min(in_cos):.3f}  mean={sum(in_cos)/len(in_cos):.3f}  "
            f"max={max(in_cos):.3f}"
        )
    if out_cos:
        print(
            f"  out/borderln: min={min(out_cos):.3f}  mean={sum(out_cos)/len(out_cos):.3f}  "
            f"max={max(out_cos):.3f}"
        )
    print(f"  active gate : {settings.offtopic_threshold or 0.28:.3f}")

    print("\n" + ("ALL HARD GATES PASSED" if all_pass else "HARD GATE FAILURE"))
    print("=========================================================\n")
    return all_pass


def print_per_item(results: list[ItemResult]) -> None:
    print("\n---- per-item detail -------------------------------------")
    for r in results:
        recall = "-" if r.recall_hit is None else ("hit" if r.recall_hit else "MISS")
        abst = "abstain" if r.abstained else "answer"
        exp = r.item.expect
        flag = "" if r.abstain_correct else "  <-- abstain WRONG"
        cites = ",".join(sorted(r.used_markers)) or "-"
        print(
            f"  {r.item.id:<26} grp={r.item.group:<10} cos={r.max_dense_cosine:.3f} "
            f"recall={recall:<4} got={abst:<7} exp={exp:<7} cites={cites}{flag}"
        )


async def main() -> int:
    ap = argparse.ArgumentParser(description="PaperLens Eval Tier-0 harness")
    ap.add_argument(
        "--no-ingest",
        action="store_true",
        help="skip the corpus idempotency check (assume it is already ingested)",
    )
    ap.add_argument(
        "--force-ingest",
        action="store_true",
        help="re-ingest both papers even if already TEXT_READY",
    )
    ap.add_argument("--golden", default=str(_GOLDEN_PATH), help="path to golden.jsonl")
    ap.add_argument("--detail", action="store_true", help="print per-item detail")
    args = ap.parse_args()

    if not args.no_ingest:
        outcomes = await ensure_corpus(settings.data_dir, force=args.force_ingest)
        media = await corpus_media_counts()
        print("[corpus] media counts:", json.dumps(media))
        for o in outcomes:
            print(f"[corpus] {o.arxiv_id}: {o.status} (points={o.point_count})")

    items = load_golden(Path(args.golden))
    all_contents = await all_chunk_contents()
    if not all_contents:
        print("ERROR: eval corpus has no chunks; run without --no-ingest first.")
        return 2

    results: list[ItemResult] = []
    for it in items:
        r = await evaluate_item(it, all_contents)
        results.append(r)

    if args.detail:
        print_per_item(results)
    all_pass = print_report(results)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
