"""Display formatting for claim text. The validator parses these same formats back."""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, Decimal

BILLION = Decimal(1_000_000_000)
MILLION = Decimal(1_000_000)


def usd(value: Decimal, places: int = 2) -> str:
    quantum = Decimal(1).scaleb(-places)
    q = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    sign = "-" if q < 0 else ""
    return f"{sign}${abs(q):,.{places}f}"


def usd_compact(value: Decimal) -> str:
    magnitude = abs(value)
    if magnitude >= BILLION:
        return (
            f"{'-' if value < 0 else ''}${(magnitude / BILLION).quantize(Decimal('0.01'), ROUND_HALF_EVEN):,} billion"
        )
    if magnitude >= MILLION:
        return (
            f"{'-' if value < 0 else ''}${(magnitude / MILLION).quantize(Decimal('0.01'), ROUND_HALF_EVEN):,} million"
        )
    return usd(value)


def pct(value: Decimal, places: int = 2, signed: bool = True) -> str:
    q = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return f"{q:+.{places}f}%" if signed else f"{abs(q):.{places}f}%"


def per_share(value: Decimal) -> str:
    return usd(value, 2)


def date(value: dt.date | None) -> str:
    return value.isoformat() if value else "an unreported date"


def direction(value: Decimal, up: str = "rose", down: str = "fell", flat: str = "was unchanged") -> str:
    if value > 0:
        return up
    if value < 0:
        return down
    return flat
