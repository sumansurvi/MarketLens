"""Research runs executed through Vercel Queues (RUN_EXECUTION=queue, D-98).

The backend publishes each run instead of running it; a queue-triggered consumer claims and runs
it. These tests use SQLite (the logic is the same; production requires Postgres, which
``validate_settings`` enforces) and run the whole suite's Postgres variant when configured.
"""

from __future__ import annotations

import asyncio
import uuid
from functools import partial

import pytest
from app.container import build_container
from app.main import create_app
from app.services.run_consumer import run_queued_research
from app.services.run_manager import QueueRunManager
from marketlens_core.config import Settings, validate_settings
from marketlens_core.db import initialize_database
from marketlens_core.models import ResearchRequest, RunStage, RunStatus
from marketlens_core.timeutil import utc_now
from starlette.testclient import TestClient

HEADERS = {"X-MarketLens-Client": "pytest"}
REQUEST = {"symbol": "AAPL", "question": "How did the stock do recently?"}


class FakeQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, str], str | None]] = []
        self.fail = fail

    async def send(self, topic: str, payload: dict[str, str], *, idempotency_key: str | None = None) -> str:
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.sent.append((topic, payload, idempotency_key))
        return "msg_test"


def queue_settings(database_url: str, **overrides: object) -> Settings:
    return Settings(_env_file=None, database_url=database_url, run_execution="queue", **overrides)


def queue_client(settings: Settings, queue: FakeQueue):
    client = TestClient(create_app(settings), headers=HEADERS)
    client.__enter__()
    client.app.state.container.run_manager._client = queue
    return client


def test_queue_mode_publishes_the_run_and_the_consumer_runs_it_once(database_url) -> None:
    queue = FakeQueue()
    client = queue_client(queue_settings(database_url), queue)
    try:
        run = client.post("/api/research", json=REQUEST).json()["run"]
        assert queue.sent == [("marketlens-research-runs", {"run_id": run["run_id"]}, run["run_id"])]
        detail = client.get(f"/api/research/{run['run_id']}").json()
        assert detail["run"]["status"] == "queued" and detail["is_active"] is True

        container = client.app.state.container
        consume = partial(run_queued_research, container.workflow, container.runs, run["run_id"])
        assert client.portal.call(consume) == "completed"  # on the app's loop, where the MCP clients live
        assert client.get(f"/api/research/{run['run_id']}").json()["run"]["status"] == "completed"
        # At-least-once delivery: a repeated message doesn't run the research again.
        assert client.portal.call(partial(consume, delivery_count=2)) == "skipped"
    finally:
        client.__exit__(None, None, None)


def test_a_queue_failure_leaves_a_failed_run_with_a_reason(database_url) -> None:
    client = queue_client(queue_settings(database_url), FakeQueue(fail=True))
    try:
        run = client.post("/api/research", json=REQUEST).json()["run"]
        detail = client.get(f"/api/research/{run['run_id']}").json()["run"]
        assert detail["status"] == "failed"
        assert "couldn't be queued" in detail["errors"][0]["message"]
    finally:
        client.__exit__(None, None, None)


def test_cancelling_before_delivery_and_while_running(database_url) -> None:
    client = queue_client(queue_settings(database_url), FakeQueue())
    container = client.app.state.container
    try:
        # Not started yet: cancelled at once, and a later delivery skips it.
        waiting = client.post("/api/research", json=REQUEST).json()["run"]
        assert client.post(f"/api/research/{waiting['run_id']}/cancel").json()["status"] == "cancelled"
        consume = partial(run_queued_research, container.workflow, container.runs, waiting["run_id"])
        assert client.portal.call(consume) == "skipped"

        # Running in the consumer: the web app only sets the flag; the consumer stops the run.
        running = client.post("/api/research", json=REQUEST).json()["run"]
        workflow = SlowWorkflow(container.runs)
        future = client.portal.start_task_soon(
            partial(run_queued_research, workflow, container.runs, running["run_id"], poll_seconds=0.05)
        )
        client.portal.call(workflow.started.wait)
        cancelled = client.post(f"/api/research/{running['run_id']}/cancel").json()
        assert cancelled["status"] == "cancelled"
        assert future.result(timeout=10) == "cancelled"
    finally:
        client.__exit__(None, None, None)


class SlowWorkflow:
    """Stands in for ResearchWorkflowService.execute: waits until cancelled, then records it."""

    def __init__(self, runs) -> None:
        self.runs = runs
        self.started = asyncio.Event()

    async def execute(self, run_id: str) -> None:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.runs.update_run(run_id, status=RunStatus.CANCELLED, stage=RunStage.DONE, completed_at=utc_now())
            raise


def test_interrupted_and_stale_runs_are_marked_failed(database_url) -> None:
    client = queue_client(queue_settings(database_url), FakeQueue())
    container = client.app.state.container
    try:
        # A redelivery finds the run still "running" (its function was cut off): fail it, don't rerun.
        cut_off = client.post("/api/research", json=REQUEST).json()["run"]
        assert container.runs.claim_queued_run(cut_off["run_id"])
        consume = partial(run_queued_research, container.workflow, container.runs, cut_off["run_id"], delivery_count=2)
        assert client.portal.call(consume) == "interrupted"
        assert client.get(f"/api/research/{cut_off['run_id']}").json()["run"]["status"] == "failed"

        # A run nobody picked up within its time limit plus the margin is stopped when anyone looks.
        stale = client.post("/api/research", json=REQUEST).json()["run"]
        with container.db.write() as conn:
            conn.execute(
                "UPDATE research_runs SET created_at = ? WHERE run_id = ?", ("2000-01-01T00:00:00Z", stale["run_id"])
            )
        listed = {r["run_id"]: r for r in client.get("/api/research").json()["runs"]}
        assert listed[stale["run_id"]]["status"] == "failed"
        assert "didn't finish in time" in listed[stale["run_id"]]["errors"][0]["message"]
    finally:
        client.__exit__(None, None, None)


def test_queue_mode_requires_postgres() -> None:
    problems = validate_settings(Settings(_env_file=None, run_execution="queue"))
    assert any("RUN_EXECUTION=queue requires a Postgres" in p for p in problems)
    postgres = Settings(_env_file=None, run_execution="queue", database_url="postgresql://u@db.example/marketlens")
    assert not any("RUN_EXECUTION" in p for p in validate_settings(postgres))


@pytest.mark.anyio
async def test_vercel_queues_delivers_a_published_run_to_the_consumer(database_url) -> None:
    """The real SDK path: publish → Vercel Queues (the SDK's embedded service) → subscriber."""
    from vercel.queue import Message, subscribe
    from vercel.queue.embedded import embedded_queue_service

    topic = f"test-runs-{uuid.uuid4().hex[:8]}"
    settings = queue_settings(database_url, research_queue_topic=topic)
    container = build_container(settings)
    initialize_database(container.db)
    await container.gateway.start()
    outcomes: list[str] = []

    @subscribe(topic=topic)
    async def consume(message: Message[dict[str, str]]) -> None:
        outcomes.append(
            await run_queued_research(
                container.workflow,
                container.runs,
                message.payload["run_id"],
                delivery_count=message.metadata.delivery_count,
            )
        )

    try:
        async with embedded_queue_service() as service:
            manager = QueueRunManager(settings, container.runs, queue_client=service.get_async_client())
            run = await container.workflow.create_run(ResearchRequest(**REQUEST), session_id="s" * 43)
            await manager.start(run.run_id)
            await manager.wait(run.run_id, wait_seconds=60)
        stored = container.runs.get_run(run.run_id)
        assert stored is not None and stored.status == RunStatus.COMPLETED
        assert outcomes == ["completed"]
    finally:
        await container.gateway.aclose()
        container.db.close()
