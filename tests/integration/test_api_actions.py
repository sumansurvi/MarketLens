"""HTTP confirmation flow: propose → preview → confirm, with negative cases and persistence."""

from __future__ import annotations

from app.main import create_app
from marketlens_core.config import Settings
from marketlens_core.db import open_database
from starlette.testclient import TestClient

from tests.helpers import wait_for_run

HEADERS = {"X-MarketLens-Client": "pytest"}


def completed_run(client) -> str:
    run_id = client.post("/api/research", json={"symbol": "AAPL", "question": "Brief to save"}).json()["run"]["run_id"]
    assert wait_for_run(client, run_id)["run"]["status"] == "completed"
    return run_id


def count(settings: Settings, table: str) -> int:
    db = open_database(settings)  # SQLite or Postgres, whichever the test runs on
    try:
        with db.read() as conn:
            return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 - fixed table names
    finally:
        db.close()


def test_save_brief_requires_explicit_confirmation(client, settings) -> None:
    run_id = completed_run(client)
    proposal = client.post(
        "/api/actions", json={"action_type": "save_research_brief", "run_id": run_id, "title": "AAPL research"}
    )
    assert proposal.status_code == 202
    action = proposal.json()["action"]
    assert action["status"] == "pending" and action["summary"]["data_mode"] == "fixture"
    assert count(settings, "saved_briefs") == 0  # proposing writes nothing
    preview = client.get(f"/api/actions/{action['action_id']}").json()["action"]
    assert preview["title"] == "AAPL research" and preview["summary"]["destination"] == "saved briefs"
    confirmed = client.post(f"/api/actions/{action['action_id']}/confirm")
    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["action"]["status"] == "consumed" and result["result"]["is_fixture"] is True
    assert count(settings, "saved_briefs") == 1
    replay = client.post(f"/api/actions/{action['action_id']}/confirm")
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "ACTION_ALREADY_CONSUMED"
    assert count(settings, "saved_briefs") == 1
    saved = client.get(f"/api/briefs/{result['result']['brief_id']}").json()["saved"]
    assert saved["brief"]["run_id"] == run_id and saved["evidence"]


def test_other_sessions_cannot_see_or_confirm(client, settings) -> None:
    proposal = client.post("/api/watchlist", json={"symbol": "MSFT"}).json()["action"]
    with TestClient(client.app, headers=HEADERS) as stranger:  # fresh cookie jar = different session
        assert stranger.get(f"/api/actions/{proposal['action_id']}").status_code == 404
        attempt = stranger.post(f"/api/actions/{proposal['action_id']}/confirm")
        assert attempt.status_code == 404
    assert count(settings, "watchlist_items") == 0


def test_expired_and_cancelled_actions_cannot_write(client, settings) -> None:
    expired = client.post("/api/watchlist", json={"symbol": "NVDA"}).json()["action"]
    db = open_database(settings)
    with db.write() as conn:
        conn.execute(
            "UPDATE pending_actions SET expires_at = '2000-01-01T00:00:00Z' WHERE action_id = ?",
            (expired["action_id"],),
        )
    db.close()
    response = client.post(f"/api/actions/{expired['action_id']}/confirm")
    assert response.status_code == 410 and response.json()["error"]["code"] == "CONFIRMATION_EXPIRED"
    cancelled = client.post("/api/watchlist", json={"symbol": "NVDA"}).json()["action"]
    assert client.post(f"/api/actions/{cancelled['action_id']}/cancel").json()["action"]["status"] == "cancelled"
    response = client.post(f"/api/actions/{cancelled['action_id']}/confirm")
    assert response.status_code == 403 and response.json()["error"]["code"] == "INVALID_CONFIRMATION_GRANT"
    assert count(settings, "watchlist_items") == 0


def test_client_supplied_confirmation_flags_and_missing_csrf_header_are_rejected(client, settings) -> None:
    flagged = client.post("/api/watchlist", json={"symbol": "AAPL", "confirmed": True})
    assert flagged.status_code == 422
    flagged_action = client.post(
        "/api/actions", json={"action_type": "add_watchlist_symbol", "symbol": "AAPL", "confirmed": True, "grant": "x"}
    )
    assert flagged_action.status_code == 422
    no_header = client.post("/api/watchlist", json={"symbol": "AAPL"}, headers={"X-MarketLens-Client": ""})
    assert no_header.status_code == 403 and no_header.json()["error"]["code"] == "REQUEST_NOT_ALLOWED"
    assert count(settings, "watchlist_items") == 0 and count(settings, "pending_actions") == 0


def test_watchlist_add_remove_validation_and_idempotency(client) -> None:
    assert client.post("/api/watchlist", json={"symbol": "TSLA"}).json()["error"]["code"] == "UNKNOWN_SYMBOL"
    assert client.post("/api/watchlist", json={"symbol": "AA PL"}).json()["error"]["code"] == "INVALID_SYMBOL"
    assert client.delete("/api/watchlist/NVDA").json()["error"]["code"] == "NOT_FOUND"
    for _ in range(2):
        action = client.post("/api/watchlist", json={"symbol": "nvda"}).json()["action"]
        client.post(f"/api/actions/{action['action_id']}/confirm")
    items = client.get("/api/watchlist").json()["items"]
    assert [i["symbol"] for i in items] == ["NVDA"] and items[0]["display_name"] == "NVIDIA Corporation"
    removal = client.delete("/api/watchlist/NVDA").json()["action"]
    assert client.post(f"/api/actions/{removal['action_id']}/confirm").json()["result"]["removed"] is True
    assert client.get("/api/watchlist").json()["items"] == []


def test_saving_requires_an_existing_brief(client) -> None:
    missing = client.post(
        "/api/actions", json={"action_type": "save_research_brief", "run_id": "run_0000000000000000", "title": "Nope"}
    )
    assert missing.status_code == 404


def test_workspace_state_survives_backend_restart(tmp_path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/restart.db")
    with TestClient(create_app(settings), headers=HEADERS) as first:
        action = first.post("/api/watchlist", json={"symbol": "AAPL"}).json()["action"]
        first.post(f"/api/actions/{action['action_id']}/confirm")
        run_id = completed_run(first)
        save = first.post(
            "/api/actions", json={"action_type": "save_research_brief", "run_id": run_id, "title": "Kept"}
        ).json()["action"]
        brief_id = first.post(f"/api/actions/{save['action_id']}/confirm").json()["result"]["brief_id"]
    with TestClient(create_app(settings), headers=HEADERS) as second:
        assert [i["symbol"] for i in second.get("/api/watchlist").json()["items"]] == ["AAPL"]
        saved = second.get(f"/api/briefs/{brief_id}").json()["saved"]
        assert saved["title"] == "Kept" and saved["is_fixture"] is True
        assert saved["brief"]["as_of"]["fixture_dataset_version"] == "fx-2026-09-30-v1"
