"""Tracks background research tasks so runs can be cancelled and shut down cleanly."""

from __future__ import annotations

import asyncio
import logging

from app.services.research_workflow import ResearchWorkflowService

logger = logging.getLogger("marketlens.runs")


class RunManager:
    def __init__(self, workflow: ResearchWorkflowService) -> None:
        self.workflow = workflow
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, run_id: str) -> None:
        if run_id in self._tasks:
            return
        task = asyncio.create_task(self.workflow.execute(run_id), name=f"research:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

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
