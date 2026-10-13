"""Background watcher: tell the user when long-running research finishes.

Research keeps running in the backend regardless of speech, interruptions or reconnects.
The watcher polls the backend for the active run, gives at most one "still working" update,
and asks the agent to summarize (via ``get_brief_summary``) once the run is terminal. It
never generates content itself; it only schedules agent replies with fixed instructions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from marketlens_voice.backend import BackendError
from marketlens_voice.prompts import COMPLETION_INSTRUCTIONS, FAILURE_INSTRUCTIONS, PROGRESS_INSTRUCTIONS
from marketlens_voice.tools import ResearchVoiceTools

logger = logging.getLogger("marketlens.voice")


class ReplySession(Protocol):
    def generate_reply(self, *, instructions: str) -> Any: ...


class ProgressWatcher:
    def __init__(
        self,
        session: ReplySession,
        tools: ResearchVoiceTools,
        *,
        poll_seconds: float = 2.0,
        update_after_seconds: float = 20.0,
        give_up_after_seconds: float = 600.0,
    ) -> None:
        self.session = session
        self.tools = tools
        self.poll_seconds = poll_seconds
        self.update_after_seconds = update_after_seconds
        self.give_up_after_seconds = give_up_after_seconds
        self._task: asyncio.Task[None] | None = None
        self._run_id: str | None = None

    def watch(self, run_id: str) -> None:
        """Start (or keep) watching ``run_id``; a new run replaces the previous watch."""
        if self._run_id == run_id and self._task and not self._task.done():
            return
        self.cancel()
        self._run_id = run_id
        self._task = asyncio.create_task(self._loop(run_id), name=f"marketlens-watch-{run_id}")

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self, run_id: str) -> None:
        started = time.monotonic()
        updated = False
        failures = 0
        while time.monotonic() - started < self.give_up_after_seconds:
            await asyncio.sleep(self.poll_seconds)
            try:
                status = await self.tools.backend.status(run_id)
            except BackendError as exc:
                failures += 1
                logger.warning("status poll failed for %s: %s", run_id, exc.code)
                if failures >= 5 or exc.code in ("VOICE_SESSION_EXPIRED", "NOT_FOUND"):
                    return
                continue
            failures = 0
            if status.get("is_terminal"):
                if status["status"] in ("completed", "partial"):
                    self.session.generate_reply(instructions=COMPLETION_INSTRUCTIONS.format(status=status["status"]))
                else:
                    self.session.generate_reply(
                        instructions=FAILURE_INSTRUCTIONS.format(
                            status=status["status"], reason=status.get("status_sentence", "")
                        )
                    )
                return
            if not updated and time.monotonic() - started >= self.update_after_seconds:
                updated = True
                self.session.generate_reply(
                    instructions=PROGRESS_INSTRUCTIONS.format(stage=status.get("stage_label", "working"))
                )
