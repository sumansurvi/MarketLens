"""Period comparison: two deterministic price-change calculations through the MCP gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.api.schemas import ComparePeriodsRequest, ComparePeriodsResponse, ErrorResponse, PeriodChange
from app.container import AppContainer
from app.services.compare import compare_periods

router = APIRouter(prefix="/api/compare", tags=["research"])
ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


@router.post("", response_model=ComparePeriodsResponse, responses=ERRORS)
async def compare(
    body: ComparePeriodsRequest, container: AppContainer = Depends(get_container)
) -> ComparePeriodsResponse:
    """Percentage change for two periods, with the first and last trading days actually used."""
    result = await compare_periods(container.gateway, body.symbol, [(p.start_date, p.end_date) for p in body.periods])
    available = sum(1 for p in result.periods if p.change.percentage_change is not None)
    try:
        container.analytics.record(
            "periods_compared", data_mode="fixture" if result.is_fixture else "live", periods_available=available
        )
    except Exception:  # noqa: BLE001, S110 - analytics never breaks research
        pass
    return ComparePeriodsResponse(
        symbol=result.symbol,
        company_name=result.company_name,
        periods=[PeriodChange(label=p.label, calculation=p.change) for p in result.periods],
        difference_percentage_points=float(result.difference_percentage_points)
        if result.difference_percentage_points is not None
        else None,
        difference_note=result.difference_note,
        is_fixture=result.is_fixture,
        evidence=result.evidence,
    )
