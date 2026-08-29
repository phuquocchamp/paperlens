"""Unit tests for the rate limiter + daily cost cap (app/api/limits.py).

No new deps: a ~20-line fake async Redis (incr/expire/ttl/get/incrbyfloat) is
monkeypatched over the module's client getter. Covers the window-rollover 429
branch, the over-cap 503 branch, cost accumulation, the fail-open path, and the
PAPERLENS_DISABLE_LIMITS bypass.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from app.api import limits


@pytest.fixture(autouse=True)
def _limits_honor_env_only(monkeypatch):
    """Under pytest ``limits_disabled()`` short-circuits (see limits.py). These
    tests exercise the real limiter, so make the check honor ONLY the env flag —
    that keeps ``test_disable_flag_bypasses`` meaningful while every other test
    runs with limits ON."""
    monkeypatch.setattr(
        limits,
        "limits_disabled",
        lambda: os.getenv("PAPERLENS_DISABLE_LIMITS") == "1",
    )
    monkeypatch.delenv("PAPERLENS_DISABLE_LIMITS", raising=False)


class _FakeRedis:
    """Minimal in-memory async stand-in for the bits limits.py uses."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = float(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])

    async def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return True

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    async def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v)

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + amount
        return self.store[key]


class _Req:
    """Duck-typed FastAPI Request: .headers.get + .client.host."""

    def __init__(self, ip: str = "1.2.3.4", xff: str | None = None) -> None:
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": ip})()

    # limits.client_ip calls request.headers.get(...)
    def _hget(self, k, default=None):  # pragma: no cover - unused helper
        return self.headers.get(k, default)


@pytest.fixture()
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(limits, "_get_redis", lambda: r)
    # Ensure limits are ON for these tests regardless of the session-wide flag.
    monkeypatch.delenv("PAPERLENS_DISABLE_LIMITS", raising=False)
    return r


# Wrap the dict-headers so .get works like Starlette Headers.
class _Headers(dict):
    pass


def _req(ip="1.2.3.4", xff=None):
    r = _Req(ip=ip, xff=xff)
    r.headers = _Headers(r.headers)
    return r


def test_client_ip_prefers_xff_first_hop():
    r = _req(ip="10.0.0.1", xff="203.0.113.9, 10.0.0.1")
    assert limits.client_ip(r) == "203.0.113.9"


def test_client_ip_falls_back_to_peer():
    assert limits.client_ip(_req(ip="192.168.1.5")) == "192.168.1.5"


async def test_rate_limit_allows_then_429(fake_redis, monkeypatch):
    # Force a tiny limit so the branch is cheap to hit.
    monkeypatch.setattr(limits.settings, "rate_limit_messages", 3, raising=False)
    monkeypatch.setattr(limits.settings, "rate_limit_messages_window_s", 600, raising=False)
    req = _req(ip="9.9.9.9")
    # First 3 pass.
    for _ in range(3):
        await limits.chat_rate_limit(req)
    # 4th trips 429 with a Retry-After header.
    with pytest.raises(HTTPException) as ei:
        await limits.chat_rate_limit(req)
    assert ei.value.status_code == 429
    assert "Retry-After" in ei.value.headers
    assert int(ei.value.headers["Retry-After"]) > 0


async def test_rate_limit_isolated_per_ip(fake_redis, monkeypatch):
    monkeypatch.setattr(limits.settings, "rate_limit_messages", 1, raising=False)
    await limits.chat_rate_limit(_req(ip="a"))
    # Different IP still allowed (separate counter).
    await limits.chat_rate_limit(_req(ip="b"))
    with pytest.raises(HTTPException):
        await limits.chat_rate_limit(_req(ip="a"))


async def test_cost_cap_blocks_when_over(fake_redis, monkeypatch):
    monkeypatch.setattr(limits.settings, "daily_cost_cap_usd", 5.0, raising=False)
    # Under cap: no error.
    await limits.add_cost(4.99)
    await limits.check_cost_cap()
    # Push over the cap.
    await limits.add_cost(0.02)
    with pytest.raises(HTTPException) as ei:
        await limits.check_cost_cap()
    assert ei.value.status_code == 503


async def test_add_cost_accumulates(fake_redis):
    assert await limits.get_today_cost() == 0.0
    await limits.add_cost(0.10)
    await limits.add_cost(0.05)
    assert abs(await limits.get_today_cost() - 0.15) < 1e-9


def test_estimate_chat_cost_uses_token_counts():
    # deepseek-chat: 1000 in * 0.00027 + 1000 out * 0.00110 = 0.00137
    c = limits.estimate_chat_cost("deepseek-chat", 1000, 1000)
    assert abs(c - 0.00137) < 1e-9


def test_estimate_chat_cost_flat_when_tokens_missing():
    assert limits.estimate_chat_cost("deepseek-chat", None, None) == limits._FLAT_COST_USD


def test_estimate_chat_cost_unknown_model_is_conservative():
    # Unknown model uses the priciest row -> at least the deepseek output price.
    c = limits.estimate_chat_cost("mystery-model", 0, 1000)
    assert c >= 1000 / 1000.0 * 0.00110


async def test_fail_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(limits, "_get_redis", lambda: None)
    monkeypatch.delenv("PAPERLENS_DISABLE_LIMITS", raising=False)
    monkeypatch.setattr(limits.settings, "rate_limit_messages", 1, raising=False)
    # No Redis -> never blocks.
    for _ in range(5):
        await limits.chat_rate_limit(_req(ip="x"))
    await limits.check_cost_cap()
    assert await limits.get_today_cost() == 0.0


async def test_disable_flag_bypasses(fake_redis, monkeypatch):
    monkeypatch.setenv("PAPERLENS_DISABLE_LIMITS", "1")
    monkeypatch.setattr(limits.settings, "rate_limit_messages", 1, raising=False)
    monkeypatch.setattr(limits.settings, "daily_cost_cap_usd", 0.0, raising=False)
    # Even over the (zeroed) cap and past the limit, both bypass.
    for _ in range(5):
        await limits.chat_rate_limit(_req(ip="y"))
    await limits.check_cost_cap()
