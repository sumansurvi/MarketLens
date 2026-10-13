"""SQLite schema initialization and repositories."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
from marketlens_core.db import Database, initialize_database
from marketlens_core.db.migrate import available_migrations
from marketlens_core.db.repositories import RunRepository, WorkspaceRepository
from marketlens_core.errors import MarketLensError
from marketlens_core.models import DataMode, ResearchRun, RunStage, RunStatus, TimeHorizon


def _run(run_id: str = "run_0123456789abcdef", status: RunStatus = RunStatus.QUEUED) -> ResearchRun:
    now = dt.datetime(2026, 10, 9, 12, tzinfo=dt.UTC)
    horizon = TimeHorizon(
        start_date=dt.date(2026, 6, 30),
        end_date=dt.date(2026, 9, 30),
        label="x",
        is_default=True,
        reference_date=dt.date(2026, 9, 30),
        basis="fixture",
    )
    return ResearchRun(
        run_id=run_id,
        symbol="AAPL",
        question="q?",
        time_horizon=horizon,
        status=status,
        stage=RunStage.CREATED,
        data_mode=DataMode.FIXTURE,
        llm_mode="fixture",
        created_at=now,
        updated_at=now,
    )


def test_migrations_are_idempotent_and_versioned(tmp_path) -> None:
    db = Database(tmp_path / "m.db")
    versions = [version for version, _, _ in available_migrations()]
    assert versions == list(range(1, len(versions) + 1))  # consecutive, forward-only
    assert initialize_database(db) == versions
    assert initialize_database(db) == []
    with db.read() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "research_runs",
            "evidence_items",
            "pending_actions",
            "confirmation_grants",
            "watchlist_items",
            "voice_sessions",
            "analytics_events",
        } <= tables
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_unknown_future_schema_is_reported_not_modified(tmp_path) -> None:
    db = Database(tmp_path / "m.db")
    initialize_database(db)
    with db.write() as conn:
        conn.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (999, 'future', 'x')")
    with pytest.raises(MarketLensError):
        initialize_database(db)


def test_run_roundtrip_idempotent_client_request_and_interruption(tmp_path) -> None:
    db = Database(tmp_path / "r.db")
    initialize_database(db)
    runs = RunRepository(db)
    first = runs.create_run(_run(), session_hash="s1", client_request_id="client-req-0001")
    again = runs.create_run(_run("run_fedcba9876543210"), session_hash="s1", client_request_id="client-req-0001")
    assert again.run_id == first.run_id
    assert runs.get_run(first.run_id).symbol == "AAPL"
    runs.update_run(first.run_id, status=RunStatus.RUNNING, stage=RunStage.COLLECTING_EVIDENCE)
    assert runs.mark_interrupted() == 1
    assert runs.get_run(first.run_id).status == RunStatus.FAILED
    with pytest.raises(ValueError):
        runs.update_run(first.run_id, symbol="EVIL")


def test_foreign_keys_are_enforced(tmp_path) -> None:
    db = Database(tmp_path / "f.db")
    initialize_database(db)
    with pytest.raises(MarketLensError):
        with db.write() as conn:
            conn.execute(
                "INSERT INTO research_steps (run_id, seq, step_key, agent, status) VALUES ('nope', 1, 'a', 'b', 'c')"
            )


def test_symbol_queries_use_bound_parameters(tmp_path) -> None:
    db = Database(tmp_path / "s.db")
    initialize_database(db)
    WorkspaceRepository(db).list_saved_briefs(symbol="AAPL' OR '1'='1")  # must not raise or match everything
    with db.read() as conn:
        assert conn.execute("SELECT count(*) FROM saved_briefs").fetchone()[0] == 0
    assert isinstance(db.connect(), sqlite3.Connection)


def test_existing_database_upgrades_forward_without_losing_data(tmp_path) -> None:
    db = Database(tmp_path / "upgrade.db")
    initialize_database(db)
    # Recreate a version-1 database (before voice sessions) that already holds a watchlist row.
    with db.write() as conn:
        conn.execute("DROP TABLE voice_sessions")
        conn.execute("DELETE FROM schema_migrations WHERE version = 2")
        conn.execute(
            "INSERT INTO watchlist_items (symbol, origin, created_at, updated_at) VALUES ('AAPL', 'ui', '2026-10-09T00:00:00Z', '2026-10-09T00:00:00Z')"
        )
    assert initialize_database(db) == [2]
    with db.read() as conn:
        assert conn.execute("SELECT symbol FROM watchlist_items").fetchall()[0][0] == "AAPL"
        assert conn.execute("SELECT COUNT(*) FROM voice_sessions").fetchone()[0] == 0
