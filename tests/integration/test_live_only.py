"""Live mode never shows runs made from fixture (demo) data (D-104).

Runs made before live data was configured stay in the database, but the lists, search and direct
links of a live site leave them out. A demo (fixture) site still shows them.
"""

from __future__ import annotations

import asyncio

from app.container import build_container
from app.main import create_app
from marketlens_core.config import Settings
from marketlens_core.db import initialize_database
from marketlens_core.models import ResearchRequest
from starlette.testclient import TestClient

HEADERS = {"X-MarketLens-Client": "pytest"}
REQUEST = ResearchRequest(symbol="AAPL", question="How did the stock do recently?")


def _demo_run(database_url: str) -> str:
    """A fixture-data run in the shared database, as an earlier demo deployment would have left it."""
    settings = Settings(_env_file=None, database_url=database_url, run_execution="inline")
    container = build_container(settings)
    initialize_database(container.db)
    run = asyncio.run(container.workflow.create_run(REQUEST, session_id="s" * 43))
    assert run.data_mode == "fixture"
    container.db.close()
    return run.run_id


def _live_site(database_url: str) -> Settings:
    # Only the settings the lists read matter here; no providers are called.
    return Settings(
        _env_file=None,
        database_url=database_url,
        app_mode="live",
        data_mode="live",
        llm_mode="openai",
        openai_api_key="sk-test-not-real",
        openai_model="test-model",
    )


def test_a_live_site_leaves_demo_runs_out_of_its_lists_search_and_links(database_url) -> None:
    run_id = _demo_run(database_url)
    with TestClient(create_app(_live_site(database_url)), headers=HEADERS) as client:
        assert client.get("/api/research").json()["runs"] == []
        assert client.get("/api/research/search", params={"q": "AAPL"}).json()["results"] == []
        assert client.get(f"/api/research/{run_id}").status_code == 404


def test_a_demo_site_still_shows_its_demo_runs(database_url) -> None:
    run_id = _demo_run(database_url)
    demo = Settings(_env_file=None, database_url=database_url)
    with TestClient(create_app(demo), headers=HEADERS) as client:
        assert [r["run_id"] for r in client.get("/api/research").json()["runs"]] == [run_id]
        assert client.get(f"/api/research/{run_id}").status_code == 200
