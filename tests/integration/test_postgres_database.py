"""Postgres behaviour that SQLite gives for free (D-98): migrations, serialized writes, order, errors.

Skipped unless ``MARKETLENS_TEST_POSTGRES_URL`` is set. The rest of the suite also runs on
Postgres then, through the ``database_url`` fixture.
"""

from __future__ import annotations

import threading
import time

import pytest
from marketlens_core.db import PostgresDatabase, initialize_database
from marketlens_core.db.connection import _postgres_sql
from marketlens_core.errors import ErrorCode, MarketLensError

from tests.conftest import TEST_POSTGRES_URL

needs_postgres = pytest.mark.skipif(not TEST_POSTGRES_URL, reason="set MARKETLENS_TEST_POSTGRES_URL to run")


def test_placeholders_are_translated_and_literal_percent_signs_escaped() -> None:
    assert _postgres_sql("SELECT * FROM t WHERE a = ? AND b LIKE ? ESCAPE '\\'") == (
        "SELECT * FROM t WHERE a = %s AND b LIKE %s ESCAPE '\\'"
    )
    assert _postgres_sql("SELECT '100%' WHERE x = ?") == "SELECT '100%%' WHERE x = %s"


@needs_postgres
def test_migrations_apply_once_and_record_the_same_versions_as_sqlite(database_url) -> None:
    db = PostgresDatabase(database_url)
    try:
        assert initialize_database(db) == [1, 2, 3, 4]
        assert initialize_database(db) == []
        with db.read() as conn:
            versions = [
                r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1").fetchall()
            ]
        assert versions == [1, 2, 3, 4]
    finally:
        db.close()


@needs_postgres
def test_writes_are_serialized_like_sqlite_begin_immediate(database_url) -> None:
    """Read-modify-write from many threads loses no update: the write lock serializes them."""
    db = PostgresDatabase(database_url, max_connections=8)
    try:
        initialize_database(db)
        with db.write() as conn:
            conn.execute("INSERT INTO provider_rate_limits (provider, next_allowed_at) VALUES (?, ?)", ("t", 0.0))

        def bump() -> None:
            with db.write() as conn:
                value = conn.execute("SELECT next_allowed_at FROM provider_rate_limits WHERE provider = ?", ("t",))
                current = value.fetchone()["next_allowed_at"]
                time.sleep(0.02)  # widen the race window
                conn.execute(
                    "UPDATE provider_rate_limits SET next_allowed_at = ? WHERE provider = ?", (current + 1, "t")
                )

        threads = [threading.Thread(target=bump) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with db.read() as conn:
            final = conn.execute("SELECT next_allowed_at FROM provider_rate_limits WHERE provider = ?", ("t",))
            assert final.fetchone()["next_allowed_at"] == 8
    finally:
        db.close()


@needs_postgres
def test_evidence_keeps_insertion_order_and_duplicates_are_ignored(database_url) -> None:
    db = PostgresDatabase(database_url)
    try:
        initialize_database(db)
        with db.write() as conn:
            conn.execute(
                "INSERT INTO research_runs (run_id, symbol, question, time_horizon_json, status, stage, data_mode, "
                "llm_mode, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("run_x", "AAPL", "q?", "{}", "queued", "queued", "fixture", "fixture", "2026-10-16", "2026-10-16"),
            )
            rows = [("run_x", eid, "k", "s", "2026-10-16", "fixture", 1, "h", "{}") for eid in ("ev_c", "ev_a", "ev_b")]
            sql = (
                "INSERT INTO evidence_items (run_id, evidence_id, kind, source_name, retrieved_at, source_mode, "
                "is_fixture, content_hash, item_json) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING"
            )
            conn.executemany(sql, rows)
            conn.executemany(sql, rows[:1])  # a repeat is ignored, not an error
        with db.read() as conn:
            order = conn.execute("SELECT evidence_id FROM evidence_items WHERE run_id = ? ORDER BY rowid", ("run_x",))
            assert [r["evidence_id"] for r in order.fetchall()] == ["ev_c", "ev_a", "ev_b"]
    finally:
        db.close()


@needs_postgres
def test_database_errors_become_persistence_errors_without_details_leaking(database_url) -> None:
    db = PostgresDatabase(database_url)
    try:
        initialize_database(db)
        with pytest.raises(MarketLensError) as raised, db.write() as conn:
            conn.execute(
                "INSERT INTO tool_calls (call_id, server_id, tool_name, transport, status, started_at) "
                "VALUES (?,?,?,?,?,?)",
                ("c1", "s", "t", "stdio", "not-a-status", "now"),
            )
        assert raised.value.code == ErrorCode.PERSISTENCE_ERROR
        assert raised.value.details == {"database_error": "CheckViolation"}
    finally:
        db.close()
