"""Phase 6 security checks: secrets, CORS, request-body limits, allowlists, bounded retries and
public-exposure warnings (architecture Phase 6)."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from app.agents.runtime import AgentRuntime
from app.main import create_app
from app.mcp import client_gateway
from app.mcp.gateway import ALLOWED_TOOLS
from marketlens_core.config import Settings
from marketlens_core.policy import PROHIBITED_TOOL_FRAGMENTS
from marketlens_core.providers import live_transport
from starlette.testclient import TestClient

from tests.helpers import wait_for_run

HEADERS = {"X-MarketLens-Client": "pytest"}
SECRETS = {
    "openai_api_key": "sk-security-check-openai-secret-0001",
    "alpha_vantage_api_key": "AVSECURITYCHECKSECRET0001",
    "livekit_api_key": "APIsecuritycheck0001",
    "livekit_api_secret": "security-check-livekit-secret-0123456789abcdef",
}


def _dump_database(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return "\n".join(str(row) for table in tables for row in conn.execute(f"SELECT * FROM {table}"))  # noqa: S608
    finally:
        conn.close()


def test_secrets_never_reach_logs_responses_storage_or_mcp_logs(tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/marketlens.db",
        voice_mode="livekit",
        livekit_url="wss://security-check.livekit.invalid",
        log_level="DEBUG",
        **SECRETS,
    )
    responses: list[str] = []
    with TestClient(create_app(settings), headers=HEADERS) as client:
        for path in ("/api/health", "/api/data-status"):
            responses.append(client.get(path).text)
        run = client.post("/api/research", json={"symbol": "AAPL", "question": "Research Apple."}).json()["run"]
        responses.append(json.dumps(wait_for_run(client, run["run_id"])))
        proposal = client.post("/api/actions", json={"action_type": "save_research_brief", "run_id": run["run_id"]})
        responses.append(proposal.text)
        responses.append(client.post(f"/api/actions/{proposal.json()['action']['action_id']}/confirm").text)
        responses.append(client.post("/api/voice/session").text)
        responses.append(client.get("/api/analytics/summary").text)
        responses.append(client.get("/api/openapi.json").text)
    logs = caplog.text + "".join(p.read_text() for p in (tmp_path / "runtime" / "logs").glob("*.log"))
    stored = _dump_database(tmp_path / "marketlens.db")
    for name, secret in SECRETS.items():
        if name == "livekit_api_key":  # a public identifier, not a secret (it is the JWT issuer)
            continue
        assert secret not in logs, f"{name} found in logs"
        assert all(secret not in r for r in responses), f"{name} found in an API response"
        assert secret not in stored, f"{name} found in the database"


def test_cors_allows_only_the_configured_origin(tmp_path) -> None:
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path}/m.db", frontend_origin="http://localhost:3000"
    )
    with TestClient(create_app(settings)) as client:
        preflight = {
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-marketlens-client",
        }
        allowed = client.options("/api/research", headers={"Origin": "http://localhost:3000", **preflight})
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert allowed.headers.get("access-control-allow-credentials") == "true"
        denied = client.options("/api/research", headers={"Origin": "https://evil.example", **preflight})
        assert "access-control-allow-origin" not in denied.headers
        simple = client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in simple.headers


def test_request_bodies_are_bounded_even_without_a_declared_length(tmp_path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/m.db", max_request_body_bytes=2048)
    with TestClient(create_app(settings), headers=HEADERS) as client:
        declared = client.post(
            "/api/research", content=b"{" + b" " * 4096 + b"}", headers={"Content-Type": "application/json"}
        )
        assert declared.status_code == 413 and declared.json()["error"]["code"] == "REQUEST_TOO_LARGE"

        def chunks():
            yield b'{"symbol": "AAPL", "question": "'
            for _ in range(8):
                yield b"x" * 512
            yield b'"}'

        streamed = client.post("/api/research", content=chunks(), headers={"Content-Type": "application/json"})
        assert streamed.status_code == 413


def test_tool_allowlists_contain_no_trading_or_unexpected_tools() -> None:
    names = [tool for tools in ALLOWED_TOOLS.values() for tool in tools]
    assert not [n for n in names if any(fragment in n for fragment in PROHIBITED_TOOL_FRAGMENTS)]
    assert set(ALLOWED_TOOLS) == {"market_data", "research_sources", "research_workspace"}


def test_retries_timeouts_and_budgets_are_bounded() -> None:
    fields = Settings.model_fields

    def upper(name: str) -> float:
        return next(m.le for m in fields[name].metadata if hasattr(m, "le"))

    # Every retry/timeout/budget setting has a hard upper bound in configuration.
    bounds = {
        "market_data_max_retries": 5,
        "max_llm_calls_per_run": 20,
        "openai_timeout_seconds": 300,
        "mcp_tool_call_timeout_seconds": 120,
        "run_timeout_seconds": 900,
        "max_provider_calls": 60,
        "max_parallel_specialists": 6,
        "sec_max_concurrent_requests": 8,
        "max_request_body_bytes": 1_048_576,
    }
    assert {name: upper(name) for name in bounds} == bounds
    # Backoff and restart delays are capped; the agent runtime retries at most once per task.
    assert live_transport.BACKOFF_CAP_SECONDS <= 4.0
    assert client_gateway.MAX_RESTART_DELAY_SECONDS <= 8.0
    assert AgentRuntime.run.__kwdefaults__["retries"] == 1


def test_public_exposure_is_warned_about(tmp_path, caplog) -> None:
    exposed = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/m.db", backend_host="0.0.0.0")  # noqa: S104
    assert exposed.public_exposure_warnings()
    assert not Settings(_env_file=None).public_exposure_warnings()
    caplog.set_level(logging.WARNING)
    with TestClient(create_app(exposed), headers=HEADERS):
        pass
    assert "not a loopback address" in caplog.text


def test_deterministic_runs_make_no_external_network_connections(tmp_path, monkeypatch) -> None:
    """Fixture mode never reaches a provider, LLM or LiveKit: any non-loopback connection fails the run."""
    import ipaddress
    import socket

    attempts: list[object] = []
    real_connect = socket.socket.connect

    def guarded(self, address):  # noqa: ANN001
        host = address[0] if isinstance(address, tuple) else None
        if host is not None:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            if not loopback:
                attempts.append(address)
                raise OSError("network access is blocked in the deterministic suite")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/m.db")
    assert (settings.data_mode, settings.llm_mode, settings.voice_mode) == ("fixtures", "fixture", "disabled")
    assert not (settings.openai_api_key or settings.alpha_vantage_api_key or settings.livekit_api_secret)
    with TestClient(create_app(settings), headers=HEADERS) as client:
        run = client.post("/api/research", json={"symbol": "NVDA", "question": "Research Nvidia."}).json()["run"]
        detail = wait_for_run(client, run["run_id"])
    assert detail["run"]["status"] == "completed" and attempts == []
    assert {e["source_mode"] for e in detail["evidence"]} == {"fixture"}
