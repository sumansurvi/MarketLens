"""Live provider transports with a mocked HTTP layer (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest
from marketlens_core.config import Settings
from marketlens_core.db import Database, initialize_database
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.providers import live_transport
from marketlens_core.providers.alpha_vantage import classify_message
from marketlens_core.providers.live_transport import (
    LiveAlphaVantageTransport,
    LiveSecTransport,
    ProviderCache,
    SharedRateLimiter,
)

pytestmark = pytest.mark.anyio
KEY = "AVKEY0000111122223333"


def settings(**extra) -> Settings:
    base = dict(
        _env_file=None,
        alpha_vantage_api_key=KEY,
        sec_user_agent="MarketLens tests qa@example.org",
        market_data_max_retries=2,
        alpha_vantage_max_requests_per_minute=60_000 // 60,
        sec_max_requests_per_second=10,
    )
    base.update(extra)
    return Settings(**base)


def av(handler, *, cache=None, **extra) -> LiveAlphaVantageTransport:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return LiveAlphaVantageTransport(settings(**extra), cache=cache, client=client)


async def test_success_then_cache_hit_without_leaking_key(tmp_path) -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        return httpx2.Response(200, json={"Global Quote": {"05. price": "1.00"}})

    db = Database(tmp_path / "c.db")
    initialize_database(db)
    transport = av(handler, cache=ProviderCache(db))
    first = await transport.query({"function": "GLOBAL_QUOTE", "symbol": "AAPL"})
    second = await transport.query({"function": "GLOBAL_QUOTE", "symbol": "AAPL"})
    assert len(calls) == 1 and f"apikey={KEY}" in calls[0]
    assert first.from_cache is False and second.from_cache is True and second.retrieved_at == first.retrieved_at
    assert KEY not in first.request_label
    with db.read() as conn:
        dump = json.dumps([tuple(r) for r in conn.execute("SELECT * FROM provider_cache")])
    assert KEY not in dump


async def test_rate_limit_messages_are_not_cached(tmp_path) -> None:
    db = Database(tmp_path / "c.db")
    initialize_database(db)
    transport = av(
        lambda r: httpx2.Response(200, json={"Note": "standard API call frequency is 5 calls per minute"}),
        cache=ProviderCache(db),
    )
    await transport.query({"function": "GLOBAL_QUOTE", "symbol": "AAPL"})
    with db.read() as conn:
        assert conn.execute("SELECT count(*) FROM provider_cache").fetchone()[0] == 0


@pytest.mark.parametrize(
    "status, code, attempts",
    [
        (500, ErrorCode.DATA_UNAVAILABLE, 3),
        (429, ErrorCode.RATE_LIMITED, 2),
        (401, ErrorCode.PROVIDER_AUTH_FAILED, 1),
        (404, ErrorCode.DATA_UNAVAILABLE, 1),
    ],
)
async def test_http_errors_are_translated_with_bounded_retries(status, code, attempts) -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(status, json={}, headers={"retry-after": "1"})

    with pytest.raises(MarketLensError) as err:
        await av(handler).query({"function": "TIME_SERIES_DAILY", "symbol": "AAPL"})
    assert err.value.code == code and len(calls) == attempts
    assert KEY not in str(err.value) and KEY not in json.dumps(err.value.details)


async def test_retry_then_success() -> None:
    responses = iter([httpx2.Response(503), httpx2.Response(200, json={"ok": 1})])
    payload = await av(lambda r: next(responses)).query({"function": "EARNINGS", "symbol": "AAPL"})
    assert payload.data == {"ok": 1}


async def test_timeouts_and_malformed_json() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(MarketLensError) as err:
        await av(timeout, market_data_max_retries=0).query({"function": "GLOBAL_QUOTE", "symbol": "AAPL"})
    assert err.value.code == ErrorCode.PROVIDER_TIMEOUT and err.value.retryable
    with pytest.raises(MarketLensError) as bad:
        await av(lambda r: httpx2.Response(200, text="<html>")).query({"function": "GLOBAL_QUOTE", "symbol": "AAPL"})
    assert bad.value.code == ErrorCode.MALFORMED_PROVIDER_RESPONSE


async def test_sec_transport_sends_user_agent_and_explains_auth_failures() -> None:
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx2.Response(403, text="denied")

    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), headers={"User-Agent": "MarketLens tests qa@example.org"}
    )
    transport = LiveSecTransport(settings(), client=client)
    with pytest.raises(MarketLensError) as err:
        await transport.submissions("0000320193")
    assert err.value.code == ErrorCode.PROVIDER_AUTH_FAILED and "SEC_USER_AGENT" in err.value.message
    assert seen == ["MarketLens tests qa@example.org"]
    assert transport.archives_base == "https://www.sec.gov/Archives/edgar/data"


async def test_sec_documents_are_size_bounded() -> None:
    big = httpx2.Response(200, content=b"x" * (21 * 1024 * 1024))
    transport = LiveSecTransport(settings(), client=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: big)))
    with pytest.raises(MarketLensError) as err:
        await transport.document(320193, "000032019326000079", "aapl.htm")
    assert err.value.code == ErrorCode.DATA_UNAVAILABLE


def test_live_transports_require_configuration() -> None:
    with pytest.raises(MarketLensError):
        LiveAlphaVantageTransport(Settings(_env_file=None))
    with pytest.raises(MarketLensError):
        LiveSecTransport(Settings(_env_file=None, sec_user_agent="MarketLens/0.1 contact=your-email@example.com"))


BURST = {
    "Information": "Thank you for using Alpha Vantage! Please consider spreading out your free API requests more "
    "sparingly (1 request per second)."
}
DAILY = {
    "Information": "We have detected your API key as ABCDEFGHIJKLMNOP and our standard API rate limit is 25 requests "
    "per day. Please subscribe to any of the premium plans to instantly remove all daily rate limits."
}


def test_alpha_vantage_pacing_is_shared_by_every_process_using_the_database(tmp_path) -> None:
    """The market-data and research-sources servers are separate processes sharing one database."""
    initialize_database(Database(tmp_path / "c.db"))
    market = SharedRateLimiter(Database(tmp_path / "c.db"), "alpha_vantage", 0.5)
    sources = SharedRateLimiter(Database(tmp_path / "c.db"), "alpha_vantage", 0.5)
    waits = [market.reserve(), sources.reserve(), market.reserve()]
    assert waits[0] == pytest.approx(0.0, abs=0.05)
    assert waits[1] == pytest.approx(0.5, abs=0.05)
    assert waits[2] == pytest.approx(1.0, abs=0.05)


async def test_identical_concurrent_requests_share_one_upstream_call(tmp_path) -> None:
    calls = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        await asyncio.sleep(0.05)
        return httpx2.Response(200, json={"Time Series (Daily)": {"2026-09-30": {"4. close": "1.00"}}})

    db = Database(tmp_path / "c.db")
    initialize_database(db)
    transport = av(handler, cache=ProviderCache(db))
    params = {"function": "TIME_SERIES_DAILY", "symbol": "NVDA", "outputsize": "compact"}
    first, second = await asyncio.gather(transport.query(params), transport.query(params))
    assert len(calls) == 1
    assert first.data == second.data and sorted([first.from_cache, second.from_cache]) == [False, True]
    assert transport._inflight == {}


async def test_burst_rejection_is_retried_once_but_the_daily_quota_is_not(monkeypatch) -> None:
    monkeypatch.setattr(live_transport, "BURST_RETRY_SECONDS", 0.01)
    replies = [BURST, {"Global Quote": {"05. price": "1.00"}}]
    calls: list[int] = []

    def burst_then_data(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(200, json=replies[min(len(calls), 2) - 1])

    payload = await av(burst_then_data).query({"function": "GLOBAL_QUOTE", "symbol": "AAPL"})
    assert len(calls) == 2 and "Global Quote" in payload.data

    daily_calls: list[int] = []

    def always_daily(request: httpx2.Request) -> httpx2.Response:
        daily_calls.append(1)
        return httpx2.Response(200, json=DAILY)

    payload = await av(always_daily).query({"function": "GLOBAL_QUOTE", "symbol": "MSFT"})
    assert len(daily_calls) == 1
    error = classify_message(payload.data)
    assert error is not None and error.code == ErrorCode.RATE_LIMITED
    assert error.retryable is False and "tomorrow" in error.message and error.details["limit"] == "daily"
    assert "ABCDEFGHIJKLMNOP" not in str(error.details)

    still_limited: list[int] = []

    def always_burst(request: httpx2.Request) -> httpx2.Response:
        still_limited.append(1)
        return httpx2.Response(200, json=BURST)

    payload = await av(always_burst).query({"function": "GLOBAL_QUOTE", "symbol": "NVDA"})
    error = classify_message(payload.data)
    assert len(still_limited) == 2 and error is not None and error.retryable and error.details["limit"] == "burst"
