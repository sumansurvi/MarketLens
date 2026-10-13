"""Phase 1 vertical slice: fixture research run end to end through the HTTP API."""

from __future__ import annotations

import datetime as dt
import json
from decimal import ROUND_HALF_EVEN, Decimal

from tests.helpers import FIXTURE_ROOT, load_fixture, wait_for_run

QUESTION = (
    "Research AAPL over the last 3 months; explain notable movement; review recent news and latest "
    "available filings; list risks and unknowns."
)


def _close(symbol: str, day: str) -> Decimal:
    series = load_fixture(f"alpha_vantage/TIME_SERIES_DAILY/{symbol}.json")["Time Series (Daily)"]
    return Decimal(series[day]["4. close"])


def test_happy_path_fixture_report_is_complete_labeled_and_sourced(client) -> None:
    created = client.post("/api/research", json={"symbol": "aapl", "question": QUESTION})
    assert created.status_code == 202
    run = created.json()["run"]
    assert run["symbol"] == "AAPL" and run["status"] == "queued" and run["data_mode"] == "fixture"
    assert run["time_horizon"]["is_default"] is True
    assert (run["time_horizon"]["start_date"], run["time_horizon"]["end_date"]) == ("2026-06-30", "2026-09-30")

    detail = wait_for_run(client, run["run_id"])
    assert detail["run"]["status"] == "completed", detail["run"]["errors"]
    brief = detail["brief"]
    assert brief["is_fixture"] is True and brief["data_mode"] == "fixture"
    assert brief["fixture_disclosure"] and "synthetic" in brief["fixture_disclosure"].lower()
    assert "not a recommendation" in brief["disclaimer"].lower() and "does not execute trades" in brief["disclaimer"]
    assert brief["as_of"]["fixture_dataset_version"] == "fx-2026-09-30-v1"

    # Ordered audit trail with every stage and MCP-contract tool names.
    keys = [s["step_key"] for s in detail["steps"]]
    assert keys == [
        "plan",
        "collect_evidence",
        "calculations",
        "market_specialist",
        "news_specialist",
        "fundamentals_specialist",
        "supervisor_synthesis",
        "evidence_review",
        "finalize",
    ]
    agents = {s["step_key"]: s["agent"] for s in detail["steps"]}
    assert agents["market_specialist"] == "market_data_specialist"
    assert agents["evidence_review"] == "evidence_risk_reviewer"
    tools = {t["tool_name"] for t in detail["tool_calls"]}
    assert {
        "get_quote",
        "get_historical_prices",
        "calculate_price_change",
        "get_sec_company_facts",
        "search_company_filings",
        "get_filing_excerpt",
        "search_company_news",
        "resolve_symbol",
    } <= tools
    assert all(t["status"] == "ok" for t in detail["tool_calls"])

    # Every claim cites evidence that exists in the run; every evidence item is a labeled fixture.
    evidence = {e["evidence_id"]: e for e in detail["evidence"]}
    assert all(e["is_fixture"] and e["source_mode"] == "fixture" for e in evidence.values())
    for claim in brief["claims"]:
        assert claim["evidence_ids"] and all(i in evidence for i in claim["evidence_ids"]), claim
        assert claim["validation_status"] in ("supported", "needs_qualification")
    assert brief["withheld_claims"] == []
    assert {c["claim_type"] for c in brief["claims"]} >= {"source_fact", "calculation", "interpretation"}
    sections = {c["section"] for c in brief["claims"]}
    assert {
        "executive_summary",
        "market_movement",
        "fundamentals",
        "filings",
        "risks",
        "positive_evidence",
        "negative_evidence",
        "interpretation",
    } <= sections

    # Numbers match the fixture files (independent recomputation from raw fixture data).
    start, end = _close("AAPL", "2026-06-30"), _close("AAPL", "2026-09-30")
    pct = ((end - start) / start * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    change = next(m for m in brief["key_metrics"] if m["label"] == "Price change (close)")
    assert change["display_value"] == f"+{pct}%"
    market_claim = next(c for c in brief["claims"] if c["section"] == "market_movement")
    assert f"${end:.2f}" in market_claim["text"] and f"${start:.2f}" in market_claim["text"]
    facts = load_fixture("sec/companyfacts/CIK0000320193.json")["facts"]["us-gaap"]
    revenue_q = next(
        f
        for f in facts["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
        if f["end"] == "2026-06-27" and f["start"] == "2026-03-29"
    )
    billions = (Decimal(revenue_q["val"]) / Decimal(10**9)).quantize(Decimal("0.01"))
    assert any(f"${billions} billion" in c["text"] for c in brief["claims"])

    # Sources keep fixture links/evidence identifiers from the fixture files.
    news = [e for e in evidence.values() if e["kind"] == "news"]
    fixture_urls = {item["url"] for item in load_fixture("alpha_vantage/NEWS_SENTIMENT/AAPL.json")["feed"]}
    assert news and {e["source_url"] for e in news} <= fixture_urls
    assert all(e["published_at_tz_known"] is False for e in news)

    # Prompt-injection article is flagged, excluded from events, and disclosed.
    flagged = [e for e in news if "suspected_prompt_injection" in e["flags"]]
    assert len(flagged) == 1
    assert flagged[0]["evidence_id"] not in {ev["evidence_id"] for ev in brief["recent_events"]}
    assert any("instruction-like" in n for n in brief["source_quality_notes"])

    # Unknowns are explicit, and nothing claims consensus estimates or targets.
    gap_text = " ".join(g["description"] for g in brief["unknowns_and_gaps"])
    assert "consensus" in gap_text and "causes of individual price moves" in gap_text
    all_text = json.dumps(brief["claims"]).lower()
    for forbidden in ("price target", "you should buy", "strong buy", "guaranteed"):
        assert forbidden not in all_text


def test_capability_gap_is_disclosed_not_filled(client) -> None:
    run = client.post("/api/research", json={"symbol": "MSFT", "question": "Review price movement, news and risks."})
    detail = wait_for_run(client, run.json()["run"]["run_id"])
    brief = detail["brief"]
    news_status = next(s for s in detail["source_status"] if s["category"] == "news")
    assert news_status["status"] == "unavailable" and news_status["reason_code"] == "CAPABILITY_UNAVAILABLE"
    assert not [e for e in detail["evidence"] if e["kind"] == "news"]
    assert brief["recent_events"] == []
    assert any(g["category"] == "news" and g["severity"] == "material" for g in brief["unknowns_and_gaps"])
    assert detail["run"]["status"] == "completed"  # documented unavailability (decision D-09)


def test_issuer_specific_revenue_tag_is_used_without_imputation(client) -> None:
    run = client.post("/api/research", json={"symbol": "NVDA", "question": "What changed recently?"})
    detail = wait_for_run(client, run.json()["run"]["run_id"])
    revenue_claim = next(c for c in detail["brief"]["claims"] if c["text"].startswith("Revenue for the quarter"))
    cited = {e["evidence_id"]: e for e in detail["evidence"] if e["evidence_id"] in revenue_claim["evidence_ids"]}
    assert any((e.get("payload") or {}).get("concept") == "Revenues" for e in cited.values())
    assert "us-gaap" not in revenue_claim["text"]  # internal tags never reach the text (D-86)


def test_explicit_horizon_and_coverage_warning(client) -> None:
    payload = {"symbol": "AAPL", "question": "Six month view please.", "time_horizon": {"preset": "6m"}}
    detail = wait_for_run(client, client.post("/api/research", json=payload).json()["run"]["run_id"])
    assert detail["run"]["time_horizon"]["is_default"] is False
    gaps = " ".join(g["description"] for g in detail["brief"]["unknowns_and_gaps"])
    assert "Provider data begins 2026-05-08" in gaps


def test_invalid_and_unknown_symbols_return_actionable_4xx(client) -> None:
    cases = [
        ({"symbol": "TSLA", "question": "Research TSLA"}, 422, "UNKNOWN_SYMBOL"),
        ({"symbol": "AAPL'; DROP TABLE research_runs;--", "question": "x x x"}, 422, "INVALID_INPUT"),
        ({"symbol": "AA PL", "question": "Research please"}, 422, "INVALID_SYMBOL"),
        ({"symbol": "", "question": "Research please"}, 422, "INVALID_INPUT"),
        ({"symbol": "AAPL"}, 422, "INVALID_INPUT"),
        ({"symbol": "AAPL", "question": "x" * 1001}, 422, "INVALID_INPUT"),
        ({"symbol": "AAPL", "question": "ok then", "requested_mode": "live"}, 409, "MODE_MISMATCH"),
        (
            {
                "symbol": "AAPL",
                "question": "ok then",
                "time_horizon": {"start_date": "2026-09-01", "end_date": "2026-01-01"},
            },
            422,
            "INVALID_INPUT",
        ),
        ({"symbol": "AAPL", "question": "ok then", "time_horizon": {"preset": "5y"}}, 422, "INVALID_INPUT"),
        ({"symbol": "AAPL", "question": "ok then", "unexpected": True}, 422, "INVALID_INPUT"),
    ]
    for payload, status, code in cases:
        response = client.post("/api/research", json=payload)
        body = response.json()
        assert response.status_code == status, (payload, body)
        assert body["error"]["code"] == code, (payload, body)
        assert body["error"]["message"] and body["error"]["request_id"]
        assert "Traceback" not in json.dumps(body)


def test_double_submit_with_client_request_id_creates_one_run(client) -> None:
    payload = {"symbol": "AAPL", "question": "Same request twice", "client_request_id": "dup-check-0001"}
    first = client.post("/api/research", json=payload).json()["run"]["run_id"]
    second = client.post("/api/research", json=payload).json()["run"]["run_id"]
    assert first == second
    wait_for_run(client, first)


def test_cancel_and_unknown_run(client) -> None:
    assert client.get("/api/research/run_0000000000000000").status_code == 404
    assert client.get("/api/research/not-a-run-id").json()["error"]["code"] == "NOT_FOUND"
    run_id = client.post("/api/research", json={"symbol": "AAPL", "question": "Cancel me"}).json()["run"]["run_id"]
    response = client.post(f"/api/research/{run_id}/cancel")
    if response.status_code == 200:
        assert response.json()["status"] in ("cancelled", "completed")
    else:  # finished before the cancel arrived: terminal runs are not cancellable
        assert response.json()["error"]["code"] == "RUN_NOT_CANCELLABLE"
    final = wait_for_run(client, run_id)
    assert final["run"]["status"] in ("cancelled", "completed")
    assert client.post(f"/api/research/{run_id}/cancel").json()["error"]["code"] == "RUN_NOT_CANCELLABLE"


def test_run_list_and_fixture_report_dates_are_utc(client) -> None:
    run_id = client.post("/api/research", json={"symbol": "NVDA", "question": "List check"}).json()["run"]["run_id"]
    wait_for_run(client, run_id)
    runs = client.get("/api/research?limit=5").json()["runs"]
    assert runs and runs[0]["created_at"].endswith("Z")
    assert dt.datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00")).tzinfo is not None
    assert (FIXTURE_ROOT / "manifest.json").exists()
