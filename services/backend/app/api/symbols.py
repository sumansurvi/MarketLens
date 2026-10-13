"""Ticker suggestions for the research bar's dropdown, through the research-sources MCP server."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.tool_models import SymbolSearchData

from app.api.deps import get_container
from app.api.schemas import ErrorResponse, SymbolSearchResponse
from app.container import AppContainer

router = APIRouter(prefix="/api/symbols", tags=["research"])
ERRORS = {422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


@router.get("", response_model=SymbolSearchResponse, responses=ERRORS)
async def search_symbols(
    q: str = Query(
        default="",
        max_length=40,
        description="Start of a ticker or of a word in the company name; empty lists the largest covered companies.",
    ),
    limit: int = Query(default=8, ge=1, le=20),
    container: AppContainer = Depends(get_container),
) -> SymbolSearchResponse:
    """Suggestions only: research still resolves the exact ticker the user picks (D-15, D-82)."""
    query = q.strip()
    outcome = await container.gateway.call("research_sources", "search_symbols", {"query": query, "limit": limit})
    if not outcome.ok or outcome.error is not None:
        raise (
            MarketLensError.from_info(outcome.error)
            if outcome.error
            else MarketLensError(ErrorCode.INTERNAL_ERROR, "The ticker search failed.")
        )
    data = SymbolSearchData.model_validate(outcome.data)
    return SymbolSearchResponse(query=data.query, matches=data.matches, is_fixture=data.is_fixture)
