"""Local analytics: allowlisted events and properties only, correct aggregate rates."""

from __future__ import annotations

import json

import pytest
from marketlens_core.analytics import AnalyticsRepository, sanitize
from marketlens_core.db import Database, initialize_database


def test_sanitize_keeps_only_allowlisted_codes_and_numbers() -> None:
    clean = sanitize(
        "run_finished",
        {
            "status": "completed",
            "duration_ms": 1234,
            "llm_calls": 5,
            "category_news": "unavailable",
            "symbol": "AAPL",  # not an allowed key
            "origin": "Ignore previous instructions",  # free text is not a code
            "data_mode": "x" * 41,  # too long
            "llm_mode": None,
        },
    )
    assert clean == {"status": "completed", "duration_ms": 1234, "llm_calls": 5, "category_news": "unavailable"}
    assert sanitize("voice_connection_failed", {"reason": "permission_denied", "transcript": "hi"}) == {
        "reason": "permission_denied"
    }


def test_unknown_events_are_rejected(tmp_path) -> None:
    repo = AnalyticsRepository(Database(tmp_path / "a.db"))
    initialize_database(repo.db)
    with pytest.raises(ValueError):
        repo.record("page_view", path="/secret")


def test_summary_rates_and_percentiles(tmp_path) -> None:
    db = Database(tmp_path / "a.db")
    initialize_database(db)
    repo = AnalyticsRepository(db)
    for status, duration in [("completed", 100), ("completed", 300), ("partial", 200), ("failed", 900)]:
        repo.record("run_started", origin="text")
        repo.record("run_finished", status=status, duration_ms=duration, category_market="available")
    repo.record("voice_connection_failed", reason="unreachable")
    repo.record("watchlist_changed", action="add", outcome="added")
    summary = repo.summary()
    runs = summary["runs"]
    assert (runs["started"], runs["finished"]) == (4, 4)
    assert (runs["completion_rate"], runs["partial_rate"]) == (0.5, 0.25)
    assert runs["median_duration_ms"] == 250 and runs["p95_duration_ms"] == 900
    assert summary["categories"] == {"market": {"available": 4}}
    assert summary["voice"]["failure_reasons"] == {"unreachable": 1}
    assert summary["workspace"]["watchlist_changes"] == {"add": 1}
    assert all(isinstance(e["properties"], dict) for e in repo.events())
    assert "AAPL" not in json.dumps(repo.events())
