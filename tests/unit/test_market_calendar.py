"""NYSE trading calendar, gap-aware daily moves and price-coverage shortfalls."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from decimal import Decimal

from app.services.evidence_collector import coverage_shortfall
from marketlens_core.calculations import daily_moves
from marketlens_core.config import REPO_ROOT
from marketlens_core.market_calendar import is_trading_day, market_holidays, previous_trading_day, trading_days
from marketlens_core.models import PriceBar
from marketlens_core.tool_models import HistoricalPricesData

D = dt.date


def test_holidays_match_the_published_nyse_schedule() -> None:
    assert market_holidays(2025) == {
        D(2025, 1, 1), D(2025, 1, 20), D(2025, 2, 17), D(2025, 4, 18), D(2025, 5, 26),
        D(2025, 6, 19), D(2025, 7, 4), D(2025, 9, 1), D(2025, 11, 27), D(2025, 12, 25),
    }  # fmt: skip
    assert market_holidays(2026) == {
        D(2026, 1, 1), D(2026, 1, 19), D(2026, 2, 16), D(2026, 4, 3), D(2026, 5, 25),
        D(2026, 6, 19), D(2026, 7, 3), D(2026, 9, 7), D(2026, 11, 26), D(2026, 12, 25),
    }  # fmt: skip
    # Observed-day rules: Saturday -> Friday, Sunday -> Monday; Saturday New Year's is not moved.
    assert {D(2027, 6, 18), D(2027, 7, 5), D(2027, 12, 24)} <= market_holidays(2027)
    assert D(2021, 12, 31) not in market_holidays(2021) and D(2022, 1, 1) not in market_holidays(2022)


def test_fixture_dataset_skips_the_same_2026_holidays() -> None:
    spec = importlib.util.spec_from_file_location("generate_fixtures", REPO_ROOT / "scripts" / "generate_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in the script resolve annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    window = {d for d in market_holidays(2026) if D(2026, 5, 8) <= d <= D(2026, 9, 30)}
    assert window == module.NYSE_HOLIDAYS_2026


def test_trading_day_helpers() -> None:
    assert not is_trading_day(D(2026, 7, 3)) and not is_trading_day(D(2026, 7, 4)) and is_trading_day(D(2026, 7, 6))
    assert previous_trading_day(D(2026, 7, 6)) == D(2026, 7, 2)
    assert trading_days(D(2026, 7, 1), D(2026, 7, 7)) == [D(2026, 7, 1), D(2026, 7, 2), D(2026, 7, 6), D(2026, 7, 7)]


def bar(day: dt.date, close: str) -> PriceBar:
    c = Decimal(close)
    return PriceBar(trading_date=day, open=c, high=c, low=c, close=c, volume=1)


def test_daily_moves_skip_pairs_that_span_a_missing_trading_day() -> None:
    bars = [bar(D(2026, 7, 2), "100"), bar(D(2026, 7, 6), "103"), bar(D(2026, 7, 8), "90")]  # 07-07 missing
    moves = daily_moves(bars)
    assert [(m.previous_date, m.trading_date) for m in moves] == [
        (D(2026, 7, 2), D(2026, 7, 6))
    ]  # holiday is not a gap


def series(days: list[dt.date], start: dt.date, end: dt.date) -> HistoricalPricesData:
    return HistoricalPricesData(
        symbol="AAPL",
        currency="USD",
        adjustment="unadjusted",
        timezone="US/Eastern",
        bars=[bar(d, "100") for d in days],
        requested_start=start,
        requested_end=end,
        first_date=days[0],
        last_date=days[-1],
        provider="test",
        is_fixture=True,
        evidence_id="ev_prices_000000000000",
        evidence=[],
    )


def test_coverage_shortfall_rules() -> None:
    start, end = D(2026, 7, 1), D(2026, 7, 10)
    full = trading_days(start, end)
    assert coverage_shortfall(series(full, start, end), start, end) is None
    assert coverage_shortfall(series(full[:-1], start, end), start, end) is None  # one-session publication lag
    late_start = coverage_shortfall(series(full[2:], start, end), start, end)
    assert late_start and "2 trading day(s) at the start" in late_start
    hole = coverage_shortfall(series([d for d in full if d != D(2026, 7, 7)], start, end), start, end)
    assert hole and "1 inside the window (2026-07-07)" in hole
