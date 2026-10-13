"""Deterministic calculations on retrieved evidence (architecture principle 5).

Each result becomes a ``calculation`` evidence item that records its formula, its input
values and the evidence IDs of those inputs, so every calculated claim is reproducible.
Year-over-year comparisons match periods by dates (SEC ``fy``/``fp`` describe filings).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from marketlens_core.calculations import DailyMove, largest_moves, percent_change
from marketlens_core.models import EvidenceItem, EvidenceKind, FinancialMetric, PriceBar, SourceMode
from marketlens_core.providers.base import ProviderPayload, build_evidence
from marketlens_core.timeutil import utc_now

from app.services.evidence_collector import EvidenceBundle

CALC_SOURCE = "MarketLens deterministic calculation"


@dataclass
class MoveCalc:
    move: DailyMove
    evidence_id: str


@dataclass
class RangeCalc:
    low: PriceBar
    high: PriceBar
    evidence_id: str


@dataclass
class GrowthCalc:
    metric_name: str
    label: str
    latest: FinancialMetric
    prior: FinancialMetric
    percentage_change: Decimal | None
    unavailable_reason: str | None
    evidence_id: str


@dataclass
class MarginCalc:
    revenue: FinancialMetric
    operating_income: FinancialMetric
    margin_pct: Decimal
    evidence_id: str


@dataclass
class DerivedMetrics:
    largest_decline: MoveCalc | None = None
    largest_gain: MoveCalc | None = None
    close_range: RangeCalc | None = None
    quarterly_growth: dict[str, GrowthCalc] = field(default_factory=dict)
    annual_growth: dict[str, GrowthCalc] = field(default_factory=dict)
    operating_margin: MarginCalc | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)


def _calc_evidence(
    bundle: EvidenceBundle,
    *,
    category: str,
    name: str,
    title: str,
    formula: str,
    inputs: list[str],
    values: dict[str, object],
    period_start: dt.date | None,
    period_end: dt.date | None,
    units: str,
) -> EvidenceItem:
    modes = {bundle.ledger[i].source_mode for i in inputs if i in bundle.ledger}
    mode = SourceMode.FIXTURE if modes == {SourceMode.FIXTURE} else SourceMode.LIVE
    payload = ProviderPayload(data=None, source_mode=mode, retrieved_at=utc_now(), request_label=f"calculation:{name}")
    rendered = {
        k: (v.isoformat() if isinstance(v, dt.date) else (str(v) if isinstance(v, Decimal) else v))
        for k, v in values.items()
    }
    return build_evidence(
        kind=EvidenceKind.CALCULATION,
        payload=payload,
        provider="marketlens",
        source_name=CALC_SOURCE,
        identity={"calc": name, "inputs": inputs, "symbol": bundle.company.symbol},
        content=rendered,
        symbol=bundle.company.symbol,
        title=title,
        period_start=period_start,
        period_end=period_end,
        currency="USD",
        units=units,
        evidence_payload={"category": category, "formula": formula, "input_evidence_ids": inputs, **rendered},
    )


def _match_prior(latest: FinancialMetric, series: list[FinancialMetric]) -> FinancialMetric | None:
    """Same-length period ending ~one year earlier (±16 days), never the same period."""
    if latest.period_start is None:
        return None
    length = (latest.period_end - latest.period_start).days
    for candidate in series:
        if candidate is latest or candidate.period_start is None:
            continue
        gap = (latest.period_end - candidate.period_end).days
        candidate_length = (candidate.period_end - candidate.period_start).days
        if 349 <= gap <= 381 and abs(candidate_length - length) <= 10:
            return candidate
    return None


def _growth(bundle: EvidenceBundle, facts: list[FinancialMetric], metric: str, period_type: str) -> GrowthCalc | None:
    series = sorted((f for f in facts if f.metric_name == metric), key=lambda f: f.period_end, reverse=True)
    if not series:
        return None
    latest = series[0]
    prior = _match_prior(latest, series)
    if prior is None:
        return None
    pct, reason = percent_change(prior.value, latest.value)
    inputs = list(dict.fromkeys([latest.evidence_id, prior.evidence_id]))
    evidence = _calc_evidence(
        bundle,
        category="fundamentals",
        name=f"yoy_{period_type}_{metric}",
        title=f"{latest.label}: {period_type} period ended {latest.period_end.isoformat()} vs "
        f"period ended {prior.period_end.isoformat()}",
        formula="(latest_value - prior_value) / prior_value * 100",
        inputs=inputs,
        values={
            "metric": metric,
            "latest_value": latest.value,
            "latest_period_start": latest.period_start,
            "latest_period_end": latest.period_end,
            "prior_value": prior.value,
            "prior_period_start": prior.period_start,
            "prior_period_end": prior.period_end,
            "percentage_change": pct,
            "unavailable_reason": reason,
        },
        period_start=prior.period_start,
        period_end=latest.period_end,
        units="percent",
    )
    bundle.add_evidence([evidence])
    return GrowthCalc(metric, latest.label, latest, prior, pct, reason, evidence.evidence_id)


def compute_derived(bundle: EvidenceBundle) -> DerivedMetrics:
    derived = DerivedMetrics()
    before = set(bundle.ledger)
    prices = bundle.prices
    if prices and len(prices.bars) >= 2:
        declines, gains = largest_moves(prices.bars, 1)
        for moves, attr, word in ((declines, "largest_decline", "decline"), (gains, "largest_gain", "gain")):
            if not moves:
                continue
            move = moves[0]
            evidence = _calc_evidence(
                bundle,
                category="market",
                name=f"largest_daily_{word}",
                title=f"Largest close-to-close {word} {prices.first_date} to {prices.last_date}",
                formula="(close_t - close_previous) / close_previous * 100, maximum magnitude in window",
                inputs=[prices.evidence_id],
                values={
                    "trading_date": move.trading_date,
                    "previous_date": move.previous_date,
                    "previous_close": move.previous_close,
                    "close": move.close,
                    "percentage_change": move.percentage_change,
                },
                period_start=move.previous_date,
                period_end=move.trading_date,
                units="percent",
            )
            bundle.add_evidence([evidence])
            setattr(derived, attr, MoveCalc(move, evidence.evidence_id))
        low = min(prices.bars, key=lambda b: (b.close, b.trading_date))
        high = max(prices.bars, key=lambda b: (b.close, -b.trading_date.toordinal()))
        evidence = _calc_evidence(
            bundle,
            category="market",
            name="close_range",
            title=f"Closing-price range {prices.first_date} to {prices.last_date}",
            formula="min(close), max(close) over trading days in the window",
            inputs=[prices.evidence_id],
            values={
                "low_close": low.close,
                "low_date": low.trading_date,
                "high_close": high.close,
                "high_date": high.trading_date,
            },
            period_start=prices.first_date,
            period_end=prices.last_date,
            units="USD per share",
        )
        bundle.add_evidence([evidence])
        derived.close_range = RangeCalc(low, high, evidence.evidence_id)
    if bundle.facts_quarterly:
        facts = bundle.facts_quarterly.facts
        for metric in ("revenue", "net_income", "diluted_eps", "operating_income"):
            growth = _growth(bundle, facts, metric, "quarterly")
            if growth:
                derived.quarterly_growth[metric] = growth
        revenue = max((f for f in facts if f.metric_name == "revenue"), key=lambda f: f.period_end, default=None)
        if revenue is not None:
            op = next(
                (
                    f
                    for f in facts
                    if f.metric_name == "operating_income"
                    and f.period_end == revenue.period_end
                    and f.period_start == revenue.period_start
                ),
                None,
            )
            if op is not None and revenue.value != 0:
                margin = (op.value / revenue.value * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
                evidence = _calc_evidence(
                    bundle,
                    category="fundamentals",
                    name="operating_margin_latest_quarter",
                    title=f"Operating margin, quarter ended {revenue.period_end.isoformat()}",
                    formula="operating_income / revenue * 100",
                    inputs=list(dict.fromkeys([op.evidence_id, revenue.evidence_id])),
                    values={
                        "operating_income": op.value,
                        "revenue": revenue.value,
                        "margin_pct": margin,
                        "period_start": revenue.period_start,
                        "period_end": revenue.period_end,
                    },
                    period_start=revenue.period_start,
                    period_end=revenue.period_end,
                    units="percent",
                )
                bundle.add_evidence([evidence])
                derived.operating_margin = MarginCalc(revenue, op, margin, evidence.evidence_id)
    if bundle.facts_annual:
        for metric in ("revenue", "net_income"):
            growth = _growth(bundle, bundle.facts_annual.facts, metric, "annual")
            if growth:
                derived.annual_growth[metric] = growth
    derived.evidence = [bundle.ledger[i] for i in bundle.ledger if i not in before]
    return derived
