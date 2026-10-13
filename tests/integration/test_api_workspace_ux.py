"""Phase 5 workspace features: re-run, period comparison and privacy-preserving local analytics."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal

from app.main import create_app
from marketlens_core.config import Settings
from starlette.testclient import TestClient

from tests.helpers import load_fixture, wait_for_run

HEADERS = {"X-MarketLens-Client": "pytest"}


def start(client, **payload) -> dict:
    body = {"symbol": "AAPL", "question": "Research Apple over the window.", **payload}
    created = client.post("/api/research", json=body)
    assert created.status_code == 202, created.text
    return created.json()["run"]


# ----------------------------------------------------------------------------- re-run
def test_rerun_creates_a_linked_run_and_leaves_the_original_unchanged(client) -> None:
    original = wait_for_run(client, start(client)["run_id"])
    rerun = client.post(f"/api/research/{original['run']['run_id']}/rerun")
    assert rerun.status_code == 202, rerun.text
    new = rerun.json()["run"]
    assert new["run_id"] != original["run"]["run_id"]
    assert (new["origin"], new["parent_run_id"]) == ("rerun", original["run"]["run_id"])
    assert (new["symbol"], new["question"]) == (original["run"]["symbol"], original["run"]["question"])
    assert new["time_horizon"]["is_default"] is True
    finished = wait_for_run(client, new["run_id"])
    assert finished["run"]["status"] == "completed"
    assert client.get(f"/api/research/{original['run']['run_id']}").json() == original


def test_rerun_reuses_an_explicit_window_exactly(client) -> None:
    run = wait_for_run(
        client, start(client, time_horizon={"start_date": "2026-07-15", "end_date": "2026-09-15"})["run_id"]
    )
    window = run["run"]["time_horizon"]
    new = client.post(f"/api/research/{run['run']['run_id']}/rerun").json()["run"]
    assert (new["time_horizon"]["start_date"], new["time_horizon"]["end_date"]) == (
        window["start_date"],
        window["end_date"],
    )
    assert new["time_horizon"]["is_default"] is False
    wait_for_run(client, new["run_id"])


def test_a_run_in_progress_cannot_be_rerun_yet(client) -> None:
    run = start(client)
    refused = client.post(f"/api/research/{run['run_id']}/rerun")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "RUN_IN_PROGRESS"
    wait_for_run(client, run["run_id"])
    assert client.post("/api/research/run_0000000000000000/rerun").status_code == 404


# ----------------------------------------------------------------------------- period comparison
def _expected(start: str, end: str) -> tuple[str, str, Decimal]:
    series = load_fixture("alpha_vantage/TIME_SERIES_DAILY/AAPL.json")["Time Series (Daily)"]
    days = sorted(d for d in series if start <= d <= end)
    first, last = Decimal(series[days[0]]["4. close"]), Decimal(series[days[-1]]["4. close"])
    return days[0], days[-1], ((last - first) / first * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def test_compare_two_periods_reports_the_observation_dates_actually_used(client) -> None:
    periods = [
        {"start_date": "2026-07-04", "end_date": "2026-08-14"},
        {"start_date": "2026-08-14", "end_date": "2026-09-30"},
    ]
    response = client.post("/api/compare", json={"symbol": "aapl", "periods": periods})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "AAPL" and body["is_fixture"] is True and body["company_name"]
    changes = []
    for period, result in zip(periods, body["periods"], strict=True):
        calc = result["calculation"]
        first, last, pct = _expected(period["start_date"], period["end_date"])
        assert (calc["requested_start"], calc["requested_end"]) == (period["start_date"], period["end_date"])
        assert (calc["start_date"], calc["end_date"]) == (first, last)
        assert Decimal(str(calc["percentage_change"])) == pct
        assert calc["evidence_id"] in {e["evidence_id"] for e in body["evidence"]}
        changes.append(pct)
    # 2026-07-04 is a market holiday (a Saturday in this calendar), so the first trading day used differs.
    assert body["periods"][0]["calculation"]["start_date"] != "2026-07-04"
    expected_difference = (changes[1] - changes[0]).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    assert Decimal(str(body["difference_percentage_points"])) == expected_difference


def test_compare_discloses_missing_data_instead_of_estimating(client) -> None:
    periods = [
        {"start_date": "2025-01-02", "end_date": "2025-02-03"},
        {"start_date": "2026-08-14", "end_date": "2026-09-30"},
    ]
    body = client.post("/api/compare", json={"symbol": "AAPL", "periods": periods}).json()
    first = body["periods"][0]["calculation"]
    assert first["percentage_change"] is None and first["unavailable_reason"]
    assert body["difference_percentage_points"] is None and "no usable prices" in body["difference_note"]


def test_compare_validates_input(client) -> None:
    backwards = [
        {"start_date": "2026-09-01", "end_date": "2026-08-01"},
        {"start_date": "2026-08-14", "end_date": "2026-09-30"},
    ]
    response = client.post("/api/compare", json={"symbol": "AAPL", "periods": backwards})
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_INPUT"
    one = [{"start_date": "2026-08-14", "end_date": "2026-09-30"}]
    assert client.post("/api/compare", json={"symbol": "AAPL", "periods": one}).status_code == 422
    unknown = client.post("/api/compare", json={"symbol": "ZZZZ", "periods": one * 2})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "UNKNOWN_SYMBOL"


# ----------------------------------------------------------------------------- analytics
SECRETS = {
    "openai_api_key": "sk-analytics-secret-openai-0001",
    "alpha_vantage_api_key": "AVSECRETANALYTICS0001",
    "livekit_api_key": "APIanalyticsKey0001",
    "livekit_api_secret": "analytics-livekit-secret-0123456789abcdef",
}


def test_analytics_capture_product_events_without_personal_data_or_secrets(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/marketlens.db",
        voice_mode="livekit",
        livekit_url="wss://analytics-test.livekit.cloud",
        **SECRETS,
    )
    with TestClient(create_app(settings), headers=HEADERS) as client:
        run = wait_for_run(client, start(client)["run_id"])
        save = client.post("/api/actions", json={"action_type": "save_research_brief", "run_id": run["run"]["run_id"]})
        client.post(f"/api/actions/{save.json()['action']['action_id']}/confirm")
        add = client.post("/api/watchlist", json={"symbol": "NVDA"}).json()["action"]
        client.post(f"/api/actions/{add['action_id']}/confirm")
        remove = client.delete("/api/watchlist/NVDA").json()["action"]
        client.post(f"/api/actions/{remove['action_id']}/confirm")
        room = client.post("/api/voice/session").json()["room_name"]
        client.post(f"/api/voice/session/{room}/end")
        assert client.post("/api/analytics/events", json={"event": "voice_connection_succeeded"}).status_code == 204
        failed = {"event": "voice_connection_failed", "reason": "permission_denied"}
        assert client.post("/api/analytics/events", json=failed).status_code == 204
        periods = [
            {"start_date": "2026-07-01", "end_date": "2026-08-14"},
            {"start_date": "2026-08-14", "end_date": "2026-09-30"},
        ]
        client.post("/api/compare", json={"symbol": "AAPL", "periods": periods})

        # Browsers can report only allowlisted events with coded reasons.
        for bad in (
            {"event": "run_finished"},
            {"event": "voice_connection_failed", "reason": "my password is hunter2"},
            {"event": "voice_connection_failed", "reason": "other", "transcript": "hello"},
        ):
            assert client.post("/api/analytics/events", json=bad).status_code == 422, bad

        summary = client.get("/api/analytics/summary").json()
        cookie = client.cookies.get("ml_session")
        rows = client.app.state.container.analytics.events()

    assert summary["runs"]["started"] == 1 and summary["runs"]["by_status"] == {"completed": 1}
    assert summary["runs"]["completion_rate"] == 1.0 and summary["runs"]["median_duration_ms"] > 0
    assert summary["categories"]["market"] == {"available": 1} and summary["categories"]["news"] == {"available": 1}
    assert summary["workspace"] == {"briefs_saved": 1, "watchlist_changes": {"add": 1, "remove": 1}}
    assert summary["voice"]["sessions_created"] == 1 and summary["voice"]["connections_succeeded"] == 1
    assert summary["voice"]["failure_reasons"] == {"permission_denied": 1}
    assert summary["periods_compared"] == 1
    finished = next(r for r in rows if r["event"] == "run_finished")["properties"]
    assert finished["status"] == "completed" and finished["llm_calls"] == 5 and finished["withheld_claims"] == 0
    stored = json.dumps(rows)
    for forbidden in (*SECRETS.values(), cookie, "AAPL", "NVDA", "Research Apple", room):
        assert forbidden and forbidden not in stored, forbidden


def test_analytics_survive_restart_and_never_block_research(tmp_path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/marketlens.db")
    with TestClient(create_app(settings), headers=HEADERS) as client:
        wait_for_run(client, start(client)["run_id"])
    with TestClient(create_app(settings), headers=HEADERS) as client:
        assert client.get("/api/analytics/summary").json()["runs"]["finished"] == 1
        container = client.app.state.container

        def broken(*args, **kwargs):  # analytics storage failing must not break a run
            raise RuntimeError("disk full")

        container.analytics.record = broken
        assert wait_for_run(client, start(client)["run_id"])["run"]["status"] == "completed"
