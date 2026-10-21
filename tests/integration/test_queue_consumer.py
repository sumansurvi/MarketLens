"""The production research consumer (``vercel_backend.queue_consumer``) as Vercel Queues calls it (D-102).

Covers what the first live deploy could not show: that the consumer is registered with the trigger
Vercel builds from, that a delivery runs the research through the SDK's push path, and that a
failing consumer retries and then records the run as failed with a reason, instead of leaving it
queued until the stale-run sweep.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from marketlens_core.config import Settings
from marketlens_core.db import initialize_database
from marketlens_core.db.connection import open_database
from marketlens_core.db.repositories import RunRepository
from marketlens_core.models import ResearchRequest, RunStatus

REQUEST = {"symbol": "AAPL", "question": "How did the stock do recently?"}
RUN_ID = "run_0123456789abcdef"


@pytest.fixture
def consumer(monkeypatch):
    # Importing the consumer package marks the process as running on Vercel (VERCEL=1) unless it is
    # already set, so set it to "" first: the rest of the suite must keep its local settings.
    monkeypatch.setenv("VERCEL", "")
    import vercel_backend.queue_consumer as module

    monkeypatch.setattr(module, "_container", None)
    monkeypatch.setattr(module, "_container_loop", None)
    return module


def delivery(run_id: str, count: int):
    """What Vercel Queues hands the consumer: the payload and the delivery metadata."""
    return SimpleNamespace(payload={"run_id": run_id}, metadata=SimpleNamespace(delivery_count=count))


def test_the_build_sees_one_trigger_for_the_research_topic(consumer) -> None:
    """Vercel reads the subscriptions a module registers when it builds (python-sdk, subscriber configuration)."""
    from vercel.queue import get_subscriptions

    mine = [s for s in get_subscriptions() if s.func is consumer.run_research]
    assert len(mine) == 1
    trigger = mine[0]
    assert trigger.topic == consumer.TOPIC == "marketlens-research-runs"
    assert trigger.max_attempts == consumer.MAX_ATTEMPTS == 3
    assert trigger.retry_after_seconds == consumer.RETRY_AFTER_SECONDS == 30


@pytest.mark.anyio
async def test_an_early_failure_is_raised_so_the_queue_retries_it(consumer, monkeypatch) -> None:
    async def broken():
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(consumer, "get_container", broken)
    with pytest.raises(RuntimeError, match="database unreachable"):
        await consumer.run_research(delivery(RUN_ID, count=1))


@pytest.mark.anyio
async def test_the_last_failed_delivery_records_the_run_as_failed(consumer, monkeypatch, database_url) -> None:
    from app.container import build_container

    # The consumer's own settings check requires Postgres in queue mode; these tests use SQLite.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RUN_EXECUTION", "inline")
    container = build_container(Settings(_env_file=None, database_url=database_url, run_execution="queue"))
    initialize_database(container.db)
    run = await container.workflow.create_run(ResearchRequest(**REQUEST), session_id="s" * 43)
    container.db.close()

    async def broken():
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(consumer, "get_container", broken)
    # The last delivery returns normally (no further retry) and records why the run stopped.
    assert await consumer.run_research(delivery(run.run_id, count=consumer.MAX_ATTEMPTS)) is None

    db = open_database(Settings(_env_file=None, database_url=database_url))
    try:
        stored = RunRepository(db).get_run(run.run_id)
    finally:
        db.close()
    assert stored is not None and stored.status == RunStatus.FAILED
    assert stored.errors[0].code == "RESEARCH_STOPPED" and stored.errors[0].retryable is True


@pytest.mark.anyio
async def test_the_production_consumer_runs_a_published_run_through_the_push_path(consumer, monkeypatch, database_url):
    """Publish → the SDK's embedded queue service → push delivery to ``run_research`` → completed."""
    from app.container import build_container
    from vercel.queue.embedded import embedded_queue_service

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RUN_EXECUTION", "inline")
    container = build_container(Settings(_env_file=None, database_url=database_url, run_execution="queue"))
    initialize_database(container.db)
    run = await container.workflow.create_run(ResearchRequest(**REQUEST), session_id="s" * 43)
    try:
        async with embedded_queue_service() as service:
            await service.get_async_client().send(consumer.TOPIC, {"run_id": run.run_id}, idempotency_key=run.run_id)
            stored = container.runs.get_run(run.run_id)
            for _ in range(600):  # the dispatch is asynchronous; the fixture run takes a few seconds
                stored = container.runs.get_run(run.run_id)
                if stored is not None and stored.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                    break
                await asyncio.sleep(0.1)
        assert stored is not None and stored.status == RunStatus.COMPLETED, stored and stored.errors
    finally:
        built = consumer._container
        if built is not None:
            await built.gateway.aclose()
            built.db.close()
        container.db.close()
