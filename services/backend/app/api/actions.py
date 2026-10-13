"""Pending write actions: propose → preview → confirm (one-time grant + MCP write) or cancel.

Only the session that proposed an action can see, confirm or cancel it. A client-supplied
``confirmed: true`` is not part of any request model and is rejected as an unknown field.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, status
from marketlens_core.errors import ErrorCode, MarketLensError

from app.api.deps import get_container, get_session_id
from app.api.schemas import ConfirmActionResponse, ErrorResponse, PendingActionResponse, ProposeActionRequest
from app.container import AppContainer

router = APIRouter(prefix="/api/actions", tags=["actions"])
ERRORS = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    410: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{16}$")


def valid_action_id(action_id: str) -> str:
    if not ACTION_ID_RE.fullmatch(action_id):
        raise MarketLensError(ErrorCode.NOT_FOUND, "Action not found.")
    return action_id


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PendingActionResponse, responses=ERRORS)
async def propose_action(
    body: ProposeActionRequest,
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> PendingActionResponse:
    action = await container.workspace.propose(
        action_type=body.action_type, session_id=session_id, run_id=body.run_id, symbol=body.symbol, title=body.title
    )
    return PendingActionResponse(action=action)


@router.get("/{action_id}", response_model=PendingActionResponse, responses=ERRORS)
async def get_action(
    action_id: str = Depends(valid_action_id),
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> PendingActionResponse:
    return PendingActionResponse(action=container.workspace.get(action_id, session_id))


@router.post("/{action_id}/confirm", response_model=ConfirmActionResponse, responses=ERRORS)
async def confirm_action(
    action_id: str = Depends(valid_action_id),
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> ConfirmActionResponse:
    action, result = await container.workspace.confirm(action_id, session_id)
    return ConfirmActionResponse(action=action, result=result)


@router.post("/{action_id}/cancel", response_model=PendingActionResponse, responses=ERRORS)
async def cancel_action(
    action_id: str = Depends(valid_action_id),
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> PendingActionResponse:
    return PendingActionResponse(action=container.workspace.cancel(action_id, session_id), requires_confirmation=False)
