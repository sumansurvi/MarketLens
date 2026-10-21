"""Research run routes: create (async), poll, list, search and cancel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.models import TERMINAL_RUN_STATUSES, ResearchRequest, ResearchRun, TimeHorizonInput

from app.api.deps import get_container, get_session_id, valid_run_id
from app.api.schemas import (
    CreateResearchResponse,
    ErrorResponse,
    ResearchRunDetailResponse,
    RunListResponse,
    RunSearchResponse,
    RunSearchResult,
)
from app.container import AppContainer

router = APIRouter(prefix="/api/research", tags=["research"])


def _live_only(container: AppContainer) -> bool:
    """In live mode the site never shows runs made from fixture (demo) data: D-104.

    They stay in the database; they are only left out of what the site reads."""
    return container.settings.data_mode == "live"


ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateResearchResponse, responses=ERRORS)
async def create_research(
    request: ResearchRequest,
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> CreateResearchResponse:
    """Validate the request synchronously, then run the research workflow in the background."""
    run = await container.workflow.create_run(request, session_id=session_id, origin="text")
    if run.status.value == "queued":
        await container.run_manager.start(run.run_id)
    return CreateResearchResponse(run=run, poll_url=f"/api/research/{run.run_id}")


@router.get("", response_model=RunListResponse)
async def list_research(
    limit: int = Query(default=20, ge=1, le=100),
    symbol: str | None = Query(default=None, max_length=16),
    container: AppContainer = Depends(get_container),
) -> RunListResponse:
    from marketlens_core.symbols import normalize_symbol

    normalized = normalize_symbol(symbol) if symbol else None
    container.run_manager.sweep_stale()
    return RunListResponse(
        runs=container.runs.list_runs(limit=limit, symbol=normalized, live_only=_live_only(container))
    )


# Declared before /{run_id} so "search" is not taken for a run ID.
@router.get("/search", response_model=RunSearchResponse)
async def search_research(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=8, ge=1, le=20),
    container: AppContainer = Depends(get_container),
) -> RunSearchResponse:
    """Find past research by ticker, company name, question or the text of its kept findings."""
    hits = container.runs.search_runs(q, limit=limit, live_only=_live_only(container))
    return RunSearchResponse(
        query=q,
        results=[RunSearchResult(run=h.run, matched=h.matched, snippet=h.snippet) for h in hits],
    )


@router.get("/{run_id}", response_model=ResearchRunDetailResponse, responses=ERRORS)
async def get_research(
    run_id: str = Depends(valid_run_id),
    container: AppContainer = Depends(get_container),
) -> ResearchRunDetailResponse:
    container.run_manager.sweep_stale()
    if _live_only(container):
        stored = container.runs.get_run(run_id)
        if stored is not None and stored.data_mode != "live":
            # A demo run from before live data was configured is not shown in live mode (D-104).
            raise MarketLensError(ErrorCode.NOT_FOUND, "Research run not found.")
    detail = container.workflow.get_run_detail(run_id)
    active = container.run_manager.is_active(run_id)
    return ResearchRunDetailResponse(
        run=detail.run,
        steps=detail.steps,
        tool_calls=detail.tool_calls,
        source_status=detail.source_status,
        evidence=detail.evidence,
        brief=detail.brief,
        brief_payload_hash=detail.brief_payload_hash,
        is_active=active,
        poll_after_ms=None if detail.run.status in TERMINAL_RUN_STATUSES else 800,
    )


@router.post("/{run_id}/cancel", response_model=ResearchRun, responses=ERRORS)
async def cancel_research(
    run_id: str = Depends(valid_run_id),
    container: AppContainer = Depends(get_container),
) -> ResearchRun:
    run = container.runs.get_run(run_id)
    if run is None:
        raise MarketLensError(ErrorCode.NOT_FOUND, "Research run not found.")
    if run.status in TERMINAL_RUN_STATUSES:
        raise MarketLensError(
            ErrorCode.RUN_NOT_CANCELLABLE, f"The run is already {run.status.value}; nothing to cancel."
        )
    container.runs.update_run(run_id, cancel_requested=True)
    cancelled = await container.run_manager.cancel(run_id)
    if not cancelled:
        # No live task (e.g. queued but never started): persist the cancellation directly.
        from marketlens_core.models import RunStage, RunStatus
        from marketlens_core.timeutil import utc_now

        container.runs.update_run(run_id, status=RunStatus.CANCELLED, stage=RunStage.DONE, completed_at=utc_now())
    updated = container.runs.get_run(run_id)
    assert updated is not None
    return updated


@router.post("/{run_id}/execute", response_model=ResearchRun, responses=ERRORS)
async def execute_research(
    run_id: str = Depends(valid_run_id),
    container: AppContainer = Depends(get_container),
) -> ResearchRun:
    """Run a queued run inside this request (RUN_EXECUTION=request, D-103).

    The page calls this right after starting a run, and polls the run while the call is open. The
    answer is the run as it stands when execution ends. In the other modes the run is already
    executing elsewhere, so this only returns its current state."""
    if container.runs.get_run(run_id) is None:
        raise MarketLensError(ErrorCode.NOT_FOUND, "Research run not found.")
    await container.run_manager.execute(run_id)
    updated = container.runs.get_run(run_id)
    assert updated is not None
    return updated


@router.post(
    "/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED, response_model=CreateResearchResponse, responses=ERRORS
)
async def rerun_research(
    run_id: str = Depends(valid_run_id),
    container: AppContainer = Depends(get_container),
    session_id: str = Depends(get_session_id),
) -> CreateResearchResponse:
    """Run the same research again as a new run; the original run and any saved brief are unchanged.

    Same symbol, question and focus areas. A default window stays the default (resolved against
    the current reference date); an explicit window is reused exactly."""
    original = container.runs.get_run(run_id)
    if original is None:
        raise MarketLensError(ErrorCode.NOT_FOUND, "Research run not found.")
    if original.status not in TERMINAL_RUN_STATUSES:
        raise MarketLensError(
            ErrorCode.RUN_IN_PROGRESS, "This run is still in progress; wait for it to finish before running it again."
        )
    horizon = original.time_horizon
    request = ResearchRequest(
        symbol=original.symbol,
        question=original.question,
        time_horizon=None
        if horizon.is_default
        else TimeHorizonInput(start_date=horizon.start_date, end_date=horizon.end_date),
        focus_areas=original.focus_areas,
    )
    run = await container.workflow.create_run(request, session_id=session_id, origin="rerun", parent_run_id=run_id)
    if run.status.value == "queued":
        await container.run_manager.start(run.run_id)
    return CreateResearchResponse(run=run, poll_url=f"/api/research/{run.run_id}")
