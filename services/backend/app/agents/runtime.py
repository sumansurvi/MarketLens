"""Agent runtime: per-run LLM call and time budgets, timeouts, bounded retries and call metadata.

The time budget is the run's deadline: a model call never outlives it, and a step that
cannot start in time fails fast (the workflow then uses its labeled deterministic
fallback) instead of letting the whole run hit its hard timeout."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from marketlens_core.errors import ErrorCode, MarketLensError
from pydantic import BaseModel, ValidationError

from app.providers.llm.base import LLMProvider

logger = logging.getLogger("marketlens.agents")
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMCallRecord:
    task: str
    attempt: int
    status: str
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class AgentRuntime:
    llm: LLMProvider
    max_calls: int
    timeout_seconds: float
    # Margin over the provider's own HTTP timeout before the runtime gives up on a call.
    grace_seconds: float = 5.0
    # ``time.monotonic()`` value after which no model call may run (None = no deadline).
    deadline: float | None = None
    min_call_seconds: float = 3.0
    calls: list[LLMCallRecord] = field(default_factory=list)

    @property
    def calls_made(self) -> int:
        return len(self.calls)

    @property
    def mode(self) -> str:
        return self.llm.mode

    @property
    def model(self) -> str | None:
        return self.llm.model

    def records_for(self, task: str) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.calls if c.task == task]

    async def run(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        output_model: type[T],
        fixture_context: Any = None,
        retries: int = 1,
    ) -> T:
        """One structured LLM task with a hard call budget; raises ``MarketLensError`` on failure."""
        last: MarketLensError | None = None
        for attempt in range(1, retries + 2):
            if self.calls_made >= self.max_calls:
                raise MarketLensError(
                    ErrorCode.BUDGET_EXCEEDED,
                    f"The per-run language-model budget ({self.max_calls} calls) was exhausted before '{task}'.",
                )
            timeout = self.timeout_seconds + self.grace_seconds
            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining < self.min_call_seconds:
                    raise MarketLensError(
                        ErrorCode.BUDGET_EXCEEDED,
                        f"Not enough of the run's time budget was left to run '{task}'.",
                        retryable=False,
                    )
                timeout = min(timeout, remaining)
            record = LLMCallRecord(task=task, attempt=attempt, status="error")
            self.calls.append(record)
            try:
                result = await asyncio.wait_for(
                    self.llm.generate_structured(
                        task=task,
                        system_prompt=system_prompt,
                        payload=payload,
                        output_model=output_model,
                        fixture_context=fixture_context,
                    ),
                    timeout=timeout,
                )
                output = output_model.model_validate(result.output.model_dump())
            except TimeoutError:
                last = MarketLensError(
                    ErrorCode.LLM_UNAVAILABLE, f"The language model timed out on '{task}'.", retryable=True
                )
            except ValidationError:
                last = MarketLensError(
                    ErrorCode.LLM_UNAVAILABLE,
                    f"The language model returned output for '{task}' that failed schema validation.",
                    retryable=True,
                )
            except MarketLensError as exc:
                last = exc
            else:
                record.status = "ok"
                record.model = result.model
                record.latency_ms = result.latency_ms
                record.input_tokens = result.input_tokens
                record.output_tokens = result.output_tokens
                return output
            record.error_code = last.code.value
            logger.warning("LLM task %s attempt %d failed: %s", task, attempt, last.code.value)
            if not last.retryable:
                break
        assert last is not None
        raise last
