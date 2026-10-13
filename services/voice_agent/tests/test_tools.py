"""Tool logic against a fake backend: request shape, wording guards and error mapping."""

from __future__ import annotations

import json

import httpx
import pytest
from marketlens_voice.tools import ResearchVoiceTools, VoiceToolError

from tests.fakes import ROOM, STARTED, FakeBackend, fail, ok

pytestmark = pytest.mark.anyio


async def test_start_research_calls_the_backend_once_with_scoped_credentials() -> None:
    backend = FakeBackend()
    backend.on("POST", "/research", ok(STARTED, 202))
    tools = ResearchVoiceTools(backend.client())
    text = await tools.run_company_research("aapl", "How has Apple done?", "6m")
    (request,) = backend.requests
    assert request.url.path == f"/api/internal/voice/rooms/{ROOM}/research"
    assert request.headers["authorization"] == "Bearer worker-token"
    assert request.headers["x-marketlens-client"] == "voice-worker"
    assert json.loads(request.content) == {"symbol": "aapl", "question": "How has Apple done?", "horizon": "6m"}
    assert tools.active_run_id == STARTED["run_id"]
    assert "Research started on Apple Inc. (AAPL) for the default three-month window" in text
    assert "Do not describe any results until get_brief_summary" in text


async def test_repeat_request_reports_the_running_run() -> None:
    backend = FakeBackend()
    backend.on("POST", "/research", ok({**STARTED, "already_running": True, "status": "running"}, 202))
    text = await ResearchVoiceTools(backend.client()).run_company_research("AAPL", "Again please")
    assert "already running; no new run was started" in text


@pytest.mark.parametrize(
    ("status", "code", "hint"),
    [
        (422, "UNKNOWN_SYMBOL", "do not guess another company"),
        (422, "INVALID_SYMBOL", "spell the ticker"),
        (410, "VOICE_SESSION_EXPIRED", "voice session has ended"),
        (503, "MCP_SERVER_UNAVAILABLE", "use the text research form"),
    ],
)
async def test_backend_errors_become_user_safe_tool_errors(status: int, code: str, hint: str) -> None:
    backend = FakeBackend()
    message = "This voice session has ended or expired." if code == "VOICE_SESSION_EXPIRED" else "Problem."
    backend.on("POST", "/research", fail(status, code, message))
    with pytest.raises(VoiceToolError) as err:
        await ResearchVoiceTools(backend.client()).run_company_research("ZZZZ", "What about this?")
    assert hint in err.value.message


async def test_unreachable_backend_is_reported_not_guessed() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    backend = FakeBackend()
    backend.on("GET", "/status", down)
    with pytest.raises(VoiceToolError) as err:
        await ResearchVoiceTools(backend.client()).get_research_run_status()
    assert "not reachable" in err.value.message and "text research form" in err.value.message


async def test_status_text_never_invites_results_while_running() -> None:
    backend = FakeBackend()
    running = {
        **STARTED,
        "status": "running",
        "stage_label": "collecting market data, filings and news",
        "elapsed_seconds": 7,
    }
    backend.on("GET", "/status", ok(running))
    tools = ResearchVoiceTools(backend.client())
    tools.active_run_id = STARTED["run_id"]
    text = await tools.get_research_run_status()
    assert "collecting market data, filings and news (7 seconds so far)" in text
    assert "Do not describe results yet" in text
    assert backend.requests[0].url.params["run_id"] == STARTED["run_id"]


async def test_partial_status_names_its_reasons() -> None:
    backend = FakeBackend()
    partial = {
        **STARTED,
        "status": "partial",
        "is_terminal": True,
        "status_sentence": "The research report on Apple Inc. is partial, so some parts are missing or qualified.",
        "partial_reasons": ["news: The provider timed out."],
    }
    backend.on("GET", "/status", ok(partial))
    text = await ResearchVoiceTools(backend.client()).get_research_run_status()
    assert "is partial" in text and "Partial because: news: The provider timed out." in text
    assert "Use get_brief_summary" in text


async def test_summary_text_carries_caveats_and_validated_sentences_only() -> None:
    backend = FakeBackend()
    summary = {
        "run_id": STARTED["run_id"],
        "status": "completed",
        "focus": "risks",
        "status_sentence": "The research report on Apple Inc. is complete.",
        "window": "June 30, 2026 to September 30, 2026 (the default three-month window)",
        "sentences": ["Risk factor quoted from the Form 10-Q filed July 31, 2026: “Supply constraints.”"],
        "caveats": ["These figures come from synthetic demo data, not real market data."],
        "follow_up_topics": ["financials", "market"],
        "details_on_screen": "The full report with sources and citations is on screen.",
        "is_fixture": True,
    }
    backend.on("GET", "/summary", ok(summary))
    text = await ResearchVoiceTools(backend.client()).get_brief_summary(focus="risks")
    assert text.splitlines()[0] == summary["status_sentence"]
    assert "Caveats to mention: These figures come from synthetic demo data" in text
    assert "do not add or change numbers" in text and "- Risk factor quoted" in text
    assert "Offer details on: financials, market." in text
    assert backend.requests[0].url.params["focus"] == "risks"


async def test_no_run_yet_suggests_starting_one() -> None:
    backend = FakeBackend()
    backend.on("GET", "/status", fail(404, "NOT_FOUND", "No research has been started in this voice session yet."))
    with pytest.raises(VoiceToolError) as err:
        await ResearchVoiceTools(backend.client()).get_research_run_status()
    assert "Offer to start research" in err.value.message
