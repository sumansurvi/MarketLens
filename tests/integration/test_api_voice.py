"""Phase 4 backend: voice sessions, room-scoped tokens and the worker's internal API.

LiveKit itself is not contacted: tokens are verified by decoding them with the configured
secret, and the worker API is exercised over HTTP exactly as the voice worker calls it.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from contextlib import contextmanager

import jwt
from app.main import create_app
from app.providers.llm.fixture_provider import FixtureLLMProvider
from marketlens_core.config import Settings
from marketlens_core.hashing import hash_secret
from marketlens_core.timeutil import iso_z
from marketlens_core.voice import ROOM_NAME_RE, internal_token
from starlette.testclient import TestClient

from tests.helpers import wait_for_run

API_KEY = "APItestkey0001"
API_SECRET = "test-livekit-secret-0123456789abcdef0123456789"
HEADERS = {"X-MarketLens-Client": "pytest"}


def voice_settings(tmp_path, **extra) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/marketlens.db",
        voice_mode="livekit",
        livekit_url="wss://marketlens-test.livekit.cloud",
        livekit_api_key=API_KEY,
        livekit_api_secret=API_SECRET,
        **extra,
    )


@contextmanager
def app_client(settings: Settings, **kwargs) -> Iterator[TestClient]:
    with TestClient(create_app(settings, **kwargs), headers=HEADERS) as client:
        yield client


def worker_headers(settings: Settings) -> dict[str, str]:
    return {**HEADERS, "Authorization": f"Bearer {internal_token(settings)}"}


def new_session(client: TestClient) -> dict:
    created = client.post("/api/voice/session")
    assert created.status_code == 201, created.text
    return created.json()


def test_voice_disabled_is_explicit_and_text_research_still_works(client) -> None:
    refused = client.post("/api/voice/session")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "VOICE_NOT_CONFIGURED"
    assert "Text research works without voice" in refused.json()["error"]["message"]
    assert client.get("/api/health").json()["voice_enabled"] is False
    assert client.post("/api/research", json={"symbol": "AAPL", "question": "Quick check."}).status_code == 202


def test_token_is_room_scoped_short_lived_and_dispatches_the_agent(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as client:
        body = new_session(client)
        assert ROOM_NAME_RE.fullmatch(body["room_name"]) and body["server_url"] == settings.livekit_url
        assert API_SECRET not in str(body) and body["agent_name"] == "marketlens-voice-agent"
        claims = jwt.decode(body["participant_token"], API_SECRET, algorithms=["HS256"])
        assert claims["iss"] == API_KEY and claims["sub"] == body["participant_identity"]
        assert claims["exp"] - claims["nbf"] == settings.voice_session_ttl_seconds
        video = claims["video"]
        assert video["roomJoin"] is True and video["room"] == body["room_name"]
        assert video["canPublishSources"] == ["microphone"]
        assert video["canPublish"] and video["canSubscribe"]
        for admin in ("roomAdmin", "roomCreate", "roomList", "roomRecord", "agent", "recorder"):
            assert not video.get(admin), admin
        assert claims["roomConfig"]["agents"] == [{"agentName": "marketlens-voice-agent"}]
        # The state route is bound to the browser session that created the room.
        assert client.get(f"/api/voice/session/{body['room_name']}").json()["ended"] is False
        other = TestClient(client.app, headers=HEADERS)  # another browser: own cookie, same running app
        assert other.get(f"/api/voice/session/{body['room_name']}").status_code == 404
        # The CSRF header is still required.
        assert client.post("/api/voice/session", headers={"X-MarketLens-Client": ""}).status_code == 403


def test_voice_sessions_are_rate_limited_per_browser_session(tmp_path) -> None:
    with app_client(voice_settings(tmp_path)) as client:
        for _ in range(6):
            new_session(client)
        limited = client.post("/api/voice/session")
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"


def test_worker_api_requires_the_derived_token(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as client:
        room = new_session(client)["room_name"]
        url = f"/api/internal/voice/rooms/{room}/status"
        assert client.get(url).json()["error"]["code"] == "REQUEST_NOT_ALLOWED"
        wrong = {"Authorization": "Bearer " + "0" * 64}
        assert client.get(url, headers=wrong).status_code == 403
        assert client.get(url, headers={"Authorization": f"Bearer {API_SECRET}"}).status_code == 403
        # Authenticated, but nothing has been started yet.
        assert client.get(url, headers=worker_headers(settings)).json()["error"]["code"] == "NOT_FOUND"
    with app_client(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/other.db")) as disabled:
        refused = disabled.get(url, headers=worker_headers(settings))
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "VOICE_NOT_CONFIGURED"
    assert "/api/internal" not in str(client.app.openapi()["paths"])


def test_voice_research_runs_the_shared_workflow_once_for_the_browser_session(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as client:
        room = new_session(client)["room_name"]
        worker = worker_headers(settings)
        payload = {"symbol": "aapl", "question": "How has Apple done recently?"}
        started = client.post(f"/api/internal/voice/rooms/{room}/research", json=payload, headers=worker)
        assert started.status_code == 202, started.text
        first = started.json()
        assert first["already_running"] is False and first["symbol"] == "AAPL" and first["window_is_default"]
        again = client.post(f"/api/internal/voice/rooms/{room}/research", json=payload, headers=worker).json()
        assert again["run_id"] == first["run_id"] and again["already_running"] is True

        detail = wait_for_run(client, first["run_id"])
        assert detail["run"]["status"] == "completed" and detail["run"]["origin"] == "voice"
        runs = client.app.state.container.runs
        assert runs.run_session_hash(first["run_id"]) == hash_secret(client.cookies.get("ml_session"))
        state = client.get(f"/api/voice/session/{room}").json()
        assert (state["active_run_id"], state["active_run_status"]) == (first["run_id"], "completed")

        status = client.get(f"/api/internal/voice/rooms/{room}/status", headers=worker).json()
        assert status["is_terminal"] and status["status_sentence"].endswith("is complete.")
        overview = client.get(f"/api/internal/voice/rooms/{room}/summary", headers=worker).json()
        assert overview["status"] == "completed" and overview["is_fixture"] is True
        assert overview["sentences"] and "synthetic demo data" in " ".join(overview["caveats"])
        assert "overview" not in overview["follow_up_topics"]
        spoken = " ".join(overview["sentences"])
        assert not re.search(r"\d{4}-\d{2}-\d{2}|ev_|us-gaap:", spoken)
        assert "September 30, 2026" in overview["window"]
        risks = client.get(f"/api/internal/voice/rooms/{room}/summary?focus=risks", headers=worker).json()
        assert any(s.startswith("Risk factor quoted") for s in risks["sentences"])
        brief_claims = " ".join(c["text"] for c in detail["brief"]["claims"])
        for number in re.findall(r"\$?\d[\d,.]*%?", spoken):
            assert number.rstrip(".,") in brief_claims  # nothing is added that the brief does not say


def test_worker_cannot_read_runs_of_another_session(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as alice:
        bob = TestClient(alice.app, headers=HEADERS)  # another browser: own cookie, same running app
        text_run = alice.post("/api/research", json={"symbol": "MSFT", "question": "Text research."}).json()
        room = new_session(bob)["room_name"]
        url = f"/api/internal/voice/rooms/{room}/status?run_id={text_run['run']['run_id']}"
        denied = bob.get(url, headers=worker_headers(settings))
        assert denied.status_code == 404 and denied.json()["error"]["code"] == "NOT_FOUND"
        wait_for_run(alice, text_run["run"]["run_id"])


def test_ended_unknown_and_malformed_rooms_are_rejected(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as client:
        worker = worker_headers(settings)
        room = new_session(client)["room_name"]
        assert client.post(f"/api/voice/session/{room}/end").json()["ended"] is True
        expired = client.post(
            f"/api/internal/voice/rooms/{room}/research",
            json={"symbol": "AAPL", "question": "Hi there"},
            headers=worker,
        )
        assert expired.status_code == 410 and expired.json()["error"]["code"] == "VOICE_SESSION_EXPIRED"
        unknown = "ml-voice-" + "A" * 24
        assert client.get(f"/api/internal/voice/rooms/{unknown}/status", headers=worker).status_code == 404
        assert client.get("/api/internal/voice/rooms/not-a-room/status", headers=worker).status_code == 404


def test_session_expiry_is_enforced_by_time(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as client:
        room = new_session(client)["room_name"]
        repo = client.app.state.container.voice.sessions
        record = repo.get(room)
        with repo.db.write() as conn:
            past = iso_z(record.created_at - dt.timedelta(seconds=1))
            conn.execute("UPDATE voice_sessions SET expires_at = ? WHERE room_name = ?", (past, room))
        late = client.get(f"/api/internal/voice/rooms/{room}/status", headers=worker_headers(settings))
        assert late.status_code == 410


def test_unknown_symbol_by_voice_gets_an_actionable_error(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    with app_client(settings) as client:
        room = new_session(client)["room_name"]
        refused = client.post(
            f"/api/internal/voice/rooms/{room}/research",
            json={"symbol": "ZZZZ", "question": "What about this one?"},
            headers=worker_headers(settings),
        )
        error = refused.json()["error"]
        assert refused.status_code == 422 and error["code"] == "UNKNOWN_SYMBOL"
        assert "AAPL" in error["message"]


def test_partial_report_is_reported_as_partial_for_every_focus(tmp_path) -> None:
    settings = voice_settings(tmp_path)
    llm = FixtureLLMProvider(failing_tasks={"news_specialist"})
    with app_client(settings, llm=llm) as client:
        room = new_session(client)["room_name"]
        worker = worker_headers(settings)
        run_id = client.post(
            f"/api/internal/voice/rooms/{room}/research",
            json={"symbol": "AAPL", "question": "Research Apple please.", "horizon": "3m"},
            headers=worker,
        ).json()["run_id"]
        wait_for_run(client, run_id)
        for focus in ("overview", "market", "financials", "risks", "news", "unknowns"):
            summary = client.get(f"/api/internal/voice/rooms/{room}/summary?focus={focus}", headers=worker).json()
            assert summary["status"] == "partial" and "is partial" in summary["status_sentence"], focus
            assert any(c.startswith("Partial because:") for c in summary["caveats"]), focus
            assert summary["sentences"], focus
        bad_focus = client.get(f"/api/internal/voice/rooms/{room}/summary?focus=trades", headers=worker)
        assert bad_focus.status_code == 422
