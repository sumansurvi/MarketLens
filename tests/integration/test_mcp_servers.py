"""MCP protocol suite (MCP_TOOLS §8): every server is spawned as its own stdio process and
exercised only through MCP protocol calls — never by importing the tool functions."""

from __future__ import annotations

import json

import pytest
from app.mcp.gateway import ALLOWED_TOOLS
from marketlens_core.config import Settings
from marketlens_core.policy import PROHIBITED_TOOL_FRAGMENTS
from marketlens_core.tool_models import (
    CompanyFactsData,
    FilingExcerptData,
    FilingsData,
    FinancialMetricsData,
    HistoricalPricesData,
    NewsData,
    PendingActionData,
    PriceChangeData,
    QuoteData,
    ResolveSymbolData,
    SavedBriefListData,
    ServerStatusData,
    SymbolSearchData,
    WatchlistData,
)

from tests.helpers import fault_payload, mcp_session, overlay_dataset, structured

pytestmark = pytest.mark.anyio

WINDOW = {"start_date": "2026-06-30", "end_date": "2026-09-30"}


@pytest.fixture
def fx_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/mcp.db")


def ok_data(result, model):
    payload = structured(result)
    assert result.is_error is False and payload["ok"] is True, payload
    assert payload["meta"]["request_id"].startswith("req_")
    return model.model_validate(payload["data"])


def error_code(result) -> str:
    payload = structured(result)
    assert result.is_error is True and payload["ok"] is False, payload
    return payload["error"]["code"]


# --------------------------------------------------------------------------- discovery
@pytest.mark.parametrize("server_id", sorted(ALLOWED_TOOLS))
async def test_handshake_and_tool_contracts(server_id: str, fx_settings: Settings) -> None:
    async with mcp_session(server_id, fx_settings) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == set(ALLOWED_TOOLS[server_id])
    for tool in tools:
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema is not None and {"ok", "data", "error", "meta"} <= set(
            tool.output_schema["properties"]
        )
        assert tool.annotations is not None
        assert not any(fragment in tool.name for fragment in PROHIBITED_TOOL_FRAGMENTS)
        if server_id != "research_workspace" or tool.name.startswith(("list_", "get_")):
            assert tool.annotations.read_only_hint is True, tool.name
        else:
            assert tool.annotations.read_only_hint is False, tool.name


async def test_schemas_advertise_allowlists_and_bounds(fx_settings: Settings) -> None:
    async with mcp_session("market_data", fx_settings) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    metrics = tools["get_financial_metrics"].input_schema["properties"]["metrics"]
    assert "revenue" in json.dumps(metrics) and "diluted_eps" in json.dumps(metrics)
    limit = tools["get_financial_metrics"].input_schema["properties"]["limit"]
    assert limit["minimum"] == 1 and limit["maximum"] == 12


# --------------------------------------------------------------------------- valid calls, every tool
async def test_market_data_tools_return_schema_valid_fixture_results(fx_settings: Settings) -> None:
    async with mcp_session("market_data", fx_settings) as c:
        quote = ok_data(await c.call_tool("get_quote", {"symbol": "aapl"}), QuoteData)
        assert quote.symbol == "AAPL" and quote.is_fixture and quote.observed_at is None
        assert quote.evidence[0].is_fixture and quote.evidence[0].source_mode.value == "fixture"
        series = ok_data(await c.call_tool("get_historical_prices", {"symbol": "AAPL", **WINDOW}), HistoricalPricesData)
        assert series.bars[0].trading_date.isoformat() == "2026-06-30" and series.adjustment == "unadjusted"
        change = ok_data(await c.call_tool("calculate_price_change", {"symbol": "AAPL", **WINDOW}), PriceChangeData)
        assert str(change.percentage_change) == "11.0565" and change.input_evidence_ids == [series.evidence_id]
        metrics = ok_data(
            await c.call_tool(
                "get_financial_metrics",
                {"symbol": "NVDA", "metrics": ["revenue", "reported_eps"], "period_type": "quarterly", "limit": 2},
            ),
            FinancialMetricsData,
        )
        assert {m.metric_name for m in metrics.metrics} == {"revenue", "reported_eps"}
        status = ok_data(await c.call_tool("get_provider_status", {}), ServerStatusData)
        assert status.is_fixture and status.fixture_dataset_version == "fx-2026-09-30-v1"


async def test_research_sources_tools_return_schema_valid_fixture_results(fx_settings: Settings) -> None:
    async with mcp_session("research_sources", fx_settings) as c:
        identity = ok_data(await c.call_tool("resolve_symbol", {"symbol": "msft"}), ResolveSymbolData)
        assert identity.cik == "0000789019"
        by_ticker = ok_data(await c.call_tool("search_symbols", {"query": "nv"}), SymbolSearchData)
        assert [m.symbol for m in by_ticker.matches] == ["NVDA"] and by_ticker.is_fixture
        by_name = ok_data(await c.call_tool("search_symbols", {"query": "micro", "limit": 3}), SymbolSearchData)
        assert [(m.symbol, m.company_name) for m in by_name.matches] == [("MSFT", "Microsoft Corporation")]
        everything = ok_data(await c.call_tool("search_symbols", {"query": "  a "}), SymbolSearchData)
        assert everything.query == "a" and everything.matches[0].symbol == "AAPL"  # ticker prefix before name words
        assert ok_data(await c.call_tool("search_symbols", {"query": "zzz"}), SymbolSearchData).matches == []
        filings = ok_data(await c.call_tool("search_company_filings", {"symbol": "AAPL", "limit": 5}), FilingsData)
        assert filings.filings and all(f.form in ("10-K", "10-Q", "8-K") for f in filings.filings)
        facts = ok_data(
            await c.call_tool(
                "get_sec_company_facts",
                {"symbol": "NVDA", "concepts": ["Revenues", "NetIncomeLoss"], "period_type": "annual", "limit": 2},
            ),
            CompanyFactsData,
        )
        assert {f.concept for f in facts.facts} == {"Revenues", "NetIncomeLoss"}
        news = ok_data(
            await c.call_tool(
                "search_company_news",
                {"symbol": "AAPL", "from_date": "2026-06-30", "to_date": "2026-09-30", "limit": 10},
            ),
            NewsData,
        )
        assert len(news.articles) == 7 and any("suspected_prompt_injection" in a.flags for a in news.articles)
        url = next(f.source_url for f in filings.filings if f.form == "10-Q")
        excerpt = ok_data(
            await c.call_tool("get_filing_excerpt", {"filing_url": url, "max_chars": 800}), FilingExcerptData
        )
        assert excerpt.section_found and len(excerpt.excerpt) <= 800
        status = ok_data(await c.call_tool("get_research_sources_status", {}), ServerStatusData)
        assert {s.provider for s in status.sources} == {"sec_edgar", "alpha_vantage"}


async def test_workspace_read_tools_and_proposal(fx_settings: Settings) -> None:
    from marketlens_core.actions import action_payload, payload_hash_of
    from marketlens_core.hashing import hash_secret

    async with mcp_session("research_workspace", fx_settings) as c:
        assert ok_data(await c.call_tool("list_watchlist", {}), WatchlistData).items == []
        assert ok_data(await c.call_tool("list_saved_briefs", {"limit": 5}), SavedBriefListData).briefs == []
        missing = await c.call_tool("get_saved_brief", {"brief_id": "brief_0000000000000000"})
        assert error_code(missing) == "NOT_FOUND"
        proposal = await c.call_tool(
            "create_pending_action",
            {
                "action_type": "add_watchlist_symbol",
                "payload_summary": {"symbol": "AAPL"},
                "payload_hash": payload_hash_of(action_payload("add_watchlist_symbol", symbol="AAPL")),
                "session_id": hash_secret("session-a"),
            },
        )
        action = ok_data(proposal, PendingActionData).action
        assert action.status.value == "pending" and action.symbol == "AAPL"
        assert ok_data(await c.call_tool("list_watchlist", {}), WatchlistData).items == []  # proposal wrote nothing
        tampered = await c.call_tool(
            "create_pending_action",
            {
                "action_type": "add_watchlist_symbol",
                "payload_summary": {"symbol": "AAPL"},
                "payload_hash": payload_hash_of(action_payload("add_watchlist_symbol", symbol="MSFT")),
                "session_id": hash_secret("session-a"),
            },
        )
        assert error_code(tampered) == "PAYLOAD_HASH_MISMATCH"


# --------------------------------------------------------------------------- input rejection
INVALID_CALLS = [
    ("market_data", "get_quote", {}),
    ("market_data", "get_quote", {"symbol": 123}),
    ("market_data", "get_quote", {"symbol": "A" * 40}),
    ("market_data", "get_historical_prices", {"symbol": "AAPL", "start_date": "not-a-date", "end_date": "2026-09-30"}),
    ("market_data", "get_historical_prices", {"symbol": "AAPL", **WINDOW, "interval": "weekly"}),
    ("market_data", "get_financial_metrics", {"symbol": "AAPL", "metrics": ["consensus_eps_estimate"]}),
    ("market_data", "get_financial_metrics", {"symbol": "AAPL", "metrics": ["revenue"], "limit": 99}),
    ("market_data", "calculate_price_change", {"symbol": "AAPL", **WINDOW, "price_field": "vwap"}),
    ("research_sources", "search_company_filings", {"symbol": "AAPL", "forms": ["S-1"]}),
    ("research_sources", "search_company_filings", {"symbol": "AAPL", "limit": 0}),
    ("research_sources", "get_sec_company_facts", {"symbol": "AAPL", "concepts": ["DROP TABLE x"]}),
    (
        "research_sources",
        "search_company_news",
        {"symbol": "AAPL", "from_date": "2026-01-01", "to_date": "2026-02-01", "limit": 51},
    ),
    (
        "research_sources",
        "search_company_news",
        {"symbol": "AAPL", "from_date": "2026-01-01", "to_date": "2026-02-01", "query": "x" * 500},
    ),
    ("research_sources", "get_filing_excerpt", {"filing_url": "https://www.sec.gov/x/y/z", "max_chars": 6000}),
    ("research_workspace", "list_saved_briefs", {"limit": 500}),
    ("research_workspace", "get_saved_brief", {"brief_id": "../../etc/passwd"}),
    ("research_workspace", "add_watchlist_symbol", {"symbol": "AAPL"}),
    ("research_workspace", "add_watchlist_symbol", {"symbol": "AAPL", "confirmed": True}),
    ("research_workspace", "save_research_brief", {"run_id": "run_0000000000000000", "brief_title": "x"}),
    (
        "research_workspace",
        "create_pending_action",
        {
            "action_type": "place_order",
            "payload_summary": {},
            "payload_hash": "sha256:" + "0" * 64,
            "session_id": "0" * 64,
        },
    ),
]


@pytest.mark.parametrize("server_id, tool, arguments", INVALID_CALLS)
async def test_invalid_arguments_are_rejected_before_the_tool_runs(server_id, tool, arguments, fx_settings) -> None:
    async with mcp_session(server_id, fx_settings) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error is True
    text = json.dumps(result.structured_content) if result.structured_content else result.content[0].text
    assert "Traceback" not in text


DOMAIN_ERRORS = [
    ("market_data", "get_quote", {"symbol": "TSLA"}, "UNKNOWN_SYMBOL"),
    ("market_data", "get_quote", {"symbol": "AAPL;DROP"}, "INVALID_SYMBOL"),
    (
        "market_data",
        "get_historical_prices",
        {"symbol": "AAPL", "start_date": "2024-01-01", "end_date": "2026-09-30"},
        "DATE_RANGE_TOO_LARGE",
    ),
    (
        "market_data",
        "get_historical_prices",
        {"symbol": "AAPL", "start_date": "2026-09-30", "end_date": "2026-06-30"},
        "INVALID_INPUT",
    ),
    (
        "market_data",
        "get_historical_prices",
        {"symbol": "AAPL", "start_date": "2025-01-01", "end_date": "2025-02-01"},
        "DATA_UNAVAILABLE",
    ),
    ("research_sources", "get_filing_excerpt", {"filing_url": "file:///etc/passwd"}, "INVALID_INPUT"),
    (
        "research_sources",
        "get_filing_excerpt",
        {"filing_url": "http://169.254.169.254/latest/meta-data/"},
        "INVALID_INPUT",
    ),
    (
        "research_sources",
        "get_filing_excerpt",
        {"filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000079/aapl-20260627.htm"},
        "INVALID_INPUT",
    ),
    (
        "research_sources",
        "get_filing_excerpt",
        {
            "filing_url": "https://example.com/marketlens-fixture/sec/Archives/edgar/data/320193/000032019326999999/x.htm"
        },
        "INVALID_INPUT",
    ),
    (
        "research_sources",
        "search_company_news",
        {"symbol": "MSFT", "from_date": "2026-06-30", "to_date": "2026-09-30"},
        "CAPABILITY_UNAVAILABLE",
    ),
]


@pytest.mark.parametrize("server_id, tool, arguments, code", DOMAIN_ERRORS)
async def test_domain_errors_use_stable_codes(server_id, tool, arguments, code, fx_settings) -> None:
    async with mcp_session(server_id, fx_settings) as client:
        result = await client.call_tool(tool, arguments)
    assert error_code(result) == code
    assert "Traceback" not in json.dumps(structured(result))


# --------------------------------------------------------------------------- simulated provider faults
FAULTS = [
    (
        "market_data",
        "get_quote",
        {"symbol": "AAPL"},
        "alpha_vantage/GLOBAL_QUOTE/AAPL.json",
        fault_payload("alpha_vantage:GLOBAL_QUOTE", "timeout"),
        "PROVIDER_TIMEOUT",
        True,
    ),
    (
        "market_data",
        "get_historical_prices",
        {"symbol": "AAPL", **WINDOW},
        "alpha_vantage/TIME_SERIES_DAILY/AAPL.json",
        fault_payload("alpha_vantage:TIME_SERIES_DAILY", "rate_limited", retry_after_seconds=30),
        "RATE_LIMITED",
        True,
    ),
    (
        "research_sources",
        "search_company_news",
        {"symbol": "NVDA", "from_date": "2026-06-30", "to_date": "2026-09-30"},
        "alpha_vantage/NEWS_SENTIMENT/NVDA.json",
        fault_payload("alpha_vantage:NEWS_SENTIMENT", "capability_unavailable"),
        "CAPABILITY_UNAVAILABLE",
        False,
    ),
    (
        "market_data",
        "get_quote",
        {"symbol": "NVDA"},
        "alpha_vantage/GLOBAL_QUOTE/NVDA.json",
        "{not json",
        "MALFORMED_PROVIDER_RESPONSE",
        False,
    ),
    (
        "market_data",
        "get_historical_prices",
        {"symbol": "MSFT", **WINDOW},
        "alpha_vantage/TIME_SERIES_DAILY/MSFT.json",
        {
            "_fixture": {"is_fixture": True, "source_mode": "fixture", "label": "SYNTHETIC TEST OVERLAY"},
            "Meta Data": {},
            "Time Series (Daily)": {"2026-09-30": {"1. open": "abc"}},
        },
        "MALFORMED_PROVIDER_RESPONSE",
        False,
    ),
    (
        "research_sources",
        "get_sec_company_facts",
        {"symbol": "AAPL", "concepts": ["NetIncomeLoss"]},
        "sec/companyfacts/CIK0000320193.json",
        fault_payload("sec_edgar:companyfacts", "auth_failed"),
        "PROVIDER_AUTH_FAILED",
        False,
    ),
    (
        "research_sources",
        "search_company_filings",
        {"symbol": "AAPL"},
        "sec/submissions/CIK0000320193.json",
        {"filings": {"recent": {}}},
        "MALFORMED_PROVIDER_RESPONSE",
        False,
    ),
]


@pytest.mark.parametrize("server_id, tool, arguments, relative, content, code, retryable", FAULTS)
async def test_provider_faults_map_to_safe_structured_errors(
    server_id, tool, arguments, relative, content, code, retryable, tmp_path
) -> None:
    dataset = overlay_dataset(tmp_path, {relative: content})
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/f.db", fixture_dir=str(dataset))
    async with mcp_session(server_id, settings) as client:
        result = await client.call_tool(tool, arguments)
    assert error_code(result) == code
    error = structured(result)["error"]
    assert error["retryable"] is retryable
    assert error["message"] and "Traceback" not in error["message"] and "/Users/" not in json.dumps(error)
