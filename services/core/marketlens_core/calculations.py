"""Deterministic financial calculations (decision D-14).

All arithmetic uses ``Decimal`` built from provider strings. Percentages are rounded
half-even to 4 dp. A missing or zero base value yields ``None`` plus a reason — never
infinity, NaN or a guessed value.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from marketlens_core.market_calendar import previous_trading_day
from marketlens_core.models import PriceBar

PCT_QUANTUM = Decimal("0.0001")
PRICE_CHANGE_FORMULA = "(end_value - start_value) / start_value * 100"


def percent_change(start: Decimal | None, end: Decimal | None) -> tuple[Decimal | None, str | None]:
    """Percentage change from ``start`` to ``end``; ``(None, reason)`` when undefined."""
    if start is None or end is None:
        return None, "A start or end value is missing, so the percentage change is unavailable."
    if start == 0:
        return None, "The start value is zero, so a percentage change is undefined."
    pct = (end - start) / start * Decimal(100)
    return pct.quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN), None


def _field(bar: PriceBar, price_field: str) -> Decimal:
    return getattr(bar, price_field)


@dataclass
class PriceChangeResult:
    start_value: Decimal | None = None
    start_date: dt.date | None = None
    end_value: Decimal | None = None
    end_date: dt.date | None = None
    absolute_change: Decimal | None = None
    percentage_change: Decimal | None = None
    unavailable_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


def price_change(
    bars: list[PriceBar],
    start: dt.date,
    end: dt.date,
    price_field: str = "close",
) -> PriceChangeResult:
    """First observation on/after ``start`` vs last observation on/before ``end`` (inclusive)."""
    result = PriceChangeResult()
    in_window = sorted((b for b in bars if start <= b.trading_date <= end), key=lambda b: b.trading_date)
    if not in_window:
        result.unavailable_reason = f"No trading-day observations between {start.isoformat()} and {end.isoformat()}."
        return result
    first, last = in_window[0], in_window[-1]
    result.start_date, result.end_date = first.trading_date, last.trading_date
    result.start_value, result.end_value = _field(first, price_field), _field(last, price_field)
    if first.trading_date == last.trading_date:
        result.warnings.append("Only one observation falls inside the window; the change is zero by construction.")
    if first.trading_date != start:
        result.warnings.append(
            f"First available observation is {first.trading_date.isoformat()} (requested start {start.isoformat()})."
        )
    if last.trading_date != end:
        result.warnings.append(
            f"Last available observation is {last.trading_date.isoformat()} (requested end {end.isoformat()})."
        )
    result.absolute_change = result.end_value - result.start_value
    result.percentage_change, reason = percent_change(result.start_value, result.end_value)
    if reason:
        result.unavailable_reason = reason
    return result


@dataclass(frozen=True)
class DailyMove:
    trading_date: dt.date
    previous_date: dt.date
    previous_close: Decimal
    close: Decimal
    percentage_change: Decimal


def daily_moves(bars: list[PriceBar]) -> list[DailyMove]:
    """Close-to-close moves between consecutive *trading days*.

    A pair separated by a missing trading day (no bar for a day the market was open) spans
    more than one session, so it is not a single-day move and is skipped; weekends and
    exchange holidays are not gaps."""
    ordered = sorted(bars, key=lambda b: b.trading_date)
    moves: list[DailyMove] = []
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if previous_trading_day(cur.trading_date) != prev.trading_date:
            continue
        pct, _ = percent_change(prev.close, cur.close)
        if pct is not None:
            moves.append(DailyMove(cur.trading_date, prev.trading_date, prev.close, cur.close, pct))
    return moves


def largest_moves(bars: list[PriceBar], count: int = 1) -> tuple[list[DailyMove], list[DailyMove]]:
    """Return (largest declines, largest gains) by close-to-close percentage."""
    moves = daily_moves(bars)
    declines = sorted((m for m in moves if m.percentage_change < 0), key=lambda m: m.percentage_change)[:count]
    gains = sorted((m for m in moves if m.percentage_change > 0), key=lambda m: -m.percentage_change)[:count]
    return declines, gains


def quantize_like(value: Decimal, exemplar: Decimal) -> Decimal:
    """Round ``value`` to the number of decimal places used by ``exemplar``."""
    exponent = exemplar.as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= 0:
        return value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_EVEN)
