"""Starts research runs and tracks them so they can be cancelled and shut down cleanly.

Three ways to execute a run (``RUN_EXECUTION``):

- ``RunManager`` (inline, local): an asyncio task in the backend process.
- ``RequestRunManager`` (request, Vercel, D-103): the start request only records the run as
  queued; the browser then calls ``execute``, which runs it inside that request. The page follows
  the run's row by polling. Nothing else has to be deployed or delivered.
- ``QueueRunManager`` (queue, D-98): the backend publishes the run to Vercel Queues and a
  queue-triggered function runs it (``app.services.run_consumer``). Not used on Vercel since D-103.

A Vercel Function can't keep working after it has answered, and several instances share nothing but
the Postgres database, so the database holds each run's state and whatever executes it watches it
for cancellation.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from marketlens_core.config import Settings
from marketlens_core.db.repositories import RunRepository
from marketlens_core.errors import ErrorCode
from marketlens_core.models import TERMINAL_RUN_STATUSES, RunStatus
from marketlens_core.timeutil import utc_now

from app.services.research_workflow import ResearchWorkflowService

logger = logging.getLogger("marketlens.runs")

# Longer than the queue client's own 10 s request timeout, so a slow publish is reported by the client first.
# The page waits for this answer, so it must not run on (D-102).
PUBLISH_TIMEOUT_SECONDS = 15.0

# A queued or running run older than its time limit plus this margin can no longer finish.
STALE_MARGIN_SECONDS = 120


class RunManager:
    def __init__(self, workflow: ResearchWorkflowService) -> None:
        self.workflow = workflow
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, run_id: str) -> None:
        if run_id in self._tasks:
            return
        task = asyncio.create_task(self.workflow.execute(run_id), name=f"research:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    async def execute(self, run_id: str) -> None:
        """Inline runs are already executing (started with the run); nothing to do here."""

    def sweep_stale(self) -> None:
        """Inline runs live in this process; a restart marks them interrupted instead."""

    async def cancel(self, run_id: str, *, wait_seconds: float = 3.0) -> bool:
        """Cancel an in-flight run and wait briefly for it to persist its cancelled state."""
        task = self._tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_seconds)
        except (asyncio.CancelledError, TimeoutError):
            pass
        return True

    async def wait(self, run_id: str, wait_seconds: float = 30.0) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_seconds)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class QueueRunManager:
    """Publishes runs to Vercel Queues; the queue-triggered consumer executes them."""

    def __init__(self, settings: Settings, runs: RunRepository, *, queue_client: object | None = None) -> None:
        self.settings = settings
        self.runs = runs
        self._client = queue_client

    def _queue(self) -> object:
        if self._client is None:
            from vercel.queue import QueueClient

            self._client = QueueClient()
        return self._client

    async def start(self, run_id: str) -> None:
        """Publish before answering: on Vercel nothing runs after the response has been sent.

        Bounded: a publish that hangs fails the run at once with a reason, instead of leaving the
        page waiting (D-102).
        """
        topic = self.settings.research_queue_topic
        try:
            await asyncio.wait_for(
                self._queue().send(topic, {"run_id": run_id}, idempotency_key=run_id),  # type: ignore[attr-defined]
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - any queue failure (or timeout) leaves a clear, failed run
            logger.exception("could not publish research run %s to %s", run_id, topic)
            self.runs.fail_run(
                run_id,
                "The research run couldn't be queued. Try again in a moment.",
                code=ErrorCode.RESEARCH_QUEUE_UNAVAILABLE,
                retryable=True,
            )
            return
        logger.info("research run %s published to %s", run_id, topic)

    def is_active(self, run_id: str) -> bool:
        run = self.runs.get_run(run_id)
        return run is not None and run.status in (RunStatus.QUEUED, RunStatus.RUNNING)

    async def execute(self, run_id: str) -> None:
        """The queue's consumer executes the run; the request has nothing to do."""

    def sweep_stale(self) -> None:
        cutoff = utc_now() - dt.timedelta(seconds=self.settings.run_timeout_seconds + STALE_MARGIN_SECONDS)
        stopped = self.runs.fail_stale_runs(cutoff)
        if stopped:
            logger.warning("marked %d research run(s) that could no longer finish as failed", stopped)

    async def cancel(self, run_id: str, *, wait_seconds: float = 3.0) -> bool:
        """A running run is cancelled by its consumer, which watches ``cancel_requested``.

        Returns False for a run that hasn't started, so the caller marks it cancelled directly;
        the consumer then skips it because it is no longer queued.
        """
        run = self.runs.get_run(run_id)
        if run is None or run.status != RunStatus.RUNNING:
            return False
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            current = self.runs.get_run(run_id)
            if current is None or current.status in TERMINAL_RUN_STATUSES:
                break
        return True

    async def wait(self, run_id: str, wait_seconds: float = 30.0) -> None:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            run = self.runs.get_run(run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            await asyncio.sleep(0.25)
        raise TimeoutError(f"research run {run_id} did not finish within {wait_seconds} s")

    async def shutdown(self) -> None:
        """Runs belong to the queue, not to this process."""


class RequestRunManager(QueueRunManager):
    """Hosted without a queue (D-103): the browser's execute request runs each queued run.

    The start request records the run as queued and returns at once. The page then calls
    ``POST /api/research/{id}/execute``, which runs it in that request (up to the function's maximum
    duration; RUN_TIMEOUT_SECONDS stays below it) while the page polls the run's row. The run's
    claim is atomic, so a second execute request for the same run does nothing.
    """

    def __init__(self, settings: Settings, runs: RunRepository, workflow: ResearchWorkflowService) -> None:
        super().__init__(settings, runs)
        self.workflow = workflow

    async def start(self, run_id: str) -> None:
        """Nothing to publish: the page's execute request runs it."""

    async def execute(self, run_id: str) -> None:
        from app.services.run_consumer import run_queued_research

        outcome = await run_queued_research(self.workflow, self.runs, run_id)
        logger.info("research run %s executed in its request: %s", run_id, outcome)
