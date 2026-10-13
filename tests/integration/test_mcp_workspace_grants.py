"""Workspace write authorization, exercised through the MCP protocol (MCP_TOOLS §6, §8).

The backend's role (proposal + grant issuance after a UI click) is played by the same
``ActionService`` the backend uses; every write itself goes through the workspace MCP
server process."""

from __future__ import annotations

import secrets

import pytest
from app.main import create_app
from marketlens_core.actions import ActionService, action_payload, payload_hash_of
from marketlens_core.config import Settings
from marketlens_core.db import Database
from marketlens_core.db.repositories import RunRepository
from marketlens_core.hashing import hash_secret
from marketlens_core.models import ActionType
from starlette.testclient import TestClient

from tests.helpers import mcp_session, structured, wait_for_run

pytestmark = pytest.mark.anyio
ADD, REMOVE, SAVE = ActionType.ADD_WATCHLIST_SYMBOL, ActionType.REMOVE_WATCHLIST_SYMBOL, ActionType.SAVE_RESEARCH_BRIEF


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> tuple[Settings, str, str]:
    """A database with two completed research runs (one is used destructively)."""
    root = tmp_path_factory.mktemp("workspace")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{root}/ws.db")
    with TestClient(create_app(settings), headers={"X-MarketLens-Client": "pytest"}) as client:
        runs = []
        for symbol in ("AAPL", "NVDA"):
            run_id = client.post("/api/research", json={"symbol": symbol, "question": "Seed run"}).json()["run"][
                "run_id"
            ]
            assert wait_for_run(client, run_id)["run"]["status"] == "completed"
            runs.append(run_id)
    return settings, runs[0], runs[1]


def approve(
    settings: Settings,
    kind: ActionType,
    *,
    session: str = "session-A",
    symbol: str | None = None,
    run_id: str | None = None,
    title: str | None = None,
):
    """Backend role: propose with a server-computed hash, then issue a grant after 'confirmation'."""
    db = Database(settings.database_path)
    service = ActionService(db, action_ttl_seconds=300)
    session_hash = hash_secret(session)
    if kind == SAVE:
        _brief, brief_hash = RunRepository(db).get_brief(run_id)
        payload = action_payload(kind, run_id=run_id, title=title, brief_payload_hash=brief_hash)
    else:
        payload = action_payload(kind, symbol=symbol)
    action = service.create(
        action_type=kind,
        run_id=run_id,
        symbol=symbol,
        title=title,
        display_name=None,
        payload_hash=payload_hash_of(payload),
        session_hash=session_hash,
    )
    grant_id, _ = service.issue_grant(action.action_id, session_hash)
    return action, grant_id, session_hash


def write_args(action, grant_id: str, session_hash: str, **override) -> dict:
    args = {"confirmation_grant_id": grant_id, "expected_payload_hash": action.payload_hash, "session_id": session_hash}
    if action.action_type == SAVE:
        args.update({"run_id": action.run_id, "brief_title": action.title})
    else:
        args["symbol"] = action.symbol
    args.update(override)
    return args


def rows(settings: Settings, sql: str, params: tuple = ()) -> list:
    with Database(settings.database_path).read() as conn:
        return conn.execute(sql, params).fetchall()


def code(result) -> str:
    payload = structured(result)
    assert result.is_error and not payload["ok"], payload
    return payload["error"]["code"]


async def test_valid_grant_writes_exactly_once_and_replay_is_rejected(seeded) -> None:
    settings, _, _ = seeded
    action, grant, session = approve(settings, ADD, symbol="ZZAA")
    async with mcp_session("research_workspace", settings) as c:
        first = structured(await c.call_tool("add_watchlist_symbol", write_args(action, grant, session)))
        assert first["ok"] and first["data"]["already_exists"] is False
        replay = await c.call_tool("add_watchlist_symbol", write_args(action, grant, session))
        assert code(replay) == "ACTION_ALREADY_CONSUMED"
        assert structured(replay)["error"]["details"]["result"]["symbol"] == "ZZAA"
    assert len(rows(settings, "SELECT * FROM watchlist_items WHERE symbol = 'ZZAA'")) == 1


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda a, g, s: write_args(a, secrets.token_urlsafe(32), s), "INVALID_CONFIRMATION_GRANT"),
        (lambda a, g, s: write_args(a, g, hash_secret("session-B")), "INVALID_CONFIRMATION_GRANT"),
        (lambda a, g, s: write_args(a, g, s, symbol="ZZXX"), "PAYLOAD_HASH_MISMATCH"),
        (lambda a, g, s: write_args(a, g, s, expected_payload_hash="sha256:" + "0" * 64), "PAYLOAD_HASH_MISMATCH"),
    ],
    ids=["invalid-grant", "wrong-session", "altered-symbol", "altered-expected-hash"],
)
async def test_bad_grants_cannot_write(seeded, mutate, expected) -> None:
    settings, _, _ = seeded
    action, grant, session = approve(settings, ADD, symbol="ZZBB")
    async with mcp_session("research_workspace", settings) as c:
        assert code(await c.call_tool("add_watchlist_symbol", mutate(action, grant, session))) == expected
    assert rows(settings, "SELECT * FROM watchlist_items WHERE symbol IN ('ZZBB', 'ZZXX')") == []


async def test_expired_grant_cannot_write(seeded) -> None:
    settings, _, _ = seeded
    action, grant, session = approve(settings, ADD, symbol="ZZCC")
    with Database(settings.database_path).write() as conn:
        conn.execute(
            "UPDATE confirmation_grants SET expires_at = '2000-01-01T00:00:00Z' WHERE action_id = ?",
            (action.action_id,),
        )
    async with mcp_session("research_workspace", settings) as c:
        assert (
            code(await c.call_tool("add_watchlist_symbol", write_args(action, grant, session)))
            == "CONFIRMATION_EXPIRED"
        )
    assert rows(settings, "SELECT * FROM watchlist_items WHERE symbol = 'ZZCC'") == []


async def test_grant_for_a_different_action_type_cannot_write(seeded) -> None:
    settings, _, _ = seeded
    add_action, add_grant, session = approve(settings, ADD, symbol="ZZDD")
    async with mcp_session("research_workspace", settings) as c:
        misuse = await c.call_tool("remove_watchlist_symbol", write_args(add_action, add_grant, session))
        assert code(misuse) == "INVALID_CONFIRMATION_GRANT"
        # The untouched grant still works for the action it was issued for.
        assert structured(await c.call_tool("add_watchlist_symbol", write_args(add_action, add_grant, session)))["ok"]


async def test_absent_grant_and_confirmed_flag_are_rejected_by_schema(seeded) -> None:
    settings, _, _ = seeded
    async with mcp_session("research_workspace", settings) as c:
        for arguments in (
            {"symbol": "ZZEE", "session_id": "0" * 64, "expected_payload_hash": "sha256:" + "0" * 64},
            {"symbol": "ZZEE", "confirmed": True},
        ):
            result = await c.call_tool("add_watchlist_symbol", arguments)
            assert result.is_error and result.structured_content is None
    assert rows(settings, "SELECT * FROM watchlist_items WHERE symbol = 'ZZEE'") == []


async def test_duplicate_add_and_double_remove_are_idempotent(seeded) -> None:
    settings, _, _ = seeded
    a1, g1, s = approve(settings, ADD, symbol="ZZFF")
    a2, g2, _ = approve(settings, ADD, symbol="ZZFF")
    async with mcp_session("research_workspace", settings) as c:
        assert (
            structured(await c.call_tool("add_watchlist_symbol", write_args(a1, g1, s)))["data"]["already_exists"]
            is False
        )
        assert (
            structured(await c.call_tool("add_watchlist_symbol", write_args(a2, g2, s)))["data"]["already_exists"]
            is True
        )
        assert len(rows(settings, "SELECT * FROM watchlist_items WHERE symbol = 'ZZFF'")) == 1
        r1, rg1, _ = approve(settings, REMOVE, symbol="ZZFF")
        r2, rg2, _ = approve(settings, REMOVE, symbol="ZZFF")
        first = structured(await c.call_tool("remove_watchlist_symbol", write_args(r1, rg1, s)))["data"]
        second = structured(await c.call_tool("remove_watchlist_symbol", write_args(r2, rg2, s)))["data"]
    assert (first["removed"], first["already_absent"]) == (True, False)
    assert (second["removed"], second["already_absent"]) == (False, True)


async def test_save_snapshots_brief_and_never_overwrites(seeded) -> None:
    settings, run_id, _ = seeded
    ids = []
    async with mcp_session("research_workspace", settings) as c:
        for title in ("First save", "Second save"):
            action, grant, session = approve(settings, SAVE, run_id=run_id, title=title)
            saved = structured(await c.call_tool("save_research_brief", write_args(action, grant, session)))["data"]
            assert saved["is_fixture"] is True and saved["report_status"] == "completed"
            ids.append(saved["brief_id"])
        snapshot = structured(await c.call_tool("get_saved_brief", {"brief_id": ids[0]}))["data"]["saved"]
    assert len(set(ids)) == 2
    assert snapshot["title"] == "First save" and snapshot["evidence"] and snapshot["brief"]["claims"]
    assert snapshot["brief"]["as_of"]["fixture_dataset_version"] == "fx-2026-09-30-v1"


async def test_save_after_run_brief_is_missing_writes_nothing(seeded) -> None:
    settings, _, doomed_run = seeded
    action, grant, session = approve(settings, SAVE, run_id=doomed_run, title="Will fail")
    with Database(settings.database_path).write() as conn:
        conn.execute("DELETE FROM research_briefs WHERE run_id = ?", (doomed_run,))
    async with mcp_session("research_workspace", settings) as c:
        assert code(await c.call_tool("save_research_brief", write_args(action, grant, session))) == "NOT_FOUND"
    assert rows(settings, "SELECT * FROM saved_briefs WHERE run_id = ?", (doomed_run,)) == []


async def test_writes_persist_across_server_restart(seeded) -> None:
    settings, _, _ = seeded
    action, grant, session = approve(settings, ADD, symbol="ZZGG")
    async with mcp_session("research_workspace", settings, instance="first") as c:
        assert structured(await c.call_tool("add_watchlist_symbol", write_args(action, grant, session)))["ok"]
    async with mcp_session("research_workspace", settings, instance="second") as c:
        items = structured(await c.call_tool("list_watchlist", {}))["data"]["items"]
    assert "ZZGG" in {i["symbol"] for i in items}


async def test_every_persisted_row_has_a_consumed_grant_and_no_raw_secrets(seeded) -> None:
    settings, _, _ = seeded
    unauthorized_watchlist = rows(
        settings,
        """SELECT w.symbol FROM watchlist_items w LEFT JOIN pending_actions a ON a.action_id = w.last_action_id
        LEFT JOIN confirmation_grants g ON g.action_id = a.action_id
        WHERE a.status IS NOT 'consumed' OR g.consumed_at IS NULL""",
    )
    unauthorized_briefs = rows(
        settings,
        """SELECT s.brief_id FROM saved_briefs s LEFT JOIN pending_actions a ON a.action_id = s.action_id
        LEFT JOIN confirmation_grants g ON g.action_id = a.action_id
        WHERE a.status IS NOT 'consumed' OR g.consumed_at IS NULL""",
    )
    assert unauthorized_watchlist == [] and unauthorized_briefs == []
    rejected = rows(settings, "SELECT metadata_json FROM audit_events WHERE event_type = 'write_rejected'")
    assert len(rejected) >= 5
    # Grants and sessions are stored only as hashes.
    action, grant, _ = approve(settings, ADD, symbol="ZZHH")
    dump = "\n".join(str(tuple(r)) for r in rows(settings, "SELECT * FROM confirmation_grants"))
    assert grant not in dump and "session-A" not in dump
