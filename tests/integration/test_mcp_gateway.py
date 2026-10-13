"""Backend MCP client gateway: allowlist, validation, least privilege, fail-closed, timeouts,
crash recovery and safe audit records."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
from pathlib import Path

import pytest
from app.mcp.client_gateway import MCPClientGateway, ServerConnection, server_environment
from marketlens_core.config import Settings
from mcp import StdioServerParameters

from tests.helpers import overlay_dataset

pytestmark = pytest.mark.anyio


@pytest.fixture
async def gateway(tmp_path):
    gw = MCPClientGateway(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/gw.db"))
    await gw.start()
    yield gw
    await gw.aclose()


async def test_unlisted_tools_are_blocked_without_a_call(gateway) -> None:
    for server, tool in (("market_data", "place_order"), ("research_workspace", "get_quote"), ("shell", "exec")):
        outcome = await gateway.call(server, tool, {})
        assert outcome.error.code.value == "MCP_TOOL_NOT_ALLOWED"
        assert outcome.record.status == "error"


async def test_arguments_are_validated_against_discovered_schemas(gateway) -> None:
    outcome = await gateway.call("market_data", "get_quote", {"symbol": 5})
    assert outcome.error.code.value == "INVALID_INPUT"
    assert outcome.error.details["fields"][0]["field"] == "symbol"
    ok = await gateway.call("market_data", "get_quote", {"symbol": "AAPL"})
    assert ok.ok and ok.record.transport == "mcp_stdio" and ok.record.latency_ms is not None


def test_server_environments_are_least_privilege(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/x.db",
        alpha_vantage_api_key="AV-KEY-123",
        sec_user_agent="MarketLens tests qa@example.org",
        openai_api_key="sk-openai",
        livekit_api_secret="lk-secret",
    )
    workspace = server_environment(settings, "research_workspace")
    market = server_environment(settings, "market_data")
    sources = server_environment(settings, "research_sources")
    assert "ALPHA_VANTAGE_API_KEY" not in workspace and "SEC_USER_AGENT" not in workspace
    assert market["ALPHA_VANTAGE_API_KEY"] == "AV-KEY-123" and "SEC_USER_AGENT" not in market
    assert sources["SEC_USER_AGENT"] and sources["ALPHA_VANTAGE_API_KEY"]
    for env in (workspace, market, sources):
        assert env["MARKETLENS_DOTENV"] == "disabled"
        assert not any(k.startswith(("OPENAI", "LIVEKIT")) for k in env)
        assert "sk-openai" not in env.values() and "lk-secret" not in env.values()


async def test_server_advertising_extra_tools_is_refused(tmp_path) -> None:
    rogue = Path(__file__).with_name("rogue_server.py")
    connection = ServerConnection(
        "market_data",
        StdioServerParameters(command=sys.executable, args=[str(rogue)], env={}),
        read_timeout=5,
        log_path=tmp_path / "rogue.log",
    )
    connection.start()
    with pytest.raises(Exception):  # noqa: B017 - ServerUnavailable
        await connection.wait_ready(3)
    assert connection.last_error and "outside the allowlist" in connection.last_error
    assert not connection.ready
    await connection.close()


async def test_tool_call_timeout_is_reported_and_gateway_recovers(tmp_path) -> None:
    slow = {
        "_fixture": {
            "is_fixture": True,
            "source_mode": "fixture",
            "label": "SYNTHETIC TEST OVERLAY",
            "delay_seconds": 4,
        },
        "Global Quote": {"01. symbol": "AAPL", "05. price": "1.00", "07. latest trading day": "2026-09-30"},
    }
    dataset = overlay_dataset(tmp_path, {"alpha_vantage/GLOBAL_QUOTE/AAPL.json": slow})
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/t.db",
        fixture_dir=str(dataset),
        mcp_tool_call_timeout_seconds=1,
    )
    gw = MCPClientGateway(settings)
    await gw.start()
    try:
        outcome = await gw.call("market_data", "get_quote", {"symbol": "AAPL"})
        assert outcome.error.code.value == "PROVIDER_TIMEOUT" and outcome.error.retryable
        follow_up = await gw.call("market_data", "get_quote", {"symbol": "NVDA"})
        assert follow_up.ok
    finally:
        await gw.aclose()


async def test_crashed_server_is_restarted_and_calls_recover(gateway) -> None:
    assert (await gateway.call("market_data", "get_quote", {"symbol": "AAPL"})).ok
    pgrep = shutil.which("pgrep")
    assert pgrep, "pgrep is required for this test"
    process = await asyncio.create_subprocess_exec(
        pgrep, "-f", f"mcp_market_data --instance {gateway.instance_id}", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    pids = stdout.decode().split()
    assert pids, "market_data server process not found"
    for pid in pids:
        os.kill(int(pid), signal.SIGKILL)
    outcomes = []
    for _ in range(40):
        outcome = await gateway.call("market_data", "get_quote", {"symbol": "AAPL"})
        outcomes.append(outcome.error.code.value if outcome.error else "ok")
        if outcome.ok:
            break
        await asyncio.sleep(0.25)
    assert outcomes[-1] == "ok", outcomes
    assert set(outcomes[:-1]) <= {"MCP_SERVER_UNAVAILABLE"}
    assert gateway.connections["market_data"].restarts >= 1


async def test_write_tool_records_mask_grants_and_sessions(gateway) -> None:
    outcome = await gateway.call(
        "research_workspace",
        "add_watchlist_symbol",
        {
            "symbol": "AAPL",
            "confirmation_grant_id": "g" * 40,
            "expected_payload_hash": "sha256:" + "0" * 64,
            "session_id": "a" * 64,
        },
    )
    assert outcome.error.code.value == "INVALID_CONFIRMATION_GRANT"
    assert outcome.record.input_summary["confirmation_grant_id"] == "[redacted]"
    assert outcome.record.input_summary["session_id"] == "[redacted]"
