"""Health, data status, error model, session cookie, CORS, body limits and secret hygiene."""

from __future__ import annotations

import json

from app.main import create_app
from marketlens_core.config import Settings
from starlette.testclient import TestClient


def test_health_reports_mode_capabilities_and_policy_without_secrets(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/h.db",
        alpha_vantage_api_key="AV-SECRET-VALUE-123",
        openai_api_key="sk-SECRET-VALUE",
        livekit_api_secret="LK-SECRET-VALUE",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
        body = response.json()
        raw = response.text
    assert response.status_code == 200
    assert body["data_mode"] == "fixtures" and body["demo_notice"].startswith("DEMO FIXTURES")
    assert body["policy"]["trading_supported"] is False and body["policy"]["research_only"] is True
    assert "Research only" in body["research_only_notice"]
    assert body["voice_enabled"] is False
    for secret in ("AV-SECRET-VALUE-123", "sk-SECRET-VALUE", "LK-SECRET-VALUE"):
        assert secret not in raw


def test_data_status_lists_both_read_servers(client) -> None:
    body = client.get("/api/data-status").json()
    assert body["is_fixture"] is True and body["fixture_dataset_version"] == "fx-2026-09-30-v1"
    assert {s["server"] for s in body["servers"]} == {"market_data", "research_sources"}
    assert body["notices"] and "DEMO FIXTURES" in body["notices"][0]


def test_session_cookie_is_http_only_and_stable(client) -> None:
    first = client.get("/api/health")
    cookie = first.headers.get("set-cookie", "")
    assert "ml_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    second = client.get("/api/health")
    assert "set-cookie" not in second.headers  # existing session reused
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert second.headers["cache-control"] == "no-store"


def test_body_size_limit_and_unknown_routes(client) -> None:
    big = client.post(
        "/api/research", content=b"{" + b" " * 70_000 + b"}", headers={"content-type": "application/json"}
    )
    assert big.status_code == 413 and big.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    missing = client.get("/api/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_cors_allows_only_configured_origin(client) -> None:
    allowed = client.options(
        "/api/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    denied = client.options(
        "/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in denied.headers


def test_workspace_shells_are_empty_and_validate_ids(client) -> None:
    assert client.get("/api/watchlist").json() == {"items": []}
    assert client.get("/api/briefs").json() == {"briefs": []}
    assert client.get("/api/briefs/brief_0000000000000000").status_code == 404
    assert client.get("/api/briefs/../../etc").status_code == 404


def test_openapi_documents_error_model(client) -> None:
    spec = client.get("/api/openapi.json").json()
    assert "ErrorResponse" in json.dumps(spec["components"]["schemas"])
    assert "/api/research" in spec["paths"]
