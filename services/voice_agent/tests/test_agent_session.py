"""The agent inside a real LiveKit AgentSession (text mode), driven by a scripted LLM.

This checks the LiveKit wiring deterministically: which tools the model is offered, how tool
arguments are validated, what the model receives back (including errors) and that research is
started once. Conversational judgment (clarifying, refusing advice) needs a real model and is
covered by the credential-gated tests in ``tests/live``.
"""

from __future__ import annotations

import json

import pytest
from livekit.agents import AgentSession
from marketlens_core.policy import PROHIBITED_TOOL_FRAGMENTS, RESEARCH_ONLY_NOTICE, UNTRUSTED_CONTENT_RULES
from marketlens_voice.assistant import ResearchAssistant
from marketlens_voice.tools import TOOL_NAMES, ResearchVoiceTools

from tests.fakes import STARTED, FakeBackend, ScriptedLLM, fail, ok

pytestmark = pytest.mark.anyio


def assistant(backend: FakeBackend) -> ResearchAssistant:
    return ResearchAssistant(ResearchVoiceTools(backend.client()))


async def test_research_tool_runs_once_and_its_output_reaches_the_model() -> None:
    backend = FakeBackend()
    backend.on("POST", "/research", ok(STARTED, 202))
    args = {"symbol": "AAPL", "question": "How has Apple done lately?"}
    model = ScriptedLLM(("tool", "run_company_research", args), ("text", "I've started the research."))
    async with model, AgentSession(llm=model) as session:
        await session.start(assistant(backend))
        result = await session.run(user_input="Research Apple, ticker AAPL, over the default window.")
        result.expect.next_event().is_function_call(name="run_company_research", arguments=args)
        output = result.expect.next_event().is_function_call_output().event().item
        assert "Research started on Apple Inc. (AAPL)" in output.output and not output.is_error
        result.expect.next_event().is_message(role="assistant")
        result.expect.no_more_events()
    assert [r.url.path.rsplit("/", 1)[-1] for r in backend.requests] == ["research"]
    assert model.tool_names[0] == sorted(TOOL_NAMES)


async def test_backend_rejection_is_returned_to_the_model_as_a_tool_error() -> None:
    backend = FakeBackend()
    backend.on("POST", "/research", fail(422, "UNKNOWN_SYMBOL", "ZZZZ is not covered. Covered: AAPL, MSFT, NVDA."))
    model = ScriptedLLM(("tool", "run_company_research", {"symbol": "ZZZZ", "question": "What is ZZZZ doing?"}))
    async with model, AgentSession(llm=model) as session:
        await session.start(assistant(backend))
        result = await session.run(user_input="Research ZZZZ")
        result.expect.next_event().is_function_call(name="run_company_research")
        output = result.expect.next_event().is_function_call_output().event().item
        assert output.is_error and "do not guess another company" in output.output
    follow_up = json.dumps(model.contexts[-1].to_dict())
    assert "ZZZZ is not covered" in follow_up  # the model sees the reason and can tell the user


async def test_invalid_arguments_never_reach_the_backend() -> None:
    backend = FakeBackend()
    model = ScriptedLLM(("tool", "run_company_research", {"symbol": "AAPL", "question": "Long view", "horizon": "5y"}))
    async with model, AgentSession(llm=model) as session:
        await session.start(assistant(backend))
        result = await session.run(user_input="Research Apple over five years")
        result.expect.next_event().is_function_call(name="run_company_research")
        assert result.expect.next_event().is_function_call_output().event().item.is_error
    assert backend.requests == []


async def test_offered_tools_are_exactly_the_three_research_tools() -> None:
    tools = {t.id for t in assistant(FakeBackend()).tools}
    assert tools == set(TOOL_NAMES)
    for name in tools:
        assert not any(fragment in name for fragment in PROHIBITED_TOOL_FRAGMENTS), name
        assert not any(word in name for word in ("save", "watchlist", "write", "delete", "remove")), name


def test_instructions_carry_the_shared_policy() -> None:
    instructions = assistant(FakeBackend()).instructions
    assert RESEARCH_ONLY_NOTICE in instructions
    assert all(rule in instructions for rule in UNTRUSTED_CONTENT_RULES)
    for phrase in ("Never invent prices", "cannot give personal investment", "does not support", "cannot save briefs"):
        assert phrase in instructions, phrase
