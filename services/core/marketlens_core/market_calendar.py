"""US equity market (NYSE/Nasdaq) trading-day calendar for full-day closures.

Used to tell a *missing* daily bar from a weekend or exchange holiday: price moves are only
"single-day" between consecutive trading days, and expected trading days without data are
disclosed. Rules follow the NYSE holiday schedule: New Year's Day, Martin Luther King Jr. Day,
Washington's Birthday, Good Friday, Memorial Day, Juneteenth (from 2022), Independence Day,
Labor Day, Thanksgiving and Christmas. A Saturday holiday is observed on Friday and a Sunday
holiday on Monday, except that New Year's Day on a Saturday is not observed on the prior
Friday. Early-close days are trading days. One-off closures (e.g. national days of mourning)
are not modeled; a bar missing on such a day is reported as missing, never invented.
"""

from __future__ import annotations

import datetime as dt
from functools import cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    last = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return dt.date(year, month, day + 1)


def _observed(day: dt.date) -> dt.date:
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


@cache
def market_holidays(year: int) -> frozenset[dt.date]:
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(dt.date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(dt.date(year, 12, 25)),  # Christmas
    }
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() != 5:  # a Saturday New Year's Day is not observed on Dec 31
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(dt.date(year, 6, 19)))  # Juneteenth
    return frozenset(days)


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in market_holidays(day.year)


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """Trading days in ``[start, end]`` (inclusive)."""
    days: list[dt.date] = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor += dt.timedelta(days=1)
    return days


def previous_trading_day(day: dt.date) -> dt.date:
    cursor = day - dt.timedelta(days=1)
    while not is_trading_day(cursor):
        cursor -= dt.timedelta(days=1)
    return cursor
