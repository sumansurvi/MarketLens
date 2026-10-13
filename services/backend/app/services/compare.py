"""Compare price movement across two user-selected periods (PRD FR-20).

Each period is computed by the market-data MCP server's deterministic ``calculate_price_change``
tool, which reports the observation dates it actually used (the first and last trading days
inside the requested range). The difference between the two changes is computed here in
decimal arithmetic and labeled as percentage points; nothing is estimated or filled in.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.models import EvidenceItem
from marketlens_core.symbols import normalize_symbol
from marketlens_core.tool_models import PriceChangeData, ResolveSymbolData

from app.mcp.gateway import ToolGateway

MAX_PERIOD_DAYS = 3 * 366


@dataclass
class PeriodResult:
    label: str
    change: PriceChangeData


@dataclass
class Comparison:
    symbol: str
    company_name: str | None
    periods: list[PeriodResult]
    difference_percentage_points: Decimal | None
    difference_note: str
    is_fixture: bool
    evidence: list[EvidenceItem]


def _check_period(start: dt.date, end: dt.date, label: str) -> None:
    if start >= end:
        raise MarketLensError(ErrorCode.INVALID_INPUT, f"Period {label}: the start date must be before the end date.")
    if (end - start).days > MAX_PERIOD_DAYS:
        raise MarketLensError(ErrorCode.DATE_RANGE_TOO_LARGE, f"Period {label} is longer than three years.")


async def compare_periods(gateway: ToolGateway, symbol: str, periods: list[tuple[dt.date, dt.date]]) -> Comparison:
    if len(periods) != 2:
        raise MarketLensError(ErrorCode.INVALID_INPUT, "Exactly two periods are compared.")
    for label, (start, end) in zip("AB", periods, strict=True):
        _check_period(start, end, label)
    normalized = normalize_symbol(symbol)
    resolved = await gateway.call("research_sources", "resolve_symbol", {"symbol": normalized})
    if not resolved.ok or resolved.error is not None:
        raise (
            MarketLensError.from_info(resolved.error)
            if resolved.error
            else MarketLensError(ErrorCode.INTERNAL_ERROR, "Symbol check failed.")
        )
    identity = ResolveSymbolData.model_validate(resolved.data)
    results: list[PeriodResult] = []
    for label, (start, end) in zip("AB", periods, strict=True):
        outcome = await gateway.call(
            "market_data",
            "calculate_price_change",
            {
                "symbol": normalized,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "price_field": "close",
            },
        )
        if not outcome.ok or outcome.error is not None:
            raise (
                MarketLensError.from_info(outcome.error)
                if outcome.error
                else MarketLensError(ErrorCode.INTERNAL_ERROR, "The price-change calculation failed.")
            )
        results.append(PeriodResult(label, PriceChangeData.model_validate(outcome.data)))
    a, b = (r.change.percentage_change for r in results)
    if a is not None and b is not None:
        difference = (Decimal(str(b)) - Decimal(str(a))).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        note = "Period B's percentage change minus period A's, in percentage points."
    else:
        difference = None
        note = "No difference is computed because at least one period has no usable prices."
    evidence = [item for r in results for item in r.change.evidence]
    return Comparison(
        symbol=normalized,
        company_name=identity.company_name,
        periods=results,
        difference_percentage_points=difference,
        difference_note=note,
        is_fixture=all(r.change.is_fixture for r in results),
        evidence=evidence,
    )
