"""LiveKit agent server for MarketLens voice (explicit dispatch by ``LIVEKIT_AGENT_NAME``).

Run with ``uv run marketlens-voice-agent dev`` (validates configuration first), or directly
with ``uv run python -m livekit.agents start marketlens_voice/worker.py --dev``.

Pipeline: LiveKit Inference speech-to-text, LLM and text-to-speech with the LiveKit turn
detector; model IDs come only from configuration. Session recording is disabled, and the
transcript is not persisted by MarketLens.

The entrypoint is a module-level function because LiveKit runs each job in a spawned
process and passes the entrypoint by reference; configuration is loaded at import.
"""

from __future__ import annotations

import asyncio
import logging

from livekit.agents import AgentServer, AgentSession, JobContext, TurnHandlingOptions, inference
from livekit.agents.voice.events import CloseEvent, CloseReason
from marketlens_core.config import Settings, load_settings, validate_voice_worker_settings
from marketlens_core.voice import internal_token

from marketlens_voice.assistant import ResearchAssistant
from marketlens_voice.backend import VoiceBackendClient
from marketlens_voice.progress import ProgressWatcher
from marketlens_voice.prompts import GREETING_INSTRUCTIONS
from marketlens_voice.tools import ResearchVoiceTools

logger = logging.getLogger("marketlens.voice")
MAX_TOOL_STEPS = 3
GREETING_WAIT_SECONDS = 10
# Participant attribute the browser reads to explain why the assistant stopped (a code, never text).
VOICE_ERROR_ATTRIBUTE = "marketlens.voice_error"
_ERROR_CODES = {
    "STTError": "speech_recognition_unavailable",
    "TTSError": "speech_output_unavailable",
    "LLMError": "voice_model_unavailable",
    "RealtimeModelError": "voice_model_unavailable",
}


_BACKGROUND: set[asyncio.Task[None]] = set()


def pipeline_error_code(error: object) -> str:
    return _ERROR_CODES.get(type(error).__name__, "voice_pipeline_error")


def load_worker_settings() -> Settings:
    settings = load_settings(validate=False)
    problems = validate_voice_worker_settings(settings)
    if problems:
        raise SystemExit("MarketLens voice worker is not configured:\n- " + "\n- ".join(problems))
    return settings


def _credentials(settings: Settings) -> dict[str, str]:
    assert settings.livekit_api_key and settings.livekit_api_secret
    return {
        "api_key": settings.livekit_api_key.get_secret_value(),
        "api_secret": settings.livekit_api_secret.get_secret_value(),
    }


def build_session(settings: Settings) -> AgentSession:
    credentials = _credentials(settings)
    tts_options = {"voice": settings.voice_tts_voice} if settings.voice_tts_voice else {}
    return AgentSession(
        stt=inference.STT(model=settings.voice_stt_model, language=settings.voice_language, **credentials),
        llm=inference.LLM(model=settings.voice_llm_model, **credentials),
        tts=inference.TTS(model=settings.voice_tts_model, **tts_options, **credentials),
        turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector(**credentials)),
        max_tool_steps=MAX_TOOL_STEPS,
    )


SETTINGS = load_worker_settings()
server = AgentServer(ws_url=SETTINGS.livekit_url, **_credentials(SETTINGS))


@server.rtc_session(agent_name=SETTINGS.livekit_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    token = internal_token(SETTINGS)
    assert token is not None
    ctx.log_context_fields = {"room": ctx.room.name}
    backend = VoiceBackendClient(SETTINGS.voice_backend_url, token, ctx.room.name)
    tools = ResearchVoiceTools(backend)
    session = build_session(SETTINGS)
    watcher = ProgressWatcher(session, tools)

    async def close() -> None:
        watcher.cancel()
        try:
            await backend.end()
        except Exception:  # noqa: BLE001 - best effort; the session also expires on its own
            logger.info("could not mark voice session %s ended", ctx.room.name)
        await backend.aclose()

    ctx.add_shutdown_callback(close)

    async def stop_on_error(code: str) -> None:
        # Tell the browser why (a fixed code), then leave the room instead of sitting there deaf.
        try:
            await ctx.room.local_participant.set_attributes({VOICE_ERROR_ATTRIBUTE: code})
        finally:
            ctx.shutdown(reason=f"voice pipeline error: {code}")

    @session.on("close")
    def _on_close(event: CloseEvent) -> None:
        if event.reason == CloseReason.ERROR:
            code = pipeline_error_code(event.error)
            logger.warning("voice session closed on an unrecoverable error (%s)", code)
            task = asyncio.create_task(stop_on_error(code))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)

    await session.start(agent=ResearchAssistant(tools, watcher), room=ctx.room, record=False)
    await ctx.connect()
    # Greet only once the browser is in the room (its audio is subscribed by then), so the first
    # words aren't spoken into an empty room and lost (D-88). Greet anyway after a short wait.
    try:
        await asyncio.wait_for(ctx.wait_for_participant(), timeout=GREETING_WAIT_SECONDS)
    except TimeoutError:
        logger.info("no participant within %ss; greeting anyway", GREETING_WAIT_SECONDS)
    session.generate_reply(instructions=GREETING_INSTRUCTIONS)
