"""Worker wiring: explicit dispatch name, session pipeline from configuration, launcher checks."""

from __future__ import annotations

import os

import pytest
from livekit.agents import AgentServer, AgentSession
from marketlens_voice import __main__ as launcher


@pytest.mark.anyio
async def test_worker_registers_explicit_dispatch_and_builds_the_configured_pipeline() -> None:
    from marketlens_voice import worker

    assert isinstance(worker.server, AgentServer)
    assert worker.server._entrypoint_fnc is worker.entrypoint  # module-level: picklable for job processes
    assert worker.server._agent_name == "marketlens-voice-agent"
    session = worker.build_session(worker.SETTINGS)
    assert isinstance(session, AgentSession)
    assert (session.stt.model, session.llm.model, session.tts.model) == (
        "test/stt-model",
        "test/llm-model",
        "test/tts-model",
    )


def test_launcher_rejects_unknown_modes_and_missing_configuration(monkeypatch, capsys) -> None:
    assert launcher.main(["console"]) == 2
    monkeypatch.delenv("VOICE_LLM_MODEL")
    monkeypatch.setenv("VOICE_MODE", "disabled")
    assert launcher.main(["dev"]) == 2
    err = capsys.readouterr().err
    assert "VOICE_MODE=livekit is required" in err and "VOICE_LLM_MODEL" in err
    assert os.environ["LIVEKIT_API_SECRET"] not in err


@pytest.mark.parametrize("mode", ["dev", "start"])
def test_launcher_hands_over_to_the_livekit_entry(monkeypatch, mode: str) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("livekit.agents.__main__.main", lambda argv: seen.append(argv) or 0)
    assert launcher.main([mode]) == 0
    (argv,) = seen
    assert argv[0] == "start" and argv[1].endswith("marketlens_voice/worker.py")
    assert ("--dev" in argv) is (mode == "dev")


def test_pipeline_errors_map_to_fixed_codes() -> None:
    from livekit.agents.voice.events import STTError, TTSError
    from marketlens_voice.worker import VOICE_ERROR_ATTRIBUTE, pipeline_error_code

    assert VOICE_ERROR_ATTRIBUTE == "marketlens.voice_error"
    assert pipeline_error_code(STTError.model_construct()) == "speech_recognition_unavailable"
    assert pipeline_error_code(TTSError.model_construct()) == "speech_output_unavailable"
    assert pipeline_error_code(RuntimeError("x")) == "voice_pipeline_error"
