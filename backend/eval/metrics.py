"""Deterministic Tier-0 metric primitives (Build Sheet §7).

Pure, side-effect-free helpers. Every metric is computed from data the pipeline
already emits (retrieved chunk content, ``CitationEvent`` objects, the answer
text) — no LLM-as-judge, no fuzzy thresholds. The only fuzz-tolerant step is
whitespace/case normalisation for the anchor substring check, which survives
re-chunking and PDF-extraction spacing quirks.
"""

from __future__ import annotations

import re

from app.config import settings

# Compiled once: the SAME marker regex the pipeline + client use (config.py).
_MARKER_RE = re.compile(settings.marker_re)
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase + collapse all whitespace runs to a single space, then strip.

    This is the ONLY tolerance applied to the anchor recall check: it absorbs the
    line-wrap and double-space artefacts of PDF text extraction without letting a
    paraphrase pass (the anchor must still appear verbatim, word-for-word).
    """
    return _WS_RE.sub(" ", s or "").strip().lower()


def anchor_recall_hit(anchor: str, retrieved_contents: list[str]) -> bool:
    """True if ``anchor`` is a normalized substring of any retrieved chunk."""
    na = normalize(anchor)
    if not na:
        return False
    return any(na in normalize(c) for c in retrieved_contents)


def inline_markers(answer_text: str) -> list[str]:
    """Every distinct anchor token (C#/F#/T#) cited inline, in first-use order."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in _MARKER_RE.finditer(answer_text or ""):
        for token in m.group(1).split(","):
            tok = token.strip()
            if tok and tok not in seen_set:
                seen.append(tok)
                seen_set.add(tok)
    return seen


def split_markers(markers: list[str]) -> dict[str, list[str]]:
    """Bucket markers by kind: ``{"C": [...], "F": [...], "T": [...]}``."""
    out: dict[str, list[str]] = {"C": [], "F": [], "T": []}
    for mk in markers:
        kind = mk[:1].upper()
        if kind in out:
            out[kind].append(mk)
    return out


def quote_is_verbatim(quote: str, contents: list[str]) -> bool:
    """True if a citation's displayed quote is a VERBATIM substring of a chunk.

    Exact (byte-for-byte) match — this is the hard ``citation_quote_match`` gate.
    It must be 1.0 by construction because ``query.py`` dereferences the quote
    from ``chunks.content`` rather than letting the LLM author it.
    """
    if not quote:
        return False
    return any(quote in c for c in contents)


def is_abstain(answer_text: str, used_markers: list[str]) -> bool:
    """An answer counts as an abstain iff it emits the sentinel AND cites nothing.

    Two conditions, because when the off-topic gate passes the pipeline still
    yields *pending* citations before text-start (the model may then abstain via
    the system prompt). So "abstained" is judged from the ANSWER: the sentinel
    text is present and no inline C/F/T marker was used.
    """
    from app.rag.generate import ABSTAIN_TEXT

    sentinel = ABSTAIN_TEXT[:40] in (answer_text or "")
    return sentinel and len(used_markers) == 0


def fraction(num: int, den: int) -> float:
    """Safe fraction: an empty denominator is treated as a perfect 1.0."""
    return 1.0 if den == 0 else num / den


__all__ = [
    "normalize",
    "anchor_recall_hit",
    "inline_markers",
    "split_markers",
    "quote_is_verbatim",
    "is_abstain",
    "fraction",
]
