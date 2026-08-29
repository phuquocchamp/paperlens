"""Per-IP rate limiting + a daily LLM cost cap (Build Sheet §2 priority #1).

Goal: protect the LLM credits. Two independent guards, both backed by Redis and
both **fail-open** — a Redis hiccup must never 500 the whole API:

1. **Per-IP fixed-window rate limiter.** One counter per (route bucket, client
   IP, window index). Over the limit → HTTP 429 with a ``Retry-After`` header
   (seconds until the current window rolls over). Two buckets:
     * ``messages`` — ``rate_limit_messages`` per ``rate_limit_messages_window_s``
       (chat streams);
     * ``uploads``  — ``rate_limit_uploads``  per ``rate_limit_uploads_window_s``
       (document ingest).

2. **Daily cost cap.** A per-UTC-date Redis counter accumulating estimated USD
   spent on generations. Before a chat generation or an ingest starts, if the
   day's spend is already ``>= settings.daily_cost_cap_usd`` → HTTP 503. The
   chat route calls :func:`add_cost` after a generation finishes to accumulate
   the estimated spend (see :func:`estimate_chat_cost`).

Everything is exposed as FastAPI dependencies (``chat_rate_limit``,
``upload_rate_limit``, ``check_cost_cap``) so routes just list them in their
decorator's ``dependencies=[...]``.

**Disabling for local dev / verification:** set ``PAPERLENS_DISABLE_LIMITS=1`` in
the environment. The flag is read at call time (not import time), so tests can
toggle it per-process. Default (unset) = limits ON in normal operation.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from fastapi import HTTPException, Request

from app.config import settings

# --------------------------------------------------------------------------- #
# Redis key prefixes
# --------------------------------------------------------------------------- #
_RL_PREFIX = "paperlens:rl"      # rate-limit counters
_COST_PREFIX = "paperlens:cost"  # daily cost counter

# Daily cost key lives ~2 days so a counter from yesterday self-expires.
_COST_TTL_S = 48 * 3600

# --------------------------------------------------------------------------- #
# Cost model (Build Sheet §2). USD per 1K tokens, blended input/output. These
# are deliberately small, order-of-magnitude constants: the cap only needs to
# stop runaway spend, not do accounting. deepseek-chat is the default provider;
# gpt-4o-mini is the circuit-breaker fallback. Unknown models fall back to the
# most expensive row so we never *under*-count spend against the cap.
# --------------------------------------------------------------------------- #
_PRICE_PER_1K: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_1k, output_usd_per_1k)
    "deepseek-chat": (0.00027, 0.00110),
    "gpt-4o-mini": (0.00015, 0.00060),
}
# Flat estimate used when token counts are unavailable (e.g. the stream errored
# before a FinishEvent). Roughly one small grounded answer.
_FLAT_COST_USD = 0.01


def limits_disabled() -> bool:
    """True when limiting is turned off (read at call time, not import).

    Off when ``PAPERLENS_DISABLE_LIMITS=1`` (local dev / verification), and always
    off under pytest: the suite POSTs ``/v1/chat/streams`` several times per run
    from one client IP, and a live-Redis fixed-window counter would persist across
    runs and eventually 429 the suite. ``test_limits.py`` drives the limiter
    functions directly (bypassing this check), so nothing loses coverage.
    """
    return (
        os.getenv("PAPERLENS_DISABLE_LIMITS") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
    )


# --------------------------------------------------------------------------- #
# Redis client — one lazily-built, module-level async client (a new pool per
# request would defeat the purpose). Mirrors chat.py's helper. Any failure
# (import or constructor) returns None and every caller then fails open.
# --------------------------------------------------------------------------- #
_redis_client = None
_redis_init = False


def _get_redis():
    global _redis_client, _redis_init
    if _redis_init:
        return _redis_client
    _redis_init = True
    try:
        from redis.asyncio import Redis

        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:  # pragma: no cover - defensive
        _redis_client = None
    return _redis_client


# --------------------------------------------------------------------------- #
# Client IP
# --------------------------------------------------------------------------- #
def client_ip(request: Request) -> str:
    """Best-effort client IP for keying the limiter.

    Honors the first hop of ``X-Forwarded-For`` because a BFF proxy fronts the
    API. NOTE: this trusts the proxy — a *direct* caller could spoof the header,
    but the BFF is the only intended ingress, so that is acceptable here.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


# --------------------------------------------------------------------------- #
# Fixed-window rate limiter
# --------------------------------------------------------------------------- #
async def _check_rate(bucket: str, ip: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Increment the (bucket, ip, window) counter. Return (allowed, retry_after).

    Fail-open: any Redis error returns (True, 0) so a limiter hiccup never blocks
    or 500s the request.
    """
    client = _get_redis()
    if client is None:
        return True, 0
    now = int(time.time())
    window_index = now // window_s
    key = f"{_RL_PREFIX}:{bucket}:{ip}:{window_index}"
    try:
        count = await client.incr(key)
        if count == 1:
            # First hit in this window — set the expiry so the counter resets.
            await client.expire(key, window_s)
        if count <= limit:
            return True, 0
        # Over the limit: Retry-After = seconds until this window rolls over.
        ttl = await client.ttl(key)
        retry_after = ttl if ttl and ttl > 0 else window_s
        return False, int(retry_after)
    except Exception:  # pragma: no cover - defensive; fail open
        return True, 0


async def _enforce_rate(request: Request, bucket: str, limit: int, window_s: int) -> None:
    if limits_disabled():
        return
    ip = client_ip(request)
    allowed, retry_after = await _check_rate(bucket, ip, limit, window_s)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({limit} per {window_s}s); retry later",
            headers={"Retry-After": str(retry_after)},
        )


# --------------------------------------------------------------------------- #
# Daily cost cap
# --------------------------------------------------------------------------- #
def _cost_key(day: str | None = None) -> str:
    day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{_COST_PREFIX}:{day}"


async def get_today_cost() -> float:
    """Return today's accumulated spend in USD (0.0 on any miss/error)."""
    client = _get_redis()
    if client is None:
        return 0.0
    try:
        raw = await client.get(_cost_key())
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0  # garbage value — treat as zero rather than block
    except Exception:  # pragma: no cover - defensive; fail open
        return 0.0


async def add_cost(usd: float) -> None:
    """Accumulate ``usd`` onto today's counter (best-effort, fail-open).

    Called by the chat route after a generation completes. A limiter failure here
    must never crash the (detached) producer, so all errors are swallowed.
    """
    if usd <= 0:
        return
    client = _get_redis()
    if client is None:
        return
    key = _cost_key()
    try:
        await client.incrbyfloat(key, float(usd))
        # Refresh the TTL so the day's counter self-expires ~48h later.
        await client.expire(key, _COST_TTL_S)
    except Exception:  # pragma: no cover - defensive; fail open
        pass


def estimate_chat_cost(
    model: str | None, prompt_tokens: int | None, completion_tokens: int | None
) -> float:
    """Estimate the USD cost of one generation from FinishEvent token counts.

    Uses the blended per-1K price for ``model`` (falling back to the priciest row
    for unknown models so we never under-count). When token counts are missing,
    returns a small flat per-request estimate.
    """
    if prompt_tokens is None and completion_tokens is None:
        return _FLAT_COST_USD
    if model in _PRICE_PER_1K:
        in_price, out_price = _PRICE_PER_1K[model]
    else:
        # Unknown model — assume the most expensive known row (conservative).
        in_price = max(p[0] for p in _PRICE_PER_1K.values())
        out_price = max(p[1] for p in _PRICE_PER_1K.values())
    p = prompt_tokens or 0
    c = completion_tokens or 0
    return (p / 1000.0) * in_price + (c / 1000.0) * out_price


# --------------------------------------------------------------------------- #
# FastAPI dependencies (routes add these to their decorator's dependencies=[...])
# --------------------------------------------------------------------------- #
async def chat_rate_limit(request: Request) -> None:
    """429 if this IP exceeded the chat message rate limit."""
    await _enforce_rate(
        request,
        bucket="messages",
        limit=settings.rate_limit_messages,
        window_s=settings.rate_limit_messages_window_s,
    )


async def upload_rate_limit(request: Request) -> None:
    """429 if this IP exceeded the upload rate limit."""
    await _enforce_rate(
        request,
        bucket="uploads",
        limit=settings.rate_limit_uploads,
        window_s=settings.rate_limit_uploads_window_s,
    )


async def check_cost_cap() -> None:
    """503 if today's accumulated spend already reached the daily cost cap."""
    if limits_disabled():
        return
    spent = await get_today_cost()
    if spent >= settings.daily_cost_cap_usd:
        raise HTTPException(
            status_code=503,
            detail="daily cost cap reached; generation temporarily disabled",
        )
