"""Deterministic fixture LLM: returns known structured payloads without any network call.

Responders (``app.agents.fixture_responses``) build each task's output from the same
evidence the real model would receive, so runs are reproducible for tests and the offline
demo; every brief records ``llm_mode: fixture``. Evaluation cases can set ``fault_hook``
(rewrite an output) or ``failing_tasks`` (raise) to prove validation catches bad output.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from marketlens_core.errors import ErrorCode, MarketLensError
from pydantic import BaseModel

from app.providers.llm.base import LLMResult, T

Responder = Callable[[dict[str, Any], Any], BaseModel]
FaultHook = Callable[[str, BaseModel, Any], BaseModel]


class FixtureLLMProvider:
    mode = "fixture"
    model = "fixture-llm-v1"

    def __init__(self, *, fault_hook: FaultHook | None = None, failing_tasks: set[str] | None = None) -> None:
        from app.agents.fixture_responses import RESPONDERS

        self._responders: dict[str, Responder] = dict(RESPONDERS)
        self.fault_hook = fault_hook
        self.failing_tasks = set(failing_tasks or ())
        self.calls: list[str] = []

    def register(self, task: str, responder: Responder) -> None:
        self._responders[task] = responder

    async def generate_structured(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        output_model: type[T],
        max_output_tokens: int = 6000,
        fixture_context: Any = None,
    ) -> LLMResult[T]:
        started = time.monotonic()
        self.calls.append(task)
        if task in self.failing_tasks:
            raise MarketLensError(
                ErrorCode.LLM_UNAVAILABLE, f"Simulated model failure for task '{task}'.", retryable=True
            )
        responder = self._responders.get(task)
        if responder is None:
            raise MarketLensError(ErrorCode.LLM_UNAVAILABLE, f"No fixture response registered for task '{task}'.")
        output = responder(payload, fixture_context)
        if self.fault_hook is not None:
            output = self.fault_hook(task, output, fixture_context)
        validated = output_model.model_validate(output.model_dump())
        return LLMResult(output=validated, model=self.model, latency_ms=int((time.monotonic() - started) * 1000))
