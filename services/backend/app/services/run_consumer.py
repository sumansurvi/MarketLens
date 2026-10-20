"""Executes one research run delivered by Vercel Queues (RUN_EXECUTION=queue, D-98).

Delivery is at least once, so the run is claimed atomically first: only the delivery that moves it
from queued to running executes it. A later delivery of the same run (after the function was cut
off mid-run) marks it failed instead of running it twice. While the workflow runs, the run row is
watched for a cancel request from the web app, which may be served by another instance.
"""

from __future__ import annotations

import asyncio
import logging

from marketlens_core.db.repositories import RunRepository
from marketlens_core.models import RunStatus

from app.services.research_workflow import ResearchWorkflowService

logger = logging.getLogger("marketlens.runs")

CANCEL_POLL_SECONDS = 1.0


async def run_queued_research(
    workflow: ResearchWorkflowService,
    runs: RunRepository,
    run_id: str,
    *,
    delivery_count: int = 1,
    poll_seconds: float = CANCEL_POLL_SECONDS,
) -> str:
    """Run ``run_id`` if this delivery claims it. Returns what happened, for logs and tests."""
    if not runs.claim_queued_run(run_id):
        run = runs.get_run(run_id)
        if run is not None and run.status == RunStatus.RUNNING and delivery_count > 1:
            runs.fail_run(run_id, "The research run was interrupted before it finished. Try again.")
            return "interrupted"
        return "skipped"

    task = asyncio.create_task(workflow.execute(run_id), name=f"research:{run_id}")
    cancelled_by_request = False
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=poll_seconds)
            if task.done():
                break
            current = runs.get_run(run_id)
            if current is not None and current.cancel_requested and not cancelled_by_request:
                cancelled_by_request = True
                task.cancel()
        await task
    except asyncio.CancelledError:
        if not cancelled_by_request:
            task.cancel()  # the function itself is being stopped: stop the run with it
            raise
        return "cancelled"
    return "completed"
