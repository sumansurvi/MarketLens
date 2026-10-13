"""Ticker suggestions for the research bar (GET /api/symbols), served by research_sources.search_symbols."""

from __future__ import annotations


def test_symbol_search_matches_ticker_and_company_name(client) -> None:
    by_ticker = client.get("/api/symbols", params={"q": "aa"})
    assert by_ticker.status_code == 200
    assert by_ticker.json()["matches"] == [{"symbol": "AAPL", "company_name": "Apple Inc.", "exchange": "Nasdaq"}]
    assert by_ticker.json()["is_fixture"] is True
    by_name = client.get("/api/symbols", params={"q": "nvidia"}).json()
    assert [m["symbol"] for m in by_name["matches"]] == ["NVDA"]
    assert client.get("/api/symbols", params={"q": "zzz"}).json()["matches"] == []


def test_symbol_search_without_a_query_lists_the_covered_companies(client) -> None:
    for params in ({}, {"q": ""}, {"q": "   "}):
        body = client.get("/api/symbols", params=params).json()
        assert [m["symbol"] for m in body["matches"]] == ["AAPL", "MSFT", "NVDA"], params  # fixture file order
    assert [m["symbol"] for m in client.get("/api/symbols", params={"limit": 2}).json()["matches"]] == ["AAPL", "MSFT"]


def test_symbol_search_validates_the_query(client) -> None:
    for params in ({"q": "x" * 41}, {"q": "a", "limit": 0}, {"limit": 21}):
        response = client.get("/api/symbols", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "INVALID_INPUT"
