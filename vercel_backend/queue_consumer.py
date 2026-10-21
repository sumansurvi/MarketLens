"""The research-run consumer: a private, queue-triggered Vercel Function (D-98).

Declared under ``[[tool.vercel.subscribers]]`` in ``pyproject.toml``; Vercel compiles this module's
subscriber into a function that only Vercel Queues can invoke. The backend publishes
``{"run_id": …}`` when a run is created (``QueueRunManager``); this runs it within the function's
time limit (300 s on Hobby, so keep ``RUN_TIMEOUT_SECONDS`` below about 280).

The app container (database, MCP servers, model client) is built once per instance and reused by
later deliveries on the same event loop.

Every step is logged (``research run … delivery N of 3``), so the function's runtime logs show whether
deliveries arrive and where one fails. A failed delivery is retried by Vercel Queues; on the last
attempt the run is recorded as failed at once, with the reason (D-102).
"""

# Importing this module runs vercel_backend/__init__.py first (import paths, Vercel marker).
from __future__ import annotations

import asyncio
import logging
import os

from vercel.queue import Message, subscribe

logger = logging.getLogger("marketlens.queue")

TOPIC = os.environ.get("RESEARCH_QUEUE_TOPIC", "marketlens-research-runs")
# Vercel Queues makes at most MAX_ATTEMPTS deliveries, RETRY_AFTER_SECONDS apart (D-102).
MAX_ATTEMPTS = 3
RETRY_AFTER_SECONDS = 30

_container = None
_container_loop: asyncio.AbstractEventLoop | None = None
_locks: dict[int, asyncio.Lock] = {}


def _configure_logging() -> None:
    # Runtime logs only show what the root logger passes on, and this function may run before the app does.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


_configure_logging()
# Vercel imports this module when it builds and when an instance starts, so this line shows the consumer exists.
logger.info("research consumer loaded: topic %s, up to %d deliveries", TOPIC, MAX_ATTEMPTS)


async def get_container():  # noqa: ANN201 - AppContainer, imported lazily so the build-time import stays light
    """The instance's container, built on first use (and again if the event loop changed)."""
    global _container, _container_loop
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(id(loop), asyncio.Lock())
    async with lock:
        if _container is None or _container_loop is not loop:
            from app.container import build_container
            from marketlens_core.config import load_settings
            from marketlens_core.db import initialize_database

            container = build_container(load_settings())
            initialize_database(container.db)
            await container.gateway.start()  # type: ignore[attr-defined]
            _container, _container_loop = container, loop
    return _container


def _record_final_failure(run_id: str) -> None:
    """The last delivery failed: stop the run now, with a reason, rather than after the stale-run sweep."""
    from marketlens_core.config import load_settings
    from marketlens_core.db import open_database
    from marketlens_core.db.repositories import RunRepository
    from marketlens_core.errors import ErrorCode

    db = None
    try:
        db = open_database(load_settings())
        RunRepository(db).fail_run(
            run_id,
            "The research service stopped before it could finish this run. Try again.",
            code=ErrorCode.RESEARCH_STOPPED,
            retryable=True,
        )
        logger.error("research run %s: stopped after %d deliveries", run_id, MAX_ATTEMPTS)
    except Exception:  # noqa: BLE001 - if the database is the problem, the stale-run sweep stops the run instead
        logger.exception("research run %s: could not record the failure", run_id)
    finally:
        if db is not None:
            db.close()


@subscribe(topic=TOPIC, max_attempts=MAX_ATTEMPTS, retry_after=RETRY_AFTER_SECONDS)
async def run_research(message: Message[dict[str, str]]) -> None:
    from app.services.run_consumer import run_queued_research

    _configure_logging()
    run_id = str(message.payload.get("run_id", ""))
    delivery = message.metadata.delivery_count
    if not run_id:
        logger.warning("ignored a queue message without a run_id")
        return
    logger.info("research run %s: delivery %d of %d", run_id, delivery, MAX_ATTEMPTS)
    try:
        container = await get_container()
        outcome = await run_queued_research(container.workflow, container.runs, run_id, delivery_count=delivery)
    except Exception:
        logger.exception("research run %s: delivery %d of %d failed", run_id, delivery, MAX_ATTEMPTS)
        if delivery < MAX_ATTEMPTS:
            raise  # Vercel Queues delivers it again after RETRY_AFTER_SECONDS
        _record_final_failure(run_id)
        return
    logger.info("research run %s: %s", run_id, outcome)
