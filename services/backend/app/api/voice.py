"""Voice routes.

Browser routes (cookie session): create a voice session (room-scoped LiveKit token), read its
state (the run the voice agent started) and end it. Worker routes (``/api/internal/voice``,
hidden from the public schema) require the bearer token derived from the LiveKit secret and
act only for one live room; they can start research and read status/summaries, nothing else.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.voice import token_matches
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_container, get_session_id
from app.api.schemas import ErrorResponse, VoiceSessionResponse, VoiceSessionState
from app.container import AppContainer
from app.services.voice_summary import Focus

router = APIRouter(prefix="/api/voice", tags=["voice"])
internal = APIRouter(prefix="/api/internal/voice", include_in_schema=False)
ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 429: {"model": ErrorResponse}}


def _state(container: AppContainer, record) -> VoiceSessionState:  # noqa: ANN001 - VoiceSessionRecord
    run = container.runs.get_run(record.active_run_id) if record.active_run_id else None
    return VoiceSessionState(
        room_name=record.room_name,
        expires_at=record.expires_at,
        ended=record.ended_at is not None,
        active_run_id=record.active_run_id,
        active_run_status=run.status.value if run else None,
    )


@router.post("/session", status_code=status.HTTP_201_CREATED, response_model=VoiceSessionResponse, responses=ERRORS)
async def create_voice_session(
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> VoiceSessionResponse:
    """Create a voice room for this browser session and return a short-lived, room-scoped token."""
    grant = container.voice.create_session(session_id)
    return VoiceSessionResponse(**grant.__dict__)


@router.get("/session/{room_name}", response_model=VoiceSessionState, responses=ERRORS)
async def get_voice_session(
    room_name: str,
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> VoiceSessionState:
    return _state(container, container.voice.session_for_browser(room_name, session_id))


@router.post("/session/{room_name}/end", response_model=VoiceSessionState, responses=ERRORS)
async def end_voice_session(
    room_name: str,
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> VoiceSessionState:
    return _state(container, container.voice.end_for_browser(room_name, session_id))


# ---------------------------------------------------------------------------- worker API
def require_worker(request: Request, container: AppContainer = Depends(get_container)) -> AppContainer:
    container.voice.require_enabled()
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.startswith("Bearer ") else None
    if not token_matches(container.settings, supplied):
        raise MarketLensError(ErrorCode.REQUEST_NOT_ALLOWED, "Voice worker authentication failed.")
    return container


class VoiceResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    question: str = Field(min_length=3, max_length=1000)
    horizon: Literal["1m", "3m", "6m", "1y"] | None = None


@internal.post("/rooms/{room_name}/research", status_code=status.HTTP_202_ACCEPTED)
async def worker_start_research(
    room_name: str, body: VoiceResearchRequest, container: AppContainer = Depends(require_worker)
) -> dict[str, object]:
    run, reused = await container.voice.start_research(
        room_name, symbol=body.symbol, question=body.question, horizon=body.horizon
    )
    view = container.voice.status(room_name, run.run_id)
    return {**view, "already_running": reused, "window_is_default": run.time_horizon.is_default}


@internal.get("/rooms/{room_name}/status")
async def worker_run_status(
    room_name: str,
    run_id: str | None = Query(default=None, pattern=r"^run_[0-9a-f]{16}$"),
    container: AppContainer = Depends(require_worker),
) -> dict[str, object]:
    return container.voice.status(room_name, run_id)


@internal.get("/rooms/{room_name}/summary")
async def worker_brief_summary(
    room_name: str,
    run_id: str | None = Query(default=None, pattern=r"^run_[0-9a-f]{16}$"),
    focus: Focus = Query(default="overview"),
    container: AppContainer = Depends(require_worker),
) -> dict[str, object]:
    return container.voice.summary(room_name, run_id, focus)


@internal.post("/rooms/{room_name}/end", status_code=status.HTTP_204_NO_CONTENT)
async def worker_end_session(room_name: str, container: AppContainer = Depends(require_worker)) -> None:
    container.voice.end_for_worker(room_name)
