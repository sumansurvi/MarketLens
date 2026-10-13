"""Independent ground truth for the evaluation (EVALUATION.md §11).

Every number is recomputed from the raw, provider-shaped fixture files with Decimal code written
here, never by calling the application's calculation, parsing or validation modules. The
trading-day calendar is the set of dates present in the pristine fixture dataset, which the
generator built by skipping weekends and the 2026 NYSE holidays.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from functools import cache
from pathlib import Path
from typing import Any

FOUR = Decimal("0.0001")
FIXTURE_URL_PREFIX = "https://example.com/marketlens-fixture/"
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_IGNORE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\bus-gaap:\w+|\bev_\w+|\b(?:10-K|10-Q|8-K)\b|\bQ[1-4]\b|\bFY\s?\d{2,4}\b|"
    r"\b(?:fiscal|calendar)\s+\d{4}\b|\b(?:19|20)\d{2}\b(?![.\d%])",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?\$?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?"
    r"(?P<suffix>\s?%|\s(?:billion|million)\b)?"
)


def d(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def pct(start: Decimal, end: Decimal) -> Decimal:
    return ((end - start) / start * 100).quantize(FOUR, rounding=ROUND_HALF_EVEN)


@dataclass
class Check:
    problems: list[str] = field(default_factory=list)
    truths: list[Decimal] = field(default_factory=list)
    checks: int = 0

    def expect(self, ok: bool, message: str) -> None:
        self.checks += 1
        if not ok:
            self.problems.append(message)


class RawFixtures:
    """Raw fixture files for one environment (the pristine dataset or a per-case overlay)."""

    def __init__(self, root: Path, pristine: Path) -> None:
        self.root = root
        self.pristine = pristine

    def _json(self, relative: str) -> dict[str, Any]:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    @cache  # noqa: B019 - one instance per environment, small and short-lived
    def closes(self, symbol: str) -> dict[dt.date, Decimal]:
        """Valid daily closes: positive, and the close lies within the day's low-high range."""
        series = self._json(f"alpha_vantage/TIME_SERIES_DAILY/{symbol}.json").get("Time Series (Daily)", {})
        result: dict[dt.date, Decimal] = {}
        for day, bar in series.items():
            try:
                low, high, close = (Decimal(bar[k]) for k in ("3. low", "2. high", "4. close"))
            except (KeyError, ArithmeticError, ValueError):
                continue
            if low > 0 and low <= close <= high:
                result[d(day)] = close
        return result

    @cache  # noqa: B019
    def trading_calendar(self) -> frozenset[dt.date]:
        days: set[dt.date] = set()
        for path in (self.pristine / "alpha_vantage" / "TIME_SERIES_DAILY").glob("*.json"):
            days |= {d(k) for k in json.loads(path.read_text())["Time Series (Daily)"]}
        return frozenset(days)

    def quote(self, symbol: str) -> tuple[Decimal, dt.date, Decimal]:
        q = self._json(f"alpha_vantage/GLOBAL_QUOTE/{symbol}.json")["Global Quote"]
        return Decimal(q["05. price"]), d(q["07. latest trading day"]), Decimal(q["08. previous close"])

    def av_metric(self, symbol: str, provider_field: str, end: str, period_type: str) -> Decimal | None:
        """A provider-normalized value from the Alpha Vantage income-statement or earnings files."""
        if provider_field == "reportedEPS":
            data = self._json(f"alpha_vantage/EARNINGS/{symbol}.json")
            rows = data["quarterlyEarnings" if period_type == "quarterly" else "annualEarnings"]
        else:
            data = self._json(f"alpha_vantage/INCOME_STATEMENT/{symbol}.json")
            rows = data["quarterlyReports" if period_type == "quarterly" else "annualReports"]
        for row in rows:
            if row.get("fiscalDateEnding") == end and row.get(provider_field) not in (None, "None"):
                return Decimal(str(row[provider_field]))
        return None

    def fact(self, cik: str, concept: str, start: str | None, end: str) -> Decimal | None:
        """The value reported for a period by the latest filing that reports it (restatement-aware)."""
        facts = self._json(f"sec/companyfacts/CIK{int(cik):010d}.json")["facts"]
        for taxonomy in facts.values():
            entry = taxonomy.get(concept)
            if not entry:
                continue
            rows = [
                r for unit in entry["units"].values() for r in unit if r.get("end") == end and r.get("start") == start
            ]
            if rows:
                return Decimal(str(max(rows, key=lambda r: r.get("filed", ""))["val"]))
        return None


def _window_closes(raw: RawFixtures, symbol: str, start: dt.date, end: dt.date) -> list[tuple[dt.date, Decimal]]:
    return sorted((day, close) for day, close in raw.closes(symbol).items() if start <= day <= end)


def check_evidence(
    item: dict[str, Any],
    raw: RawFixtures,
    symbol: str,
    cik: str | None,
    window: tuple[dt.date, dt.date],
    reference: dt.date | None = None,
) -> Check:
    """Recompute one evidence item from raw fixtures; returns problems and independent truth values."""
    c = Check()
    kind, p = item["kind"], item.get("payload") or {}
    if kind == "quote":
        price, day, previous = raw.quote(symbol)
        c.expect(Decimal(str(p["price"])) == price, f"quote price {p['price']} != raw {price}")
        c.expect(p.get("trading_date") == day.isoformat(), f"quote date {p.get('trading_date')} != raw {day}")
        c.truths += [price, previous]
        if reference is not None:
            c.truths.append(Decimal((reference - day).days))  # age in days, as disclosed for stale quotes
    elif kind == "price_series":
        in_window = _window_closes(raw, symbol, *window)
        c.expect(p["bar_count"] == len(in_window), f"bar_count {p['bar_count']} != raw {len(in_window)} in window")
        c.expect(
            bool(in_window) and p["first_date"] == in_window[0][0].isoformat(),
            "series first_date is not the first raw bar",
        )
        c.expect(
            bool(in_window) and p["last_date"] == in_window[-1][0].isoformat(),
            "series last_date is not the last raw bar",
        )
        for bar in p.get("bars", []):
            raw_close = raw.closes(symbol).get(d(bar["trading_date"]))
            c.expect(
                raw_close is not None and Decimal(str(bar["close"])) == raw_close, f"bar {bar['trading_date']} close"
            )
        c.truths += [close for _, close in in_window] + [Decimal(len(in_window))]
    elif kind == "financial_metric" and "provider_field" in p:
        for row in p.get("values", []):
            truth = raw.av_metric(symbol, p["provider_field"], row["period_end"], p["period_type"])
            c.expect(
                truth is not None and Decimal(str(row["value"])) == truth, f"{p['provider_field']} {row['period_end']}"
            )
            if truth is not None:
                c.truths.append(truth)
    elif kind == "financial_metric" and cik:
        for row in p.get("values", []):
            truth = raw.fact(cik, p["concept"], row.get("start"), row["end"])
            c.expect(truth is not None and Decimal(str(row["value"])) == truth, f"{p['concept']} {row['end']} value")
            if truth is not None:
                c.truths.append(truth)
    elif kind == "calculation":
        _check_calculation(c, p, raw, symbol, cik, window)
    return c


def _check_calculation(
    c: Check, p: dict[str, Any], raw: RawFixtures, symbol: str, cik: str | None, window: tuple[dt.date, dt.date]
) -> None:
    closes = raw.closes(symbol)
    if {"start_value", "end_value", "percentage_change"} <= p.keys():  # window price change
        start, end = (
            d(p.get("requested_start", window[0].isoformat())),
            d(p.get("requested_end", window[1].isoformat())),
        )
        days = _window_closes(raw, symbol, start, end)
        if p["percentage_change"] is None:
            c.expect(p.get("unavailable_reason") is not None, "unavailable change without a reason")
            return
        c.expect(p["start_date"] == days[0][0].isoformat(), f"start date {p['start_date']} != first raw {days[0][0]}")
        c.expect(p["end_date"] == days[-1][0].isoformat(), f"end date {p['end_date']} != last raw {days[-1][0]}")
        first, last = days[0][1], days[-1][1]
        c.expect(Decimal(p["start_value"]) == first and Decimal(p["end_value"]) == last, "window closes differ")
        c.expect(
            Decimal(p["percentage_change"]) == pct(first, last),
            f"change {p['percentage_change']} != {pct(first, last)}",
        )
        c.expect(Decimal(p["absolute_change"]) == last - first, "absolute change differs")
        c.truths += [first, last, pct(first, last), last - first]
    elif {"trading_date", "previous_close", "close", "percentage_change"} <= p.keys():  # largest daily move
        calendar = sorted(raw.trading_calendar())
        day, previous = d(p["trading_date"]), d(p["previous_date"])
        prior_days = [x for x in calendar if x < day]
        c.expect(bool(prior_days) and prior_days[-1] == previous, f"{previous} is not the trading day before {day}")
        prev_close, close = closes.get(previous), closes.get(day)
        c.expect(prev_close == Decimal(p["previous_close"]) and close == Decimal(p["close"]), "move closes differ")
        if prev_close and close:
            c.expect(Decimal(p["percentage_change"]) == pct(prev_close, close), "move percentage differs")
            c.truths += [prev_close, close, pct(prev_close, close)]
        bars = _window_closes(raw, symbol, *window)
        moves = [
            pct(a[1], b[1])
            for a, b in zip(bars, bars[1:], strict=False)
            if [x for x in calendar if x < b[0]][-1:] == [a[0]]
        ]
        c.expect(bool(moves), "no consecutive trading-day moves in the raw window")
        if moves:
            extreme = min(moves) if Decimal(p["percentage_change"]) < 0 else max(moves)
            c.expect(
                Decimal(p["percentage_change"]) == extreme,
                f"move {p['percentage_change']} is not the extreme {extreme}",
            )
    elif {"low_close", "high_close"} <= p.keys():  # close range
        bars = _window_closes(raw, symbol, *window)
        low = min(bars, key=lambda b: (b[1], b[0]))
        high = max(bars, key=lambda b: (b[1], -b[0].toordinal()))
        c.expect((d(p["low_date"]), Decimal(p["low_close"])) == low, f"range low {p['low_date']} differs")
        c.expect((d(p["high_date"]), Decimal(p["high_close"])) == high, f"range high {p['high_date']} differs")
        c.truths += [low[1], high[1]]
    elif {"latest_value", "prior_value", "metric"} <= p.keys() and cik:  # year-over-year growth
        concept = _concept_for(raw, cik, p["metric"], p["latest_period_start"], p["latest_period_end"])
        latest = raw.fact(cik, concept, p["latest_period_start"], p["latest_period_end"]) if concept else None
        prior = raw.fact(cik, concept, p["prior_period_start"], p["prior_period_end"]) if concept else None
        c.expect(latest is not None and Decimal(p["latest_value"]) == latest, f"{p['metric']} latest value differs")
        c.expect(prior is not None and Decimal(p["prior_value"]) == prior, f"{p['metric']} prior value differs")
        gap = (d(p["latest_period_end"]) - d(p["prior_period_end"])).days
        length = lambda s, e: (d(e) - d(s)).days  # noqa: E731
        c.expect(349 <= gap <= 381, f"{p['metric']} periods are not one year apart")
        c.expect(
            abs(
                length(p["latest_period_start"], p["latest_period_end"])
                - length(p["prior_period_start"], p["prior_period_end"])
            )
            <= 10,
            f"{p['metric']} compares periods of different length",
        )
        if latest is not None and prior:
            c.expect(Decimal(p["percentage_change"]) == pct(prior, latest), f"{p['metric']} growth differs")
            c.truths += [latest, prior, pct(prior, latest)]
    elif "margin_pct" in p and cik:  # operating margin
        start, end = p["period_start"], p["period_end"]
        op = raw.fact(cik, "OperatingIncomeLoss", start, end)
        revenue = raw.fact(cik, _concept_for(raw, cik, "revenue", start, end) or "", start, end)
        c.expect(op is not None and revenue is not None, "margin inputs missing in raw facts")
        if op is not None and revenue:
            margin = (op / revenue * 100).quantize(FOUR, rounding=ROUND_HALF_EVEN)
            c.expect(Decimal(p["margin_pct"]) == margin, f"margin {p['margin_pct']} != {margin}")
            c.truths += [op, revenue, margin]


_CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "operating_income": ("OperatingIncomeLoss",),
    "diluted_eps": ("EarningsPerShareDiluted",),
}


def _concept_for(raw: RawFixtures, cik: str, metric: str, start: str, end: str) -> str | None:
    for concept in _CONCEPTS.get(metric, ()):
        if raw.fact(cik, concept, start, end) is not None:
            return concept
    return None


def claim_numbers(text: str) -> list[tuple[Decimal, int, Decimal]]:
    """(magnitude, decimal places, scale) for each number shown in a claim."""
    cleaned = _IGNORE.sub(" ", text)
    found = []
    for m in _NUMBER.finditer(cleaned):
        places = len(m.group("frac") or "")
        value = Decimal(m.group("int").replace(",", "") + (f".{m.group('frac')}" if m.group("frac") else ""))
        suffix = (m.group("suffix") or "").strip().lower()
        scale = Decimal(10) ** 9 if suffix == "billion" else Decimal(10) ** 6 if suffix == "million" else Decimal(1)
        found.append((value, places, scale))
    return found


def grounded(value: Decimal, places: int, scale: Decimal, truths: list[Decimal]) -> bool:
    tolerance = Decimal("0.5") * Decimal(10) ** -places * scale + Decimal("1e-9")
    return any(abs(abs(t) - value * scale) <= tolerance for t in truths)
