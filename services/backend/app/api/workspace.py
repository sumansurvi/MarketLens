"""Saved briefs and watchlist routes. Reads and writes go through the workspace MCP server;
writes are proposals that only take effect after an explicit confirmation (see actions.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from marketlens_core.models import ActionType
from marketlens_core.symbols import normalize_symbol

from app.api.deps import get_container, get_session_id, valid_brief_id
from app.api.schemas import (
    BriefListResponse,
    ErrorResponse,
    PendingActionResponse,
    SavedBriefResponse,
    WatchlistProposalRequest,
    WatchlistResponse,
)
from app.container import AppContainer

router = APIRouter(prefix="/api", tags=["workspace"])
ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.get("/briefs", response_model=BriefListResponse, responses=ERRORS)
async def list_briefs(
    limit: int = Query(default=20, ge=1, le=50),
    symbol: str | None = Query(default=None, max_length=16),
    container: AppContainer = Depends(get_container),
) -> BriefListResponse:
    return BriefListResponse(briefs=await container.workspace.saved_briefs(limit=limit, symbol=symbol))


@router.get("/briefs/{brief_id}", response_model=SavedBriefResponse, responses=ERRORS)
async def get_brief(
    brief_id: str = Depends(valid_brief_id),
    container: AppContainer = Depends(get_container),
) -> SavedBriefResponse:
    return SavedBriefResponse(saved=await container.workspace.saved_brief(brief_id))


@router.get("/watchlist", response_model=WatchlistResponse, responses=ERRORS)
async def get_watchlist(container: AppContainer = Depends(get_container)) -> WatchlistResponse:
    return WatchlistResponse(items=await container.workspace.watchlist())


@router.post("/watchlist", status_code=status.HTTP_202_ACCEPTED, response_model=PendingActionResponse, responses=ERRORS)
async def propose_watchlist_add(
    body: WatchlistProposalRequest,
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> PendingActionResponse:
    """Creates a pending add; nothing changes until the user confirms it."""
    action = await container.workspace.propose(
        action_type=ActionType.ADD_WATCHLIST_SYMBOL, session_id=session_id, symbol=body.symbol
    )
    return PendingActionResponse(action=action)


@router.delete(
    "/watchlist/{symbol}", status_code=status.HTTP_202_ACCEPTED, response_model=PendingActionResponse, responses=ERRORS
)
async def propose_watchlist_remove(
    symbol: str,
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> PendingActionResponse:
    """Creates a pending removal; nothing changes until the user confirms it."""
    action = await container.workspace.propose(
        action_type=ActionType.REMOVE_WATCHLIST_SYMBOL, session_id=session_id, symbol=normalize_symbol(symbol)
    )
    return PendingActionResponse(action=action)
