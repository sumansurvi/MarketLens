"""Local product analytics: browser-reported voice connection events and an aggregate summary."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_container
from app.api.schemas import AnalyticsEventRequest, AnalyticsSummary
from app.container import AppContainer

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def record_event(body: AnalyticsEventRequest, container: AppContainer = Depends(get_container)) -> Response:
    """Record an allowlisted browser event (voice connection outcome only)."""
    properties = {"reason": body.reason} if body.reason else {}
    container.analytics.record(body.event, **properties)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/summary", response_model=AnalyticsSummary)
async def summary(container: AppContainer = Depends(get_container)) -> AnalyticsSummary:
    """Counts and rates stored in the local database; no personal data, free text or secrets."""
    return AnalyticsSummary.model_validate(container.analytics.summary())
