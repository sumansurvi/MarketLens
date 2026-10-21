"""Research runs executed by the page's own execute request (RUN_EXECUTION=request, D-103).

The start request only records the run as queued. Nothing is published or delivered to anything:
the page calls ``POST /api/research/{id}/execute``, which runs the research inside that request.
These tests drive that flow through the HTTP API, the way the web app does.
"""

from __future__ import annotations

import pytest
from app.main import create_app
from marketlens_core.config import Settings
from starlette.testclient import TestClient

HEADERS = {"X-MarketLens-Client": "pytest"}
REQUEST = {"symbol": "AAPL", "question": "How did the stock do recently?"}


@pytest.fixture
def client(database_url):
    settings = Settings(_env_file=None, database_url=database_url, run_execution="request")
    with TestClient(create_app(settings), headers=HEADERS) as test_client:
        yield test_client


def test_the_start_request_only_records_the_run(client) -> None:
    run = client.post("/api/research", json=REQUEST).json()["run"]
    assert run["status"] == "queued"
    # Nothing has executed it yet: the run waits for the page's execute request.
    detail = client.get(f"/api/research/{run['run_id']}").json()
    assert detail["run"]["status"] == "queued" and detail["is_active"] is True


def test_the_execute_request_runs_the_research_to_completion(client) -> None:
    run = client.post("/api/research", json=REQUEST).json()["run"]
    executed = client.post(f"/api/research/{run['run_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert client.get(f"/api/research/{run['run_id']}").json()["run"]["status"] == "completed"


def test_a_second_execute_request_does_not_run_it_again(client) -> None:
    run = client.post("/api/research", json=REQUEST).json()["run"]
    assert client.post(f"/api/research/{run['run_id']}/execute").json()["status"] == "completed"
    again = client.post(f"/api/research/{run['run_id']}/execute")
    assert again.status_code == 200 and again.json()["status"] == "completed"


def test_execute_for_an_unknown_run_is_not_found(client) -> None:
    response = client.post("/api/research/run_0123456789abcdef/execute")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_rerun_is_executed_the_same_way(client) -> None:
    run = client.post("/api/research", json=REQUEST).json()["run"]
    client.post(f"/api/research/{run['run_id']}/execute")
    rerun = client.post(f"/api/research/{run['run_id']}/rerun").json()["run"]
    assert rerun["status"] == "queued" and rerun["origin"] == "rerun"
    assert client.post(f"/api/research/{rerun['run_id']}/execute").json()["status"] == "completed"
