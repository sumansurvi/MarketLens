"""Running on Vercel (D-101): the hosted profile is enforced, and the backend never fails silently.

The first live deploy showed every MCP server stuck at ``ready: false`` with no error: project
variables copied from ``.env.example`` overrode the Vercel defaults, so the servers tried to create
a log folder on the read-only filesystem and their supervisors died without a trace.
"""

from __future__ import annotations

import asyncio

import app.main as main
import pytest
from app.main import create_app, hosted_app_from_settings
from app.mcp.client_gateway import MCPClientGateway, ServerConnection
from marketlens_core.config import ConfigError, Settings, load_settings, mcp_log_directory
from marketlens_core.errors import ErrorCode, MarketLensError
from starlette.testclient import TestClient

POSTGRES = "postgresql://user:pw@localhost:5432/marketlens"
SUPABASE_DIRECT = "postgresql://postgres.ref:hunter2@db.abcdefghij.supabase.co:5432/postgres"
SERVERS = ("market_data", "research_sources", "research_workspace")


def _on_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)


def _not_on_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)


class HangingGateway:
    """A gateway whose servers never answer."""

    transport = "mcp_stdio"

    async def call(self, server_id, tool_name, arguments, *, run_id=None, seq=None):  # noqa: ANN001, ANN201
        await asyncio.sleep(3600)

    def status(self) -> dict[str, dict[str, object]]:
        return {s: {"ready": False, "tools": [], "restarts": 0, "last_error": None} for s in SERVERS}


# ----------------------------------------------------------------- the hosted profile
def test_vercel_profile_wins_over_variables_copied_from_env_example(monkeypatch) -> None:
    _on_vercel(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RUN_EXECUTION", "inline")
    monkeypatch.setenv("MCP_LOG_DIR", "")
    monkeypatch.setenv("DATABASE_URL", POSTGRES)
    settings = load_settings()
    assert (settings.app_env, settings.run_execution, settings.mcp_log_dir) == ("production", "queue", "-")
    assert mcp_log_directory(settings) is None and settings.session_cookie_secure
    warnings = settings.deployment_warnings()
    assert any(w.startswith("APP_ENV=development is set in the project's environment variables") for w in warnings)
    assert any(w.startswith("RUN_EXECUTION=inline is set") for w in warnings)
    assert not any("MCP_LOG_DIR" in w for w in warnings)  # an empty value counts as unset
    # Explicit overrides (tests, scripts) still win.
    assert load_settings(run_execution="inline").run_execution == "inline"


def test_vercel_dev_keeps_local_control(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", "development")
    settings = load_settings()
    assert (settings.app_env, settings.run_execution, settings.mcp_log_dir) == ("development", "inline", None)


def test_postgres_url_is_used_when_database_url_is_missing(monkeypatch) -> None:
    _on_vercel(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgres://a:b@pooler.example:6543/postgres")
    assert load_settings().database_url == "postgres://a:b@pooler.example:6543/postgres"
    monkeypatch.setenv("DATABASE_URL", POSTGRES)
    assert load_settings().database_url == POSTGRES


def test_vercel_without_postgres_names_the_fix(monkeypatch) -> None:
    _on_vercel(monkeypatch)
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert len(err.value.problems) == 1
    assert err.value.problems[0].startswith("DATABASE_URL must be a Postgres URL on Vercel")
    assert "Transaction pooler" in err.value.problems[0]


def test_empty_variables_mean_the_default(monkeypatch) -> None:
    _not_on_vercel(monkeypatch)
    for name in ("APP_ENV", "LOG_LEVEL", "VOICE_LANGUAGE", "RESEARCH_QUEUE_TOPIC", "MCP_LOG_DIR"):
        monkeypatch.setenv(name, "")
    settings = load_settings()
    assert (settings.app_env, settings.log_level, settings.voice_language, settings.mcp_log_dir) == (
        "development",
        "INFO",
        "en",
        None,
    )


def test_invalid_values_are_reported_by_name_never_by_value(monkeypatch) -> None:
    _not_on_vercel(monkeypatch)
    monkeypatch.setenv("APP_MODE", "sk-looks-like-a-key")
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert err.value.problems == ["APP_MODE: Input should be 'demo' or 'live'"]
    assert "sk-looks-like-a-key" not in str(err.value)


# ----------------------------------------------------------------- answering instead of crashing
def test_on_vercel_an_invalid_configuration_answers_503_with_the_problem(monkeypatch) -> None:
    _on_vercel(monkeypatch)  # and no DATABASE_URL
    with TestClient(hosted_app_from_settings()) as client:
        health = client.get("/api/health")
        research = client.post("/api/research", json={"symbol": "AAPL", "question": "How is it doing?"})
        page = client.get("/")
    for response in (health, research):
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "BACKEND_MISCONFIGURED" and error["retryable"] is False
        assert error["message"].startswith("Setup problem: DATABASE_URL must be a Postgres URL on Vercel")
        assert len(error["details"]["problems"]) == 1
    assert page.status_code == 404


def test_on_vercel_app_main_also_answers_instead_of_exiting(monkeypatch) -> None:
    """If Vercel loads app.main:app directly, the stand-in still answers."""
    _on_vercel(monkeypatch)
    with TestClient(main._app_from_environment()) as client:
        assert client.get("/api/health").json()["error"]["code"] == "BACKEND_MISCONFIGURED"


def test_locally_an_invalid_configuration_still_stops_the_process(monkeypatch) -> None:
    _not_on_vercel(monkeypatch)
    monkeypatch.setenv("DATA_MODE", "live")
    with pytest.raises(SystemExit):
        main._app_from_environment()


def _unreachable_database(db) -> list[int]:  # noqa: ANN001
    try:
        raise OSError(
            "connection failed: postgresql://postgres.ref:hunter2@db.abcdefghij.supabase.co/postgres "
            "password=hunter2 timeout expired"
        )
    except OSError as cause:
        raise MarketLensError(ErrorCode.PERSISTENCE_ERROR, "The database could not complete the operation.") from cause


def test_on_vercel_a_database_it_cant_reach_is_named_with_the_fix(monkeypatch) -> None:
    _on_vercel(monkeypatch)
    monkeypatch.setattr(main, "initialize_database", _unreachable_database)
    settings = Settings(_env_file=None, database_url=SUPABASE_DIRECT)
    with TestClient(create_app(settings, gateway=HangingGateway())) as client:
        health = client.get("/api/health")
        runs = client.get("/api/research")
    assert health.status_code == runs.status_code == 503
    message = health.json()["error"]["message"]
    assert message.startswith("Setup problem: The backend can't use its database (OSError: connection failed:")
    assert "Transaction pooler" in message  # Supabase's direct host is IPv6-only
    assert "hunter2" not in health.text and "***" in message


def test_locally_a_database_failure_still_stops_startup(monkeypatch) -> None:
    _not_on_vercel(monkeypatch)
    monkeypatch.setattr(main, "initialize_database", _unreachable_database)
    settings = Settings(_env_file=None, database_url=SUPABASE_DIRECT)
    with pytest.raises(MarketLensError), TestClient(create_app(settings, gateway=HangingGateway())):
        pass


# ----------------------------------------------------------------- MCP servers
@pytest.mark.anyio
async def test_mcp_servers_start_when_their_log_folder_cant_be_created(tmp_path) -> None:
    blocker = tmp_path / "a-file-not-a-folder"
    blocker.write_text("")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/gw.db", mcp_log_dir=str(blocker / "logs"))
    gateway = MCPClientGateway(settings)
    try:
        await gateway.start()
        status = gateway.status()
    finally:
        await gateway.aclose()
    assert all(s["ready"] for s in status.values()), status
    assert all(c.log_path is None for c in gateway.connections.values())  # now logging to stderr


@pytest.mark.anyio
async def test_a_supervisor_that_dies_records_why(tmp_path, monkeypatch) -> None:
    def broken(self: ServerConnection):  # noqa: ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(ServerConnection, "_open_log", broken)
    gateway = MCPClientGateway(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/gw.db"))
    try:
        await gateway.start(wait_seconds=0.5)
        status = gateway.status()
    finally:
        await gateway.aclose()
    for server in SERVERS:
        assert status[server]["ready"] is False
        assert status[server]["last_error"] == "RuntimeError: boom"


def test_data_status_doesnt_hang_on_servers_that_dont_answer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.api.health.STATUS_WAIT_SECONDS", 0.2)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/status.db")
    with TestClient(create_app(settings, gateway=HangingGateway())) as client:
        response = client.get("/api/data-status")
    assert response.status_code == 200
    assert sorted(response.json()["unavailable_servers"]) == ["market_data", "research_sources"]
