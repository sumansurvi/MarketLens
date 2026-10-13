"""LLM provider interface: structured-output generation with schema validation.

Agents describe *what* they need (task name, system prompt, evidence payload, output
schema); providers decide *how* to produce it. Model identifiers come only from
configuration. Every call returns metadata (model, latency, token usage when reported)
for the run trace. ``fixture_context`` is consumed only by the deterministic fixture
provider and is never sent to a real model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult(Generic[T]):
    output: T
    model: str | None
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    mode: str
    model: str | None

    async def generate_structured(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        output_model: type[T],
        max_output_tokens: int = 6000,
        fixture_context: Any = None,
    ) -> LLMResult[T]: ...
