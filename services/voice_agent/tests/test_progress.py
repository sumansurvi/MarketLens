"""The progress watcher speaks only through fixed instructions and never invents results."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from marketlens_voice.backend import BackendError
from marketlens_voice.progress import ProgressWatcher
from marketlens_voice.tools import ResearchVoiceTools

pytestmark = pytest.mark.anyio
RUN = "run_0123456789abcdef"


class RecordingSession:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    def generate_reply(self, *, instructions: str) -> None:
        self.instructions.append(instructions)


class SequenceBackend:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        self.calls += 1
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def running(stage: str = "collecting market data, filings and news") -> dict[str, Any]:
    return {"status": "running", "is_terminal": False, "stage_label": stage}


async def watch(backend: SequenceBackend, **options: float) -> RecordingSession:
    session = RecordingSession()
    tools = ResearchVoiceTools(backend)  # type: ignore[arg-type]
    watcher = ProgressWatcher(session, tools, poll_seconds=0.01, **options)
    watcher.watch(RUN)
    watcher.watch(RUN)  # a repeated watch for the same run does not start a second loop
    assert watcher._task is not None
    await asyncio.wait_for(watcher._task, 2)
    return session


async def test_one_progress_update_then_a_summary_request() -> None:
    backend = SequenceBackend(running(), running(), running(), {"status": "partial", "is_terminal": True})
    session = await watch(backend, update_after_seconds=0.0)
    progress, completion = session.instructions
    assert "still running (collecting market data, filings and news)" in progress
    assert "Do not describe any results" in progress
    assert "finished with status partial" in completion and "Call get_brief_summary" in completion


async def test_failed_run_is_announced_as_failed() -> None:
    failed = {"status": "failed", "is_terminal": True, "status_sentence": "The research on Apple Inc. failed: X"}
    session = await watch(SequenceBackend(failed), update_after_seconds=60)
    (only,) = session.instructions
    assert "ended with status failed" in only and "no report was produced" in only


async def test_expired_session_stops_quietly() -> None:
    session = await watch(SequenceBackend(BackendError("VOICE_SESSION_EXPIRED", "ended")), update_after_seconds=60)
    assert session.instructions == []


async def test_transient_errors_are_retried() -> None:
    blip = BackendError("BACKEND_UNAVAILABLE", "down", retryable=True)
    session = await watch(
        SequenceBackend(blip, blip, {"status": "completed", "is_terminal": True}), update_after_seconds=60
    )
    assert len(session.instructions) == 1 and "status completed" in session.instructions[0]
