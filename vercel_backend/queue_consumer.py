"""The research-run consumer: a private, queue-triggered Vercel Function (D-98).

Declared under ``[[tool.vercel.subscribers]]`` in ``pyproject.toml``; Vercel compiles this module's
subscriber into a function that only Vercel Queues can invoke. The backend publishes
``{"run_id": …}`` when a run is created (``QueueRunManager``); this runs it within the function's
time limit (300 s on Hobby, so keep ``RUN_TIMEOUT_SECONDS`` below about 280).

The app container (database, MCP servers, model client) is built once per instance and reused by
later deliveries on the same event loop.
"""

# Importing this module runs vercel_backend/__init__.py first (import paths, Vercel marker).
from __future__ import annotations

import asyncio
import logging
import os

from vercel.queue import Message, subscribe

logger = logging.getLogger("marketlens.queue")

TOPIC = os.environ.get("RESEARCH_QUEUE_TOPIC", "marketlens-research-runs")

_container = None
_container_loop: asyncio.AbstractEventLoop | None = None
_locks: dict[int, asyncio.Lock] = {}


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

            settings = load_settings()
            logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            container = build_container(settings)
            initialize_database(container.db)
            await container.gateway.start()  # type: ignore[attr-defined]
            _container, _container_loop = container, loop
    return _container


@subscribe(topic=TOPIC, max_attempts=3, retry_after=30)
async def run_research(message: Message[dict[str, str]]) -> None:
    from app.services.run_consumer import run_queued_research

    run_id = str(message.payload.get("run_id", ""))
    if not run_id:
        logger.warning("ignored a queue message without a run_id")
        return
    container = await get_container()
    outcome = await run_queued_research(
        container.workflow, container.runs, run_id, delivery_count=message.metadata.delivery_count
    )
    logger.info("research run %s: %s", run_id, outcome)
