"""Time helpers: UTC timestamps, the reference clock and research time horizons.

Reference clock (decision D-10): in fixture mode, "now" for default horizons and
freshness is the fixture dataset's ``as_of``; in live mode it is real UTC now. Retrieval
timestamps are always real wall-clock times and never stand in for observation times.
"""

from __future__ import annotations

import calendar
import datetime as dt

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.models import TimeHorizon, TimeHorizonInput

UTC = dt.UTC
MAX_HORIZON_DAYS = 366
PRESET_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "1y": 12}
DEFAULT_PRESET = "3m"


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso_z(value: dt.datetime) -> str:
    """ISO-8601 in UTC with a ``Z`` suffix."""
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not allowed here")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without zone: {value!r}")
    return parsed.astimezone(UTC)


def subtract_months(day: dt.date, months: int) -> dt.date:
    """Same day-of-month ``months`` earlier, clamped to the target month's length."""
    month_index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(month_index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(day.day, last_day))


def resolve_time_horizon(
    requested: TimeHorizonInput | None,
    *,
    reference_date: dt.date,
    basis: str,
) -> TimeHorizon:
    """Turn a request's horizon into an inclusive date window, disclosing defaults."""
    if requested is None or (requested.preset is None and requested.start_date is None and requested.end_date is None):
        months = PRESET_MONTHS[DEFAULT_PRESET]
        return TimeHorizon(
            start_date=subtract_months(reference_date, months),
            end_date=reference_date,
            label=f"Last {months} months (default — not user-selected)",
            is_default=True,
            reference_date=reference_date,
            basis=basis,
        )
    if requested.preset is not None:
        if requested.start_date or requested.end_date:
            raise MarketLensError(ErrorCode.INVALID_INPUT, "Provide either a preset or explicit dates, not both.")
        months = PRESET_MONTHS[requested.preset]
        label = "Last 12 months" if months == 12 else f"Last {months} month{'s' if months > 1 else ''}"
        return TimeHorizon(
            start_date=subtract_months(reference_date, months),
            end_date=reference_date,
            label=label,
            is_default=False,
            reference_date=reference_date,
            basis=basis,
        )
    if requested.start_date is None or requested.end_date is None:
        raise MarketLensError(ErrorCode.INVALID_INPUT, "Custom horizons need both start_date and end_date.")
    start, end = requested.start_date, requested.end_date
    if end < start:
        raise MarketLensError(ErrorCode.INVALID_INPUT, "end_date cannot be earlier than start_date.")
    if end > reference_date:
        raise MarketLensError(
            ErrorCode.INVALID_INPUT,
            f"end_date {end.isoformat()} is after the latest available data date "
            f"({reference_date.isoformat()}; {basis}). Choose an earlier end date.",
        )
    if (end - start).days > MAX_HORIZON_DAYS:
        raise MarketLensError(
            ErrorCode.DATE_RANGE_TOO_LARGE,
            f"Research horizons are limited to {MAX_HORIZON_DAYS} days. Narrow the date range.",
        )
    return TimeHorizon(
        start_date=start,
        end_date=end,
        label=f"{start.isoformat()} to {end.isoformat()}",
        is_default=False,
        reference_date=reference_date,
        basis=basis,
    )
