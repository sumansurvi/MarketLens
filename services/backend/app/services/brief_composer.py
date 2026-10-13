"""Deterministic brief composition from evidence (Phase 1 orchestrator; later the fixture
LLM's scripted behavior and the labeled no-LLM fallback).

Methods mirror the specialist responsibilities that Phase 3 assigns to separate agents:
market, fundamentals, filings, news/events and synthesis. Every sentence is built from
retrieved values and cites the evidence it came from; the validator re-checks all of it.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from marketlens_core.market_calendar import trading_days
from marketlens_core.models import (
    BriefSection,
    CategoryStatus,
    ClaimType,
    EventEntry,
    EvidenceItem,
    EvidenceKind,
    FinancialMetric,
    FreshnessStatus,
    GapItem,
    KeyMetric,
    ResearchCategory,
    ResearchClaim,
    SourceRef,
)
from marketlens_core.tool_models import PriceChangeData
from marketlens_core.untrusted import INJECTION_FLAG

from app.services import formatting as fmt
from app.services.derived_metrics import DerivedMetrics, GrowthCalc, MoveCalc
from app.services.evidence_collector import EvidenceBundle

KIND_ORDER = {
    k: i for i, k in enumerate(["quote", "price_series", "calculation", "financial_metric", "filing", "news"])
}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class BriefComposer:
    def __init__(self, bundle: EvidenceBundle, derived: DerivedMetrics) -> None:
        self.b = bundle
        self.d = derived
        self.symbol = bundle.company.symbol
        self._counters: dict[str, int] = {}

    # ------------------------------------------------------------------ helpers
    def _claim(
        self,
        section: BriefSection,
        claim_type: ClaimType,
        text: str,
        evidence_ids: list[str],
        produced_by: str,
        *,
        materiality: str = "material",
        confidence_note: str | None = None,
        assumptions: list[str] | None = None,
    ) -> ResearchClaim:
        prefix = section.value[:4]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return ResearchClaim(
            claim_id=f"cl_{prefix}_{self._counters[prefix]}",
            section=section,
            text=text,
            claim_type=claim_type,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            materiality=materiality,  # type: ignore[arg-type]
            confidence_note=confidence_note,
            assumptions=assumptions or [],
            produced_by=produced_by,
        )

    def _latest(self, metric: str) -> FinancialMetric | None:
        facts = self.b.facts_quarterly.facts if self.b.facts_quarterly else []
        return max((f for f in facts if f.metric_name == metric), key=lambda f: f.period_end, default=None)

    def _latest_annual(self, metric: str) -> FinancialMetric | None:
        facts = self.b.facts_annual.facts if self.b.facts_annual else []
        return max((f for f in facts if f.metric_name == metric), key=lambda f: f.period_end, default=None)

    def _news_items(self, *, include_flagged: bool = False) -> list[EvidenceItem]:
        if not self.b.news:
            return []
        items = [self.b.ledger[a.evidence_id] for a in self.b.news.articles if a.evidence_id in self.b.ledger]
        if not include_flagged:
            items = [e for e in items if INJECTION_FLAG not in e.flags]
        return sorted(items, key=lambda e: e.published_at or "", reverse=True)

    @staticmethod
    def _fiscal_label(fact: FinancialMetric) -> str:
        if fact.fiscal_year and fact.fiscal_period:
            return (
                f" (fiscal {fact.fiscal_year} {fact.fiscal_period})"
                if fact.fiscal_period != "FY"
                else f" (fiscal {fact.fiscal_year})"
            )
        return ""

    def _missing_bars(self) -> list[dt.date]:
        """Trading days inside the returned price range that have no bar."""
        prices = self.b.prices
        if not prices:
            return []
        observed = {b.trading_date for b in prices.bars}
        return [d for d in trading_days(prices.first_date, prices.last_date) if d not in observed]

    def _moves_lead(self, word: str) -> str:
        if self._missing_bars():
            return f"Among trading days with consecutive daily data, the largest single-day {word} was"
        return f"The largest single-day {word} in the window was"

    @staticmethod
    def _covers_window(pc: PriceChangeData) -> bool:
        """True when the change runs from the window's first trading day to its last (one-session lag allowed)."""
        expected = trading_days(pc.requested_start, pc.requested_end)
        if not expected or pc.start_date is None or pc.end_date is None:
            return False
        late_end = [d for d in expected if d > pc.end_date]
        return pc.start_date == expected[0] and len(late_end) <= 1

    def _window_basis(self, pc: PriceChangeData) -> str:
        if self._covers_window(pc):
            return "first and last trading days in the window"
        return (
            f"first and last available closes; the requested window is {fmt.date(pc.requested_start)} to "
            f"{fmt.date(pc.requested_end)}"
        )

    def _window_phrase(self, pc: PriceChangeData) -> str:
        if self._covers_window(pc):
            return f"Over the research window ({fmt.date(pc.start_date)} to {fmt.date(pc.end_date)})"
        return (
            f"Over the dates with price data ({fmt.date(pc.start_date)} to {fmt.date(pc.end_date)}, within the "
            f"requested window {fmt.date(pc.requested_start)} to {fmt.date(pc.requested_end)})"
        )

    # ------------------------------------------------------------------ market specialist
    def market_findings(self) -> list[ResearchClaim]:
        claims: list[ResearchClaim] = []
        pc, prices, quote = self.b.price_change, self.b.prices, self.b.quote
        agent = "market_data_specialist"
        if pc and pc.percentage_change is not None and pc.start_value is not None and pc.end_value is not None:
            word = "up" if pc.percentage_change > 0 else ("down" if pc.percentage_change < 0 else "unchanged at")
            change = "" if pc.percentage_change == 0 else f" {fmt.pct(abs(pc.percentage_change), signed=False)}"
            claims.append(
                self._claim(
                    BriefSection.MARKET_MOVEMENT,
                    ClaimType.CALCULATION,
                    f"{self.symbol} closed at {fmt.usd(pc.end_value)} on {fmt.date(pc.end_date)}, {word}{change} from "
                    f"{fmt.usd(pc.start_value)} on {fmt.date(pc.start_date)} ({self._window_basis(pc)}).",
                    [pc.evidence_id, *pc.input_evidence_ids],
                    agent,
                )
            )
        if quote:
            ev = self.b.ledger.get(quote.evidence_id)
            text = (
                f"The latest provider quote is {fmt.usd(quote.price)} for trading date {fmt.date(quote.observed_date)}; "
                "the provider reports no intraday time or market state."
            )
            if ev and ev.freshness and ev.freshness.status == FreshnessStatus.STALE:
                text += f" It is {ev.freshness.age_days} days older than the reference date and is marked stale."
            claims.append(
                self._claim(BriefSection.MARKET_MOVEMENT, ClaimType.SOURCE_FACT, text, [quote.evidence_id], agent)
            )
        for move, word in ((self.d.largest_decline, "decline"), (self.d.largest_gain, "gain")):
            if move and prices:
                m = move.move
                claims.append(
                    self._claim(
                        BriefSection.MARKET_MOVEMENT,
                        ClaimType.CALCULATION,
                        f"{self._moves_lead(word)} {fmt.pct(abs(m.percentage_change), signed=False)} "
                        f"on {m.trading_date.isoformat()} (close moved from {fmt.usd(m.previous_close)} on "
                        f"{m.previous_date.isoformat()} to {fmt.usd(m.close)}).",
                        [move.evidence_id, prices.evidence_id],
                        agent,
                        materiality="supporting",
                    )
                )
        if self.d.close_range and prices:
            r = self.d.close_range
            claims.append(
                self._claim(
                    BriefSection.MARKET_MOVEMENT,
                    ClaimType.CALCULATION,
                    f"Closing prices {'on the available trading days' if self._missing_bars() else 'in the window'} "
                    f"ranged from {fmt.usd(r.low.close)} on {r.low.trading_date.isoformat()} "
                    f"to {fmt.usd(r.high.close)} on {r.high.trading_date.isoformat()}.",
                    [r.evidence_id, prices.evidence_id],
                    agent,
                    materiality="supporting",
                )
            )
        return claims

    # ------------------------------------------------------------------ fundamentals specialist
    def _growth_sentence(self, growth: GrowthCalc, noun: str, period_word: str) -> str | None:
        if growth.percentage_change is None:
            return None
        word = fmt.direction(growth.percentage_change, "up", "down", "unchanged")
        amount = "" if growth.percentage_change == 0 else f" {fmt.pct(abs(growth.percentage_change), signed=False)}"
        prior = fmt.usd_compact(growth.prior.value) if growth.prior.unit == "USD" else fmt.per_share(growth.prior.value)
        return (
            f"{noun} was {word}{amount} from {prior} for the {period_word} ended {growth.prior.period_end.isoformat()}, "
            "the comparable period a year earlier."
        )

    def fundamentals_findings(self) -> list[ResearchClaim]:
        claims: list[ResearchClaim] = []
        agent = "fundamentals_specialist"
        revenue = self._latest("revenue")
        if revenue:
            claims.append(
                self._claim(
                    BriefSection.FUNDAMENTALS,
                    ClaimType.SOURCE_FACT,
                    f"Revenue for the quarter ended {revenue.period_end.isoformat()}{self._fiscal_label(revenue)} was "
                    f"{fmt.usd_compact(revenue.value)} (us-gaap:{revenue.concept}, Form {revenue.form} filed "
                    f"{fmt.date(revenue.filing_or_report_date)}).",
                    [revenue.evidence_id],
                    agent,
                )
            )
            growth = self.d.quarterly_growth.get("revenue")
            sentence = self._growth_sentence(growth, "Quarterly revenue", "quarter") if growth else None
            if growth and sentence:
                claims.append(
                    self._claim(
                        BriefSection.FUNDAMENTALS,
                        ClaimType.CALCULATION,
                        sentence,
                        [growth.evidence_id, growth.latest.evidence_id],
                        agent,
                    )
                )
        net = self._latest("net_income")
        if net:
            claims.append(
                self._claim(
                    BriefSection.FUNDAMENTALS,
                    ClaimType.SOURCE_FACT,
                    f"Net income for the quarter ended {net.period_end.isoformat()} was {fmt.usd_compact(net.value)}.",
                    [net.evidence_id],
                    agent,
                )
            )
            growth = self.d.quarterly_growth.get("net_income")
            sentence = self._growth_sentence(growth, "Quarterly net income", "quarter") if growth else None
            if growth and sentence:
                claims.append(
                    self._claim(
                        BriefSection.FUNDAMENTALS,
                        ClaimType.CALCULATION,
                        sentence,
                        [growth.evidence_id, growth.latest.evidence_id],
                        agent,
                        materiality="supporting",
                    )
                )
        eps = self._latest("diluted_eps")
        if eps:
            claims.append(
                self._claim(
                    BriefSection.FUNDAMENTALS,
                    ClaimType.SOURCE_FACT,
                    f"Diluted EPS for the quarter ended {eps.period_end.isoformat()} was {fmt.per_share(eps.value)} per share.",
                    [eps.evidence_id],
                    agent,
                    materiality="supporting",
                )
            )
        margin = self.d.operating_margin
        if margin:
            claims.append(
                self._claim(
                    BriefSection.FUNDAMENTALS,
                    ClaimType.CALCULATION,
                    f"Operating income was {fmt.pct(margin.margin_pct, signed=False)} of revenue in the quarter ended "
                    f"{margin.revenue.period_end.isoformat()} ({fmt.usd_compact(margin.operating_income.value)} of "
                    f"{fmt.usd_compact(margin.revenue.value)}).",
                    [margin.evidence_id, margin.revenue.evidence_id, margin.operating_income.evidence_id],
                    agent,
                    materiality="supporting",
                )
            )
        annual = self._latest_annual("revenue")
        if annual:
            claims.append(
                self._claim(
                    BriefSection.FUNDAMENTALS,
                    ClaimType.SOURCE_FACT,
                    f"Annual revenue for the fiscal year ended {annual.period_end.isoformat()} was "
                    f"{fmt.usd_compact(annual.value)} (Form {annual.form} filed {fmt.date(annual.filing_or_report_date)}).",
                    [annual.evidence_id],
                    agent,
                    materiality="supporting",
                )
            )
            growth = self.d.annual_growth.get("revenue")
            sentence = self._growth_sentence(growth, "Annual revenue", "fiscal year") if growth else None
            if growth and sentence:
                claims.append(
                    self._claim(
                        BriefSection.FUNDAMENTALS,
                        ClaimType.CALCULATION,
                        sentence,
                        [growth.evidence_id, growth.latest.evidence_id],
                        agent,
                        materiality="supporting",
                    )
                )
        if not revenue and self.b.metrics_fallback:
            for metric in self.b.metrics_fallback.metrics[:1]:
                claims.append(
                    self._claim(
                        BriefSection.FUNDAMENTALS,
                        ClaimType.SOURCE_FACT,
                        f"Provider-normalized {metric.label.lower()} for the period ended {metric.period_end.isoformat()} "
                        f"was {fmt.usd_compact(metric.value)} (not SEC-verified).",
                        [metric.evidence_id],
                        agent,
                    )
                )
        return claims

    # ------------------------------------------------------------------ filings (fundamentals specialist)
    def filings_findings(self) -> list[ResearchClaim]:
        claims: list[ResearchClaim] = []
        agent = "fundamentals_specialist"
        filings = self.b.filings.filings if self.b.filings else []
        periodic = [f for f in filings if f.form in ("10-Q", "10-K")]
        if periodic:
            latest = periodic[0]
            claims.append(
                self._claim(
                    BriefSection.FILINGS,
                    ClaimType.SOURCE_FACT,
                    f"The most recent periodic report retrieved is a Form {latest.form} for the period ended "
                    f"{fmt.date(latest.period_end)}, filed {latest.filed_date.isoformat()}.",
                    [latest.evidence_id],
                    agent,
                )
            )
            annual = next((f for f in periodic if f.form == "10-K"), None)
            if annual and annual is not latest:
                claims.append(
                    self._claim(
                        BriefSection.FILINGS,
                        ClaimType.SOURCE_FACT,
                        f"The most recent annual report retrieved is a Form 10-K for the fiscal year ended "
                        f"{fmt.date(annual.period_end)}, filed {annual.filed_date.isoformat()}.",
                        [annual.evidence_id],
                        agent,
                        materiality="supporting",
                    )
                )
        window_8k = [
            f
            for f in filings
            if f.form == "8-K" and self.b.horizon.start_date <= f.filed_date <= self.b.horizon.end_date
        ]
        if window_8k:
            dates = ", ".join(f.filed_date.isoformat() for f in window_8k)
            claims.append(
                self._claim(
                    BriefSection.FILINGS,
                    ClaimType.SOURCE_FACT,
                    f"Form 8-K current reports were filed on {dates}, inside the research window.",
                    [f.evidence_id for f in window_8k],
                    agent,
                    materiality="supporting",
                )
            )
        return claims

    def risk_quotes(self, limit: int = 3) -> list[ResearchClaim]:
        excerpt = self.b.excerpt
        if not excerpt or INJECTION_FLAG in excerpt.flags:
            return []
        claims = []
        paragraphs = [p.strip() for p in excerpt.excerpt.split("\n") if len(p.strip()) >= 80]
        for paragraph in paragraphs:
            sentence = _SENTENCE.split(paragraph)[0].strip()
            if "synthetic" in sentence.lower() and "illustrative" in sentence.lower():
                continue  # the fixture's own disclosure line is not a risk factor
            claims.append(
                self._claim(
                    BriefSection.RISKS,
                    ClaimType.SOURCE_FACT,
                    f"Risk factor quoted from the Form {excerpt.form} filed {fmt.date(excerpt.filed_date)}: “{sentence}”",
                    [excerpt.evidence_id],
                    "evidence_risk_reviewer",
                )
            )
            if len(claims) >= limit:
                break
        return claims

    # ------------------------------------------------------------------ news & events specialist
    def _coincident_news(self, move: MoveCalc | None) -> EvidenceItem | None:
        if move is None:
            return None
        day = move.move.trading_date
        candidates = [
            e
            for e in self._news_items()
            if e.published_date and dt.timedelta(0) <= day - e.published_date <= dt.timedelta(days=1)
        ]
        return sorted(candidates, key=lambda e: e.published_date or day)[-1] if candidates else None

    def news_findings(self) -> list[ResearchClaim]:
        claims: list[ResearchClaim] = []
        agent = "news_events_specialist"
        items = self._news_items()
        if items:
            dates = sorted(e.published_date for e in items if e.published_date)
            claims.append(
                self._claim(
                    BriefSection.RECENT_EVENTS,
                    ClaimType.SOURCE_FACT,
                    f"{len(items)} dated news items published between {dates[0].isoformat()} and {dates[-1].isoformat()} "
                    "were retrieved for the window; publication time zones are not specified by the provider.",
                    [e.evidence_id for e in items],
                    agent,
                    materiality="supporting",
                )
            )
        for move, word in ((self.d.largest_decline, "decline"), (self.d.largest_gain, "gain")):
            news = self._coincident_news(move)
            if move is None or news is None:
                continue
            same_day = news.published_date == move.move.trading_date
            relation = "coincided with" if same_day else "followed"
            claims.append(
                self._claim(
                    BriefSection.INTERPRETATION,
                    ClaimType.INTERPRETATION,
                    f"The largest single-day {word} on {move.move.trading_date.isoformat()} {relation} a dated news item "
                    f"published {news.published_date.isoformat()} (“{news.title}”). It may be relevant context, but timing "
                    "alone does not establish that the article caused the move.",
                    [move.evidence_id, news.evidence_id],
                    agent,
                    confidence_note="Same-day timing only; publication time zone unspecified.",
                )
            )
        return claims

    def event_entries(self) -> list[EventEntry]:
        decline_day = self.d.largest_decline.move.trading_date if self.d.largest_decline else None
        gain_day = self.d.largest_gain.move.trading_date if self.d.largest_gain else None
        entries = []
        for item in self._news_items(include_flagged=False):
            note = "Dated context inside the research window; relevance to price movement is not established."
            if (
                item.published_date
                and decline_day
                and dt.timedelta(0) <= decline_day - item.published_date <= dt.timedelta(days=1)
            ):
                note = "Published on or just before the largest decline; may be relevant context, not established as a cause."
            elif (
                item.published_date
                and gain_day
                and dt.timedelta(0) <= gain_day - item.published_date <= dt.timedelta(days=1)
            ):
                note = (
                    "Published on or just before the largest gain; may be relevant context, not established as a cause."
                )
            entries.append(
                EventEntry(
                    evidence_id=item.evidence_id,
                    headline=item.title or "(untitled)",
                    publisher=item.payload.get("publisher"),
                    url=item.source_url,
                    published_at=item.published_at,
                    published_at_tz_known=bool(item.published_at_tz_known),
                    retrieved_at=item.retrieved_at,
                    relevance_note=note,
                    in_requested_window=True,
                    flags=item.flags,
                )
            )
        return entries

    # ------------------------------------------------------------------ synthesis (supervisor)
    def synthesis(self) -> list[ResearchClaim]:
        claims: list[ResearchClaim] = []
        agent = "research_supervisor"
        pc = self.b.price_change
        revenue_growth = self.d.quarterly_growth.get("revenue")
        if pc and pc.percentage_change is not None:
            claims.append(
                self._claim(
                    BriefSection.EXECUTIVE_SUMMARY,
                    ClaimType.CALCULATION,
                    f"{self._window_phrase(pc)}, {self.symbol} "
                    f"{fmt.direction(pc.percentage_change)} {fmt.pct(abs(pc.percentage_change), signed=False)} on a "
                    "closing-price basis.",
                    [pc.evidence_id, *pc.input_evidence_ids],
                    agent,
                )
            )
        if revenue_growth and revenue_growth.percentage_change is not None:
            latest = revenue_growth.latest
            claims.append(
                self._claim(
                    BriefSection.EXECUTIVE_SUMMARY,
                    ClaimType.CALCULATION,
                    f"The latest reported quarter (ended {latest.period_end.isoformat()}) showed revenue of "
                    f"{fmt.usd_compact(latest.value)}, {fmt.direction(revenue_growth.percentage_change, 'up', 'down', 'flat')} "
                    f"{fmt.pct(abs(revenue_growth.percentage_change), signed=False)} year over year.",
                    [revenue_growth.evidence_id, latest.evidence_id],
                    agent,
                )
            )
        news = self._news_items()
        if news:
            claims.append(
                self._claim(
                    BriefSection.EXECUTIVE_SUMMARY,
                    ClaimType.INTERPRETATION,
                    f"{len(news)} dated news items in the window may be relevant context; the evidence does not establish "
                    "that any of them caused a price move.",
                    [e.evidence_id for e in news],
                    agent,
                )
            )
        # Positive / negative evidence (separated, source-linked)
        if pc and pc.percentage_change is not None and pc.percentage_change != 0:
            section = BriefSection.POSITIVE_EVIDENCE if pc.percentage_change > 0 else BriefSection.NEGATIVE_EVIDENCE
            claims.append(
                self._claim(
                    section,
                    ClaimType.CALCULATION,
                    f"Closing price {fmt.direction(pc.percentage_change)} {fmt.pct(abs(pc.percentage_change), signed=False)} "
                    "across the research window.",
                    [pc.evidence_id, *pc.input_evidence_ids],
                    agent,
                )
            )
        for metric, noun in (("revenue", "Quarterly revenue"), ("net_income", "Quarterly net income")):
            growth = self.d.quarterly_growth.get(metric)
            if growth and growth.percentage_change not in (None, Decimal(0)):
                positive = growth.percentage_change > 0
                claims.append(
                    self._claim(
                        BriefSection.POSITIVE_EVIDENCE if positive else BriefSection.NEGATIVE_EVIDENCE,
                        ClaimType.CALCULATION,
                        f"{noun} {'grew' if positive else 'declined'} "
                        f"{fmt.pct(abs(growth.percentage_change), signed=False)} year over year (quarter ended "
                        f"{growth.latest.period_end.isoformat()}).",
                        [growth.evidence_id, growth.latest.evidence_id],
                        agent,
                    )
                )
        if self.d.largest_decline and self.b.prices:
            m = self.d.largest_decline.move
            claims.append(
                self._claim(
                    BriefSection.NEGATIVE_EVIDENCE,
                    ClaimType.CALCULATION,
                    f"The window included a single-day decline of {fmt.pct(abs(m.percentage_change), signed=False)} on "
                    f"{m.trading_date.isoformat()}.",
                    [self.d.largest_decline.evidence_id, self.b.prices.evidence_id],
                    agent,
                    materiality="supporting",
                )
            )
        revenue = self._latest("revenue")
        if pc and revenue and pc.end_date:
            claims.append(
                self._claim(
                    BriefSection.INTERPRETATION,
                    ClaimType.INTERPRETATION,
                    f"Price data runs through {pc.end_date.isoformat()} while the latest reported quarter ended "
                    f"{revenue.period_end.isoformat()}; the periods differ, so the price move cannot be attributed to "
                    "reported results from this evidence alone.",
                    [pc.evidence_id, revenue.evidence_id],
                    agent,
                )
            )
        return claims

    # ------------------------------------------------------------------ metrics, gaps, notes, sources
    def key_metrics(self) -> list[KeyMetric]:
        rows: list[KeyMetric] = []
        prices, pc, quote = self.b.prices, self.b.price_change, self.b.quote
        if prices and prices.bars:
            last = prices.bars[-1]
            rows.append(
                KeyMetric(
                    label="Last close in window",
                    value=last.close,
                    display_value=fmt.usd(last.close),
                    unit="USD per share",
                    currency="USD",
                    as_of=last.trading_date.isoformat(),
                    evidence_ids=[prices.evidence_id],
                    claim_type=ClaimType.SOURCE_FACT,
                    group="market",
                )
            )
        if pc and pc.percentage_change is not None:
            rows.append(
                KeyMetric(
                    label="Price change (close)",
                    value=pc.percentage_change,
                    display_value=fmt.pct(pc.percentage_change),
                    unit="percent",
                    as_of=f"{fmt.date(pc.start_date)} → {fmt.date(pc.end_date)}",
                    evidence_ids=[pc.evidence_id, *pc.input_evidence_ids],
                    claim_type=ClaimType.CALCULATION,
                    group="market",
                )
            )
        if quote:
            rows.append(
                KeyMetric(
                    label="Latest provider quote",
                    value=quote.price,
                    display_value=fmt.usd(quote.price),
                    unit="USD per share",
                    currency="USD",
                    as_of=fmt.date(quote.observed_date),
                    evidence_ids=[quote.evidence_id],
                    claim_type=ClaimType.SOURCE_FACT,
                    group="market",
                    note="Trading date only; no intraday time reported.",
                )
            )
        for metric, label in (("revenue", "Quarterly revenue"), ("net_income", "Quarterly net income")):
            fact = self._latest(metric)
            if fact:
                rows.append(
                    KeyMetric(
                        label=label,
                        value=fact.value,
                        display_value=fmt.usd_compact(fact.value),
                        unit=fact.unit,
                        currency="USD",
                        as_of=f"Quarter ended {fact.period_end.isoformat()}",
                        period_label=f"{fmt.date(fact.period_start)} → {fact.period_end.isoformat()}",
                        evidence_ids=[fact.evidence_id],
                        claim_type=ClaimType.SOURCE_FACT,
                        group="fundamentals",
                    )
                )
        growth = self.d.quarterly_growth.get("revenue")
        if growth and growth.percentage_change is not None:
            rows.append(
                KeyMetric(
                    label="Revenue, year over year",
                    value=growth.percentage_change,
                    display_value=fmt.pct(growth.percentage_change),
                    unit="percent",
                    as_of=f"Quarter ended {growth.latest.period_end.isoformat()}",
                    evidence_ids=[growth.evidence_id, growth.latest.evidence_id, growth.prior.evidence_id],
                    claim_type=ClaimType.CALCULATION,
                    group="fundamentals",
                )
            )
        eps = self._latest("diluted_eps")
        if eps:
            rows.append(
                KeyMetric(
                    label="Diluted EPS (quarter)",
                    value=eps.value,
                    display_value=fmt.per_share(eps.value),
                    unit="USD per share",
                    currency="USD",
                    as_of=f"Quarter ended {eps.period_end.isoformat()}",
                    evidence_ids=[eps.evidence_id],
                    claim_type=ClaimType.SOURCE_FACT,
                    group="fundamentals",
                )
            )
        if self.d.operating_margin:
            m = self.d.operating_margin
            rows.append(
                KeyMetric(
                    label="Operating margin (quarter)",
                    value=m.margin_pct,
                    display_value=fmt.pct(m.margin_pct, signed=False),
                    unit="percent",
                    as_of=f"Quarter ended {m.revenue.period_end.isoformat()}",
                    evidence_ids=[m.evidence_id],
                    claim_type=ClaimType.CALCULATION,
                    group="fundamentals",
                )
            )
        annual = self._latest_annual("revenue")
        if annual:
            rows.append(
                KeyMetric(
                    label="Annual revenue",
                    value=annual.value,
                    display_value=fmt.usd_compact(annual.value),
                    unit="USD",
                    currency="USD",
                    as_of=f"Fiscal year ended {annual.period_end.isoformat()}",
                    evidence_ids=[annual.evidence_id],
                    claim_type=ClaimType.SOURCE_FACT,
                    group="fundamentals",
                )
            )
        return rows

    def unknowns(self) -> list[GapItem]:
        gaps: list[GapItem] = []
        labels = {
            ResearchCategory.MARKET: "Market data",
            ResearchCategory.FUNDAMENTALS: "Company fundamentals",
            ResearchCategory.FILINGS: "SEC filings",
            ResearchCategory.NEWS: "News",
        }
        for category in ResearchCategory:
            outcome = self.b.outcomes.get(category)
            if outcome is None or outcome.status in (CategoryStatus.AVAILABLE, CategoryStatus.NOT_REQUESTED):
                continue
            error = outcome.primary_error
            reason = error.message if error else "no details reported"
            verb = {"unavailable": "was not available", "failed": "could not be retrieved", "partial": "is incomplete"}
            gaps.append(
                GapItem(
                    category=category,
                    description=f"{labels[category]} {verb.get(outcome.status.value, 'is limited')}: {reason}",
                    reason_code=error.code.value if error else outcome.status.value,
                    severity="material",
                )
            )
        if self.b.quote:
            ev = self.b.ledger.get(self.b.quote.evidence_id)
            if ev and ev.freshness and ev.freshness.status == FreshnessStatus.STALE:
                gaps.append(
                    GapItem(
                        category=ResearchCategory.MARKET,
                        severity="material",
                        reason_code="STALE_DATA",
                        description="The latest quote is stale relative to the reference date; it is not presented as current.",
                    )
                )
        gaps.append(
            GapItem(
                description="No consensus estimates, analyst ratings or price targets were retrieved; this brief reports none.",
                reason_code="NOT_RETRIEVED",
            )
        )
        gaps.append(
            GapItem(
                description="The retrieved evidence does not establish the causes of individual price moves.",
                reason_code="CAUSATION_UNKNOWN",
            )
        )
        if self.b.prices and self.b.prices.adjustment != "adjusted":
            gaps.append(
                GapItem(
                    category=ResearchCategory.MARKET,
                    reason_code="UNADJUSTED_PRICES",
                    description="Prices are unadjusted (as-traded); splits and dividends are not adjusted for.",
                )
            )
        if self.b.quote:
            gaps.append(
                GapItem(
                    category=ResearchCategory.MARKET,
                    reason_code="NO_INTRADAY_TIME",
                    description="The provider reports a trading date but no intraday time or market state for the quote.",
                )
            )
        if self._news_items():
            gaps.append(
                GapItem(
                    category=ResearchCategory.NEWS,
                    reason_code="TIMEZONE_UNSPECIFIED",
                    description="News publication times have no time zone, so same-day alignment with price moves is approximate.",
                )
            )
        for warning in dict.fromkeys(self.b.tool_warnings):
            if "begins" in warning or "Adjusted" in warning or "Dropped" in warning:
                gaps.append(GapItem(category=ResearchCategory.MARKET, reason_code="COVERAGE", description=warning))
        if self.b.facts_quarterly:
            fallback = {m.metric_name for m in (self.b.metrics_fallback.metrics if self.b.metrics_fallback else [])}
            labels = {
                "revenue": "Revenue",
                "operating_income": "Operating income",
                "net_income": "Net income",
                "diluted_eps": "Diluted EPS",
            }
            for metric, label in labels.items():
                if not self._latest(metric) and metric not in fallback:
                    gaps.append(
                        GapItem(
                            category=ResearchCategory.FUNDAMENTALS,
                            reason_code="CONCEPT_MISSING",
                            description=f"{label} was not found in the retrieved SEC company facts under a standard "
                            "us-gaap concept; it is not reported or estimated.",
                        )
                    )
        return gaps

    def source_quality_notes(self) -> list[str]:
        notes = []
        for item in self._news_items(include_flagged=True):
            if INJECTION_FLAG in item.flags:
                notes.append(
                    f"News item “{item.title}” ({item.payload.get('publisher', 'unknown publisher')}, "
                    f"{fmt.date(item.published_date)}) contains instruction-like text (for example, requests to reveal "
                    "prompts, use tools or place trades). It was treated as untrusted data, excluded from the event "
                    "timeline and analysis, and no action was taken."
                )
        if self.b.excerpt and INJECTION_FLAG in self.b.excerpt.flags:
            notes.append(
                "The filing excerpt contains instruction-like text; it was treated as untrusted data and not quoted."
            )
        if self.b.news:
            notes.extend(n for n in self.b.news.notes if "excluded" in n)
        return notes

    def sources(self) -> list[SourceRef]:
        items = sorted(self.b.ledger.values(), key=lambda e: (KIND_ORDER.get(e.kind.value, 9), e.title or ""))
        return [
            SourceRef(
                evidence_id=e.evidence_id,
                kind=e.kind,
                title=e.title,
                source_name=e.source_name,
                source_url=e.source_url,
                published_at=e.published_at,
                period_start=e.period_start,
                period_end=e.period_end,
                retrieved_at=e.retrieved_at,
                is_fixture=e.is_fixture,
                source_mode=e.source_mode,
                flags=e.flags,
            )
            for e in items
            if e.kind != EvidenceKind.CALCULATION or e.evidence_id in self._cited_calcs()
        ]

    def _cited_calcs(self) -> set[str]:
        return {e.evidence_id for e in self.b.ledger.values() if e.kind == EvidenceKind.CALCULATION}

    def scenarios(self) -> list[ResearchClaim]:
        """At most one qualitative, conditional scenario tied to quoted filing risks (never a forecast)."""
        excerpt = self.b.excerpt
        if not excerpt or INJECTION_FLAG in excerpt.flags or not self.risk_quotes(limit=1):
            return []
        return [
            self._claim(
                BriefSection.SCENARIOS,
                ClaimType.SCENARIO,
                "If one or more of the risk factors quoted from the latest filing were to materialize, reported "
                "margins or revenue growth could come under pressure. This is a conditional possibility, not a "
                "forecast.",
                [excerpt.evidence_id],
                "research_supervisor",
                materiality="supporting",
                assumptions=[
                    "At least one quoted risk factor materially affects operations within the period considered."
                ],
            )
        ]

    def compose_all(self) -> list[ResearchClaim]:
        return [
            *self.synthesis(),
            *self.market_findings(),
            *self.fundamentals_findings(),
            *self.filings_findings(),
            *self.news_findings(),
            *self.risk_quotes(),
        ]
