"""Hosted deployment hardening (D-94): Secure session cookie in production and the proxy shared secret."""

from __future__ import annotations

from app.main import create_app
from marketlens_core.config import Settings, validate_settings
from starlette.testclient import TestClient

SECRET = "s" * 40
CLIENT = {"x-marketlens-client": "web"}


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/hosted.db", **overrides)


def test_session_cookie_is_secure_only_in_production(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        local = client.get("/api/health").headers["set-cookie"]
    with TestClient(create_app(_settings(tmp_path, app_env="production", proxy_shared_secret=SECRET))) as client:
        hosted = client.get("/api/health").headers["set-cookie"]
    assert "Secure" not in local
    assert "Secure" in hosted and "HttpOnly" in hosted and "SameSite=Lax" in hosted


def test_proxy_secret_gates_every_api_route_except_health_and_voice_worker(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path, proxy_shared_secret=SECRET))) as client:
        assert client.get("/api/health").status_code == 200  # the host's health check
        missing = client.get("/api/data-status")
        wrong = client.get("/api/data-status", headers={"x-marketlens-proxy-secret": "nope"})
        post = client.post("/api/research", json={"symbol": "AAPL"}, headers=CLIENT)
        allowed = client.get("/api/data-status", headers={"x-marketlens-proxy-secret": SECRET})
        # The voice worker's internal API keeps its own bearer token instead of the proxy secret.
        worker = client.get("/api/internal/voice/rooms/room-x/status")
    for response in (missing, wrong, post):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "REQUEST_NOT_ALLOWED"
        assert SECRET not in response.text
    assert allowed.status_code == 200
    assert "only accepts requests through the MarketLens web app" not in worker.text


def test_without_a_proxy_secret_requests_pass_as_before(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/data-status").status_code == 200


def test_short_proxy_secret_is_rejected_and_production_without_one_warns(tmp_path) -> None:
    problems = validate_settings(_settings(tmp_path, proxy_shared_secret="short"))
    assert any("PROXY_SHARED_SECRET" in p for p in problems)
    assert not validate_settings(_settings(tmp_path, proxy_shared_secret=SECRET))
    warnings = _settings(tmp_path, app_env="production").public_exposure_warnings()
    assert any("PROXY_SHARED_SECRET" in w for w in warnings)
    assert not _settings(tmp_path, app_env="production", proxy_shared_secret=SECRET).public_exposure_warnings()
