"""Deterministic stand-ins: a scripted LLM for AgentSession and a fake backend."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from livekit.agents import APIConnectOptions, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from marketlens_voice.backend import VoiceBackendClient

ROOM = "ml-voice-" + "R" * 24


class ScriptedLLM(llm.LLM):
    """Each chat() call plays the next step: ("tool", name, args) or ("text", content)."""

    def __init__(self, *steps: tuple[Any, ...]) -> None:
        super().__init__()
        self.steps = list(steps)
        self.tool_names: list[list[str]] = []
        self.contexts: list[llm.ChatContext] = []

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **_: Any,
    ) -> llm.LLMStream:
        self.contexts.append(chat_ctx.copy())
        self.tool_names.append(sorted(getattr(t, "id", "") for t in tools or []))
        step = self.steps.pop(0) if self.steps else ("text", "Okay.")
        return _ScriptedStream(self, step, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class _ScriptedStream(llm.LLMStream):
    def __init__(self, owner: ScriptedLLM, step: tuple[Any, ...], **kwargs: Any) -> None:
        super().__init__(owner, **kwargs)
        self._step = step

    async def _run(self) -> None:
        if self._step[0] == "tool":
            _, name, args = self._step
            call = llm.FunctionToolCall(name=name, arguments=json.dumps(args), call_id=f"call_{name}")
            delta = llm.ChoiceDelta(role="assistant", tool_calls=[call])
        else:
            delta = llm.ChoiceDelta(role="assistant", content=self._step[1])
        self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=delta))


class FakeBackend:
    """Routes internal voice API calls to handlers; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def on(self, method: str, path: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.routes[(method, path)] = handler

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        suffix = request.url.path.removeprefix(f"/api/internal/voice/rooms/{ROOM}")
        handler = self.routes.get((request.method, suffix))
        return (
            handler(request)
            if handler
            else httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "No route."}})
        )

    def client(self) -> VoiceBackendClient:
        transport = httpx.MockTransport(self.handle)
        http = httpx.AsyncClient(base_url="http://backend.test", transport=transport)
        return VoiceBackendClient("http://backend.test", "worker-token", ROOM, client=http)


def ok(payload: dict[str, Any], status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=payload)


def fail(status: int, code: str, message: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(
        status, json={"error": {"code": code, "message": message, "retryable": False}}
    )


STARTED = {
    "run_id": "run_0123456789abcdef",
    "symbol": "AAPL",
    "company_name": "Apple Inc.",
    "status": "queued",
    "is_terminal": False,
    "stage": "created",
    "stage_label": "getting started",
    "current_step": None,
    "steps_completed": 0,
    "elapsed_seconds": 0,
    "status_sentence": "The research on Apple Inc. is still running.",
    "partial_reasons": [],
    "data_mode": "fixture",
    "already_running": False,
    "window_is_default": True,
}
