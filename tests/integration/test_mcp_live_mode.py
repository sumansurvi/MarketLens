"""Live mode through real MCP server processes against a local fake upstream.

Proves that live-provider failures map to safe structured errors (never silently become
fixture results), that caching and the SEC User-Agent work, and that the API key never
leaks into tool output. No real provider is contacted."""

from __future__ import annotations

import json

import pytest
from marketlens_core.config import Settings

from tests.helpers import FakeUpstream, mcp_session, structured

pytestmark = pytest.mark.anyio
API_KEY = "LIVEKEY0123456789ABCDEF"
UA = "MarketLens-tests qa@example.org"


def daily_series() -> str:
    bars = {}
    for day, close in (("2026-09-28", "100.0000"), ("2026-09-29", "102.0000"), ("2026-09-30", "99.0000")):
        bars[day] = {
            "1. open": close,
            "2. high": "103.0000",
            "3. low": "98.0000",
            "4. close": close,
            "5. volume": "1000",
        }
    return json.dumps(
        {"Meta Data": {"3. Last Refreshed": "2026-09-30", "5. Time Zone": "US/Eastern"}, "Time Series (Daily)": bars}
    )


@pytest.fixture
def upstream():
    with FakeUpstream() as fake:
        fake.routes = {
            "GLOBAL_QUOTE": lambda path, q: (
                (
                    200,
                    json.dumps(
                        {
                            "Global Quote": {
                                "01. symbol": "SLOW",
                                "05. price": "1.0000",
                                "07. latest trading day": "2026-09-30",
                            }
                        }
                    ),
                    3.0,
                )
                if q["symbol"] == ["SLOW"]
                else (
                    200,
                    json.dumps(
                        {
                            "Note": "Thank you for using Alpha Vantage! Our standard API call frequency is "
                            "5 calls per minute and 25 requests per day."
                        }
                    ),
                    0.0,
                )
            ),
            "TIME_SERIES_DAILY": lambda path, q: (200, daily_series(), 0.0),
            "TIME_SERIES_DAILY_ADJUSTED": lambda path, q: (
                200,
                json.dumps({"Information": "Thank you for using Alpha Vantage! This is a premium endpoint."}),
                0.0,
            ),
            "INCOME_STATEMENT": lambda path, q: (
                200,
                json.dumps({"Information": "Thank you for using Alpha Vantage! This is a premium endpoint."}),
                0.0,
            ),
            "EARNINGS": lambda path, q: (503, "unavailable", 0.0),
            "NEWS_SENTIMENT": lambda path, q: (200, "{not json", 0.0),
            "/sec-www/files/company_tickers_exchange.json": lambda path, q: (
                200,
                json.dumps(
                    {
                        "fields": ["cik", "name", "ticker", "exchange"],
                        "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [999999, "Pink Co", "PINKX", "OTC"]],
                    }
                ),
                0.0,
            ),
            "/sec-data/submissions/CIK0000320193.json": lambda path, q: (429, "{}", 0.0),
            "/sec-data/api/xbrl/companyfacts/CIK0000320193.json": lambda path, q: (
                200,
                "<html>maintenance</html>",
                0.0,
            ),
        }
        yield fake


def live_settings(tmp_path, upstream: FakeUpstream) -> Settings:
    return Settings(
        _env_file=None,
        app_mode="live",
        data_mode="live",
        database_url=f"sqlite:///{tmp_path}/live.db",
        alpha_vantage_api_key=API_KEY,
        alpha_vantage_base_url=f"{upstream.base}/query",
        alpha_vantage_max_requests_per_minute=1200,
        market_data_timeout_seconds=1,
        market_data_max_retries=1,
        sec_user_agent=UA,
        sec_data_base_url=f"{upstream.base}/sec-data",
        sec_www_base_url=f"{upstream.base}/sec-www",
        sec_timeout_seconds=1,
        sec_max_requests_per_second=10,
        yahoo_news_base_url=f"{upstream.base}/yahoo/headline",
    )


def error(result) -> dict:
    payload = structured(result)
    assert result.is_error and not payload["ok"], payload
    return payload["error"]


async def test_live_market_data_errors_and_successes(tmp_path, upstream) -> None:
    settings = live_settings(tmp_path, upstream)
    outputs = []
    async with mcp_session("market_data", settings) as c:
        quota = await c.call_tool("get_quote", {"symbol": "AAPL"})
        assert error(quota)["code"] == "RATE_LIMITED" and error(quota)["retryable"] is True
        series_raw = await c.call_tool(
            "get_historical_prices",
            {"symbol": "AAPL", "start_date": "2026-09-01", "end_date": "2026-09-30", "adjusted": True},
        )
        series = structured(series_raw)
        assert series["ok"] and series["data"]["is_fixture"] is False and series["data"]["adjustment"] == "unadjusted"
        assert series["data"]["evidence"][0]["source_mode"] == "live"
        assert any("premium" in w or "Adjusted prices are not available" in w for w in series["meta"]["warnings"])
        change_raw = await c.call_tool(
            "calculate_price_change", {"symbol": "AAPL", "start_date": "2026-09-01", "end_date": "2026-09-30"}
        )
        change = structured(change_raw)
        assert change["ok"] and change["data"]["percentage_change"] == -1.0
        metrics = await c.call_tool("get_financial_metrics", {"symbol": "AAPL", "metrics": ["revenue"]})
        assert error(metrics)["code"] == "CAPABILITY_UNAVAILABLE"
        eps = await c.call_tool("get_financial_metrics", {"symbol": "AAPL", "metrics": ["reported_eps"]})
        assert error(eps)["code"] == "DATA_UNAVAILABLE" and error(eps)["retryable"] is True
        slow = await c.call_tool("get_quote", {"symbol": "SLOW"})
        assert error(slow)["code"] == "PROVIDER_TIMEOUT"
        outputs = [quota, series_raw, change_raw, metrics, eps, slow]
    assert upstream.count("TIME_SERIES_DAILY") == 1  # second use served from the provider cache
    assert upstream.count("EARNINGS") == 2  # one bounded retry, then a safe error
    assert all(r["query"].get("apikey") == [API_KEY] for r in upstream.requests if "function" in r["query"])
    for result in outputs:
        text = json.dumps(result.structured_content) if result.structured_content else result.content[0].text
        assert API_KEY not in text and '"is_fixture": true' not in text


async def test_live_research_sources_errors_and_user_agent(tmp_path, upstream) -> None:
    settings = live_settings(tmp_path, upstream)
    async with mcp_session("research_sources", settings) as c:
        identity = structured(await c.call_tool("resolve_symbol", {"symbol": "aapl"}))
        assert identity["ok"] and identity["data"]["coverage"] == "live" and identity["data"]["is_fixture"] is False
        otc = await c.call_tool("resolve_symbol", {"symbol": "PINKX"})
        assert error(otc)["code"] == "UNKNOWN_SYMBOL" and "NYSE and Nasdaq" in error(otc)["message"]
        missing = await c.call_tool("resolve_symbol", {"symbol": "NOPE"})
        assert error(missing)["code"] == "UNKNOWN_SYMBOL"
        suggested = structured(await c.call_tool("search_symbols", {"query": "p"}))
        assert suggested["ok"] and suggested["data"]["matches"] == []  # PINKX is OTC, so it is never offered
        largest = structured(await c.call_tool("search_symbols", {}))
        assert [m["symbol"] for m in largest["data"]["matches"]] == ["AAPL"]  # an empty query skips OTC too
        apple = structured(await c.call_tool("search_symbols", {"query": "apple"}))
        assert [m["symbol"] for m in apple["data"]["matches"]] == ["AAPL"] and apple["data"]["coverage"] == "live"
        filings = await c.call_tool("search_company_filings", {"symbol": "AAPL"})
        assert error(filings)["code"] == "RATE_LIMITED"
        facts = await c.call_tool("get_sec_company_facts", {"symbol": "AAPL", "concepts": ["NetIncomeLoss"]})
        assert error(facts)["code"] == "MALFORMED_PROVIDER_RESPONSE"
        news = await c.call_tool(
            "search_company_news", {"symbol": "AAPL", "from_date": "2026-09-01", "to_date": "2026-09-30"}
        )
        assert error(news)["code"] == "MALFORMED_PROVIDER_RESPONSE"
        for result in (otc, missing, filings, facts, news):
            assert API_KEY not in json.dumps(result.structured_content)
    sec_requests = [r for r in upstream.requests if r["path"].startswith("/sec-")]
    assert sec_requests and all(r["headers"].get("User-Agent") == UA for r in sec_requests)
