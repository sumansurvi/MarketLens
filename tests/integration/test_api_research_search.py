"""Search past research (D-96): ticker, company name, question and kept findings, with snippets."""

from __future__ import annotations

from marketlens_core.db.repositories import _snippet

from tests.helpers import wait_for_run


def start(client, symbol: str, question: str) -> str:
    created = client.post("/api/research", json={"symbol": symbol, "question": question})
    assert created.status_code == 202, created.text
    run_id = created.json()["run"]["run_id"]
    wait_for_run(client, run_id)
    return run_id


def search(client, q: str, **params) -> list[dict]:
    response = client.get("/api/research/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == q
    return body["results"]


def test_search_matches_ticker_company_question_and_findings(client) -> None:
    apple = start(client, "AAPL", "How did the stock do after earnings?")
    microsoft = start(client, "MSFT", "Is cloud growth holding up?")

    by_ticker = search(client, "msft")
    assert [r["run"]["run_id"] for r in by_ticker] == [microsoft]
    assert by_ticker[0]["matched"] == "ticker" and by_ticker[0]["snippet"] is None

    by_company = search(client, "apple")
    assert [r["run"]["run_id"] for r in by_company] == [apple]
    assert by_company[0]["matched"] == "company"

    by_question = search(client, "cloud growth")
    assert [r["run"]["run_id"] for r in by_question] == [microsoft]
    assert by_question[0]["matched"] == "question"

    # A word that only appears in the brief's findings comes back with a readable snippet.
    claims = client.get(f"/api/research/{apple}").json()["brief"]["claims"]
    word = next(w for c in claims for w in c["text"].lower().split() if len(w) > 6 and w.isalpha())
    by_finding = [r for r in search(client, word) if r["run"]["run_id"] == apple]
    assert by_finding and by_finding[0]["snippet"] and word in by_finding[0]["snippet"].lower()


def test_every_word_must_match_and_ranking_prefers_the_ticker(client) -> None:
    apple = start(client, "AAPL", "Apple services margin")
    start(client, "MSFT", "Compare with Apple's services")
    assert search(client, "apple zebra-not-present") == []
    results = search(client, "aapl")
    assert results[0]["run"]["run_id"] == apple and results[0]["matched"] == "ticker"


def test_search_input_is_validated_and_wildcards_are_literal(client) -> None:
    start(client, "AAPL", "How did the stock do?")
    # As LIKE wildcards these would match "AAPL" and "Apple"; searched literally they match nothing.
    assert search(client, "a_p") == [] and search(client, "a%l") == []
    assert search(client, "   ") == []
    assert client.get("/api/research/search", params={"q": ""}).status_code == 422
    assert client.get("/api/research/search", params={"q": "x" * 101}).status_code == 422
    assert client.get("/api/research/search", params={"q": "a", "limit": 21}).status_code == 422
    # The route is not mistaken for a run ID, and run detail still works.
    assert client.get("/api/research/search").status_code == 422


def test_snippet_is_cut_at_word_boundaries_around_the_match() -> None:
    text = ("Revenue rose in the quarter while costs stayed flat. " * 6) + "Gross margin reached a record level."
    piece = _snippet(text, "record")
    assert "record" in piece and piece.startswith("…") and len(piece) <= 152
    assert _snippet("Short text.", "text") == "Short text."
