"""Shared provider types: payload wrapper, evidence construction, freshness."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import content_hash, evidence_id_for
from marketlens_core.models import EvidenceItem, EvidenceKind, Freshness, FreshnessStatus, SourceMode


@dataclass
class ProviderPayload:
    """A provider response plus provenance. ``data`` is parsed JSON (or text for documents)."""

    data: Any
    source_mode: SourceMode
    retrieved_at: dt.datetime
    request_label: str
    fixture_meta: dict[str, Any] | None = None
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def is_fixture(self) -> bool:
        return self.source_mode == SourceMode.FIXTURE


FIXTURE_FAULT_CODES = {
    "timeout": (ErrorCode.PROVIDER_TIMEOUT, "The provider did not respond in time (simulated)."),
    "rate_limited": (ErrorCode.RATE_LIMITED, "The provider temporarily limited requests (simulated)."),
    "capability_unavailable": (ErrorCode.CAPABILITY_UNAVAILABLE, "This capability is not available (simulated)."),
    "auth_failed": (ErrorCode.PROVIDER_AUTH_FAILED, "The provider rejected the credentials (simulated)."),
    "not_found": (ErrorCode.DATA_UNAVAILABLE, "The provider has no data for this request (simulated)."),
    "server_error": (ErrorCode.DATA_UNAVAILABLE, "The provider returned a server error (simulated)."),
}


def fault_error(fault: dict[str, Any]) -> MarketLensError:
    kind = str(fault.get("type", ""))
    if kind not in FIXTURE_FAULT_CODES:
        return MarketLensError(ErrorCode.INTERNAL_ERROR, f"Unknown fixture fault type {kind!r}.")
    code, default_message = FIXTURE_FAULT_CODES[kind]
    retry_after = fault.get("retry_after_seconds")
    return MarketLensError(
        code,
        str(fault.get("message") or default_message),
        retryable=code in (ErrorCode.PROVIDER_TIMEOUT, ErrorCode.RATE_LIMITED) or kind == "server_error",
        retry_after_seconds=float(retry_after) if retry_after is not None else None,
        details={"simulated": True, "fault": kind},
    )


def source_name_for(provider_label: str, payload: ProviderPayload) -> str:
    """Human-facing source name that always discloses fixture provenance."""
    return f"{provider_label} (synthetic fixture)" if payload.is_fixture else provider_label


def build_evidence(
    *,
    kind: EvidenceKind,
    payload: ProviderPayload,
    provider: str,
    source_name: str,
    identity: dict[str, Any],
    content: Any,
    symbol: str | None,
    title: str | None = None,
    source_url: str | None = None,
    published_at: str | None = None,
    published_at_tz_known: bool | None = None,
    published_date: dt.date | None = None,
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    currency: str | None = None,
    units: str | None = None,
    excerpt: str | None = None,
    evidence_payload: dict[str, Any] | None = None,
    freshness: Freshness | None = None,
    flags: list[str] | None = None,
    notes: list[str] | None = None,
) -> EvidenceItem:
    """Construct an evidence item with a deterministic ID (decision D-13)."""
    digest = content_hash(content)
    evidence_id = evidence_id_for(
        kind,
        {"identity": identity, "content_hash": digest, "source_mode": payload.source_mode.value},
    )
    all_flags = list(flags or [])
    if payload.is_fixture and "synthetic_fixture" not in all_flags:
        all_flags.insert(0, "synthetic_fixture")
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        symbol=symbol,
        title=title,
        source_name=source_name,
        provider=provider,
        source_url=source_url,
        published_at=published_at,
        published_at_tz_known=published_at_tz_known,
        published_date=published_date,
        period_start=period_start,
        period_end=period_end,
        retrieved_at=payload.retrieved_at,
        currency=currency,
        units=units,
        source_mode=payload.source_mode,
        is_fixture=payload.is_fixture,
        content_hash=digest,
        excerpt=excerpt,
        payload=evidence_payload or {},
        freshness=freshness,
        flags=all_flags,
        notes=list(notes or []),
    )


# Filing deadlines (SEC): a 10-Q is due within 40-45 days of quarter end, a 10-K within 60-90
# days of fiscal year end. The fiscal fourth quarter has no 10-Q (it is inside the 10-K), so after
# a third-quarter report the next quarterly figure is about half a year plus 45 days away.
QUARTERLY_FRESH_DAYS = 91 + 45
QUARTERLY_AFTER_Q3_FRESH_DAYS = 182 + 48
ANNUAL_FRESH_DAYS = 365 + 90


def reporting_period_freshness(
    latest: dt.date, reference_now: dt.datetime, *, period_type: str, fiscal_period: str | None = None, what: str
) -> Freshness:
    """Fresh while no newer report of this kind could be due yet; stale once one is overdue (D-85)."""
    if period_type == "quarterly":
        threshold = QUARTERLY_AFTER_Q3_FRESH_DAYS if (fiscal_period or "").upper() == "Q3" else QUARTERLY_FRESH_DAYS
    else:
        threshold = ANNUAL_FRESH_DAYS
    return freshness_for_date(latest, reference_now, threshold_days=threshold, what=what)


def freshness_for_date(
    observed: dt.date | None,
    reference_now: dt.datetime,
    *,
    threshold_days: int,
    what: str,
) -> Freshness:
    """Label an observation date fresh/stale relative to the reference clock."""
    if observed is None:
        return Freshness(
            status=FreshnessStatus.UNKNOWN,
            reference_time=reference_now,
            note=f"The provider did not supply an observation date for the {what}.",
        )
    age = (reference_now.date() - observed).days
    stale = age > threshold_days
    return Freshness(
        status=FreshnessStatus.STALE if stale else FreshnessStatus.FRESH,
        reference_time=reference_now,
        observed=observed.isoformat(),
        age_days=age,
        threshold_days=threshold_days,
        note=(
            f"The {what} is {age} days older than the reference date; treat it as stale."
            if stale
            else f"The {what} is within {threshold_days} days of the reference date."
        ),
    )
