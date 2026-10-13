"""Evidence collection: call the read-only tools for each category and build the ledger.

Categories run concurrently with a bounded semaphore and a hard call budget
(``MAX_PROVIDER_CALLS``). Every outcome — including capability gaps and failures — is
recorded per category so the brief can state exactly what was and was not retrieved.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from marketlens_core.config import Settings
from marketlens_core.errors import ErrorCode, ErrorInfo
from marketlens_core.hashing import new_id
from marketlens_core.market_calendar import trading_days
from marketlens_core.models import (
    CategoryStatus,
    CompanyIdentity,
    DataSourceStatus,
    EvidenceItem,
    ResearchCategory,
    SourceMode,
    TimeHorizon,
    ToolCallRecord,
)
from marketlens_core.providers.sec_edgar import DEFAULT_CONCEPTS
from marketlens_core.timeutil import utc_now
from marketlens_core.tool_models import (
    CompanyFactsData,
    FilingExcerptData,
    FilingsData,
    FinancialMetricsData,
    HistoricalPricesData,
    NewsData,
    PriceChangeData,
    QuoteData,
    ToolMeta,
    ToolResult,
)
from pydantic import ValidationError

from app.mcp.gateway import ToolCallOutcome, ToolGateway

FILINGS_LOOKBACK_DAYS = 456  # ~15 months: always reaches the latest 10-K and 10-Q
EXCERPT_CHARS = 2500


@dataclass
class CategoryOutcome:
    category: ResearchCategory
    status: CategoryStatus = CategoryStatus.NOT_REQUESTED
    tools: list[str] = field(default_factory=list)
    errors: list[ErrorInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary_error(self) -> ErrorInfo | None:
        return self.errors[0] if self.errors else None


@dataclass
class EvidenceBundle:
    company: CompanyIdentity
    horizon: TimeHorizon
    reference_time: dt.datetime
    reference_basis: str
    fixture_dataset_version: str | None = None
    quote: QuoteData | None = None
    prices: HistoricalPricesData | None = None
    price_change: PriceChangeData | None = None
    facts_quarterly: CompanyFactsData | None = None
    facts_annual: CompanyFactsData | None = None
    metrics_fallback: FinancialMetricsData | None = None
    filings: FilingsData | None = None
    excerpt: FilingExcerptData | None = None
    news: NewsData | None = None
    outcomes: dict[ResearchCategory, CategoryOutcome] = field(default_factory=dict)
    ledger: dict[str, EvidenceItem] = field(default_factory=dict)
    tool_warnings: list[str] = field(default_factory=list)

    def add_evidence(self, items: list[EvidenceItem]) -> None:
        for item in items:
            self.ledger.setdefault(item.evidence_id, item)

    @property
    def source_modes(self) -> set[SourceMode]:
        return {e.source_mode for e in self.ledger.values()}

    def data_source_statuses(self) -> list[DataSourceStatus]:
        statuses = []
        for category in ResearchCategory:
            outcome = self.outcomes.get(category, CategoryOutcome(category))
            items = [e for e in self.ledger.values() if _category_of(e) == category]
            error = outcome.primary_error
            latest = None
            dates = [e.period_end or e.published_date for e in items if (e.period_end or e.published_date)]
            if dates:
                latest = max(dates).isoformat()
            statuses.append(
                DataSourceStatus(
                    category=category,
                    status=outcome.status,
                    source_name=items[0].source_name if items else None,
                    source_mode=items[0].source_mode if items else None,
                    reason_code=error.code if error else None,
                    message=error.message if error else ("; ".join(outcome.notes) or None),
                    evidence_count=len(items),
                    latest_observation=latest,
                    retrieved_at=max((e.retrieved_at for e in items), default=None),
                    tools=outcome.tools,
                )
            )
        return statuses


def _category_of(item: EvidenceItem) -> ResearchCategory | None:
    kind = item.kind.value
    if kind in ("price_series", "quote"):
        return ResearchCategory.MARKET
    if kind == "calculation":
        return (
            ResearchCategory.FUNDAMENTALS if item.payload.get("category") == "fundamentals" else ResearchCategory.MARKET
        )
    if kind == "financial_metric":
        return ResearchCategory.FUNDAMENTALS
    if kind == "filing":
        return ResearchCategory.FILINGS
    if kind == "news":
        return ResearchCategory.NEWS
    return None


def coverage_shortfall(prices: HistoricalPricesData, start: dt.date, end: dt.date) -> str | None:
    """Explain when daily prices do not cover the requested window (None when they do).

    Every trading day missing at the start counts. At the end, one missing trading day is
    tolerated because the latest session may not be published yet."""
    expected = trading_days(start, end)
    observed = {b.trading_date for b in prices.bars}
    missing_start = [d for d in expected if d < prices.first_date]
    missing_end = [d for d in expected if d > prices.last_date]
    inside = [d for d in expected if prices.first_date < d < prices.last_date and d not in observed]
    if len(missing_end) <= 1:
        missing_end = []
    if not missing_start and not missing_end and not inside:
        return None
    parts = [f"{len(missing_start)} trading day(s) at the start"] if missing_start else []
    parts += [f"{len(missing_end)} at the end"] if missing_end else []
    if inside:
        shown = ", ".join(d.isoformat() for d in inside[:5]) + (" …" if len(inside) > 5 else "")
        parts.append(f"{len(inside)} inside the window ({shown})")
    return (
        f"Daily prices for {start.isoformat()} to {end.isoformat()} are incomplete: {' and '.join(parts)} without "
        f"data (covered {prices.first_date.isoformat()} to {prices.last_date.isoformat()}). Price figures describe "
        "the available dates only, and moves across missing days are not computed."
    )


def _status_from(results: list[ToolCallOutcome]) -> CategoryStatus:
    if not results:
        return CategoryStatus.NOT_REQUESTED
    oks = [r for r in results if r.ok]
    if len(oks) == len(results):
        return CategoryStatus.AVAILABLE
    if oks:
        return CategoryStatus.PARTIAL
    codes = {r.error.code for r in results if r.error}
    if codes and codes <= {ErrorCode.CAPABILITY_UNAVAILABLE}:
        return CategoryStatus.UNAVAILABLE
    return CategoryStatus.FAILED


class EvidenceCollector:
    def __init__(
        self,
        gateway: ToolGateway,
        settings: Settings,
        on_tool_call: Callable[[ToolCallRecord], None],
        *,
        seq_start: int = 1,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.on_tool_call = on_tool_call
        self._seq = itertools.count(seq_start)
        self._calls = 0
        self._semaphore = asyncio.Semaphore(3)

    @property
    def calls_made(self) -> int:
        return self._calls

    async def call(
        self, bundle: EvidenceBundle | None, run_id: str | None, server: str, tool: str, args: dict[str, Any]
    ) -> ToolCallOutcome:
        if self._calls >= self.settings.max_provider_calls:
            error = ErrorInfo(code=ErrorCode.BUDGET_EXCEEDED, message="The per-run tool-call budget was exhausted.")
            result: ToolResult[Any] = ToolResult(
                ok=False, error=error, meta=ToolMeta(request_id=new_id("req"), server=server, tool=tool)
            )
            record = ToolCallRecord(
                call_id=new_id("call"),
                run_id=run_id,
                seq=next(self._seq),
                server_id=server,
                tool_name=tool,
                transport=self.gateway.transport,
                status="error",
                error_code=ErrorCode.BUDGET_EXCEEDED,
                started_at=utc_now(),
                ended_at=utc_now(),
                output_summary={"ok": False, "error_code": "BUDGET_EXCEEDED"},
            )
            self.on_tool_call(record)
            return ToolCallOutcome(result=result, record=record)
        self._calls += 1
        async with self._semaphore:
            outcome = await self.gateway.call(server, tool, args, run_id=run_id, seq=next(self._seq))
        self.on_tool_call(outcome.record)
        if outcome.ok and bundle is not None and isinstance(outcome.data, dict):
            try:
                bundle.add_evidence([EvidenceItem.model_validate(e) for e in outcome.data.get("evidence", [])])
            except ValidationError:
                outcome.result.ok = False
                outcome.result.error = ErrorInfo(
                    code=ErrorCode.SCHEMA_VALIDATION_FAILED, message="A tool returned evidence that failed validation."
                )
        if outcome.ok:
            bundle_warnings = outcome.result.meta.warnings
            if bundle is not None and bundle_warnings:
                bundle.tool_warnings.extend(bundle_warnings)
        return outcome

    @staticmethod
    def parse(outcome: ToolCallOutcome, model: type[Any]) -> Any | None:
        """Validate a successful tool payload against its contract model."""
        if not outcome.ok:
            return None
        try:
            return model.model_validate(outcome.data)
        except ValidationError:
            outcome.result.ok = False
            outcome.result.error = ErrorInfo(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED, message="A tool result did not match its contract."
            )
            return None

    async def collect(self, run_id: str, bundle: EvidenceBundle) -> EvidenceBundle:
        await asyncio.gather(
            self._market(run_id, bundle),
            self._fundamentals(run_id, bundle),
            self._filings(run_id, bundle),
            self._news(run_id, bundle),
        )
        return bundle

    # ------------------------------------------------------------------ categories
    async def _market(self, run_id: str, bundle: EvidenceBundle) -> None:
        symbol, horizon = bundle.company.symbol, bundle.horizon
        window = {
            "symbol": symbol,
            "start_date": horizon.start_date.isoformat(),
            "end_date": horizon.end_date.isoformat(),
        }
        quote, prices, change = await asyncio.gather(
            self.call(bundle, run_id, "market_data", "get_quote", {"symbol": symbol}),
            self.call(
                bundle,
                run_id,
                "market_data",
                "get_historical_prices",
                {**window, "interval": "daily", "adjusted": False},
            ),
            self.call(bundle, run_id, "market_data", "calculate_price_change", {**window, "price_field": "close"}),
        )
        bundle.quote = self.parse(quote, QuoteData)
        bundle.prices = self.parse(prices, HistoricalPricesData)
        bundle.price_change = self.parse(change, PriceChangeData)
        results = [quote, prices, change]
        status = _status_from(results)
        errors = [r.error for r in results if r.error]
        shortfall = coverage_shortfall(bundle.prices, horizon.start_date, horizon.end_date) if bundle.prices else None
        if shortfall and status == CategoryStatus.AVAILABLE:
            status = CategoryStatus.PARTIAL
            errors.append(ErrorInfo(code=ErrorCode.DATA_UNAVAILABLE, message=shortfall))
        bundle.outcomes[ResearchCategory.MARKET] = CategoryOutcome(
            ResearchCategory.MARKET,
            status,
            tools=["get_quote", "get_historical_prices", "calculate_price_change"],
            errors=errors,
        )

    async def _fundamentals(self, run_id: str, bundle: EvidenceBundle) -> None:
        symbol = bundle.company.symbol
        concepts = list(DEFAULT_CONCEPTS)
        quarterly, annual = await asyncio.gather(
            self.call(
                bundle,
                run_id,
                "research_sources",
                "get_sec_company_facts",
                {"symbol": symbol, "concepts": concepts, "period_type": "quarterly", "limit": 8},
            ),
            self.call(
                bundle,
                run_id,
                "research_sources",
                "get_sec_company_facts",
                {"symbol": symbol, "concepts": concepts, "period_type": "annual", "limit": 3},
            ),
        )
        bundle.facts_quarterly = self.parse(quarterly, CompanyFactsData)
        bundle.facts_annual = self.parse(annual, CompanyFactsData)
        results = [quarterly, annual]
        tools = ["get_sec_company_facts"]
        notes: list[str] = []
        has_revenue = bool(
            bundle.facts_quarterly and any(f.metric_name == "revenue" for f in bundle.facts_quarterly.facts)
        )
        if not has_revenue:
            # Decision D-21: provider-normalized metrics only when SEC facts are unavailable.
            fallback = await self.call(
                bundle,
                run_id,
                "market_data",
                "get_financial_metrics",
                {
                    "symbol": symbol,
                    "metrics": ["revenue", "operating_income", "net_income", "reported_eps"],
                    "period_type": "quarterly",
                    "limit": 4,
                },
            )
            bundle.metrics_fallback = self.parse(fallback, FinancialMetricsData)
            results.append(fallback)
            tools.append("get_financial_metrics")
            notes.append("SEC revenue facts were unavailable; provider-normalized metrics were requested instead.")
        status = _status_from(results)
        if status == CategoryStatus.PARTIAL and bundle.facts_quarterly and bundle.facts_annual:
            status = CategoryStatus.AVAILABLE
        bundle.outcomes[ResearchCategory.FUNDAMENTALS] = CategoryOutcome(
            ResearchCategory.FUNDAMENTALS,
            status,
            tools=tools,
            errors=[r.error for r in results if r.error],
            notes=notes,
        )

    async def _filings(self, run_id: str, bundle: EvidenceBundle) -> None:
        symbol = bundle.company.symbol
        filed_after = bundle.horizon.end_date - dt.timedelta(days=FILINGS_LOOKBACK_DAYS)
        listing = await self.call(
            bundle,
            run_id,
            "research_sources",
            "search_company_filings",
            {"symbol": symbol, "forms": ["10-K", "10-Q", "8-K"], "limit": 10, "filed_after": filed_after.isoformat()},
        )
        bundle.filings = self.parse(listing, FilingsData)
        results = [listing]
        tools = ["search_company_filings"]
        periodic = None
        if bundle.filings:
            periodic = next(
                (
                    f
                    for f in bundle.filings.filings
                    if f.form in ("10-Q", "10-K") and f.filed_date <= bundle.horizon.end_date and f.source_url
                ),
                None,
            )
        if periodic is not None:
            excerpt = await self.call(
                bundle,
                run_id,
                "research_sources",
                "get_filing_excerpt",
                {"filing_url": periodic.source_url, "section_hint": "Risk Factors", "max_chars": EXCERPT_CHARS},
            )
            bundle.excerpt = self.parse(excerpt, FilingExcerptData)
            results.append(excerpt)
            tools.append("get_filing_excerpt")
        bundle.outcomes[ResearchCategory.FILINGS] = CategoryOutcome(
            ResearchCategory.FILINGS, _status_from(results), tools=tools, errors=[r.error for r in results if r.error]
        )

    async def _news(self, run_id: str, bundle: EvidenceBundle) -> None:
        news = await self.call(
            bundle,
            run_id,
            "research_sources",
            "search_company_news",
            {
                "symbol": bundle.company.symbol,
                "query": "",
                "from_date": bundle.horizon.start_date.isoformat(),
                "to_date": bundle.horizon.end_date.isoformat(),
                "limit": 10,
            },
        )
        bundle.news = self.parse(news, NewsData)
        outcome = CategoryOutcome(
            ResearchCategory.NEWS,
            _status_from([news]),
            tools=["search_company_news"],
            errors=[news.error] if news.error else [],
        )
        if bundle.news and bundle.news.notes:
            outcome.notes.extend(n for n in bundle.news.notes if "excluded" in n)
        bundle.outcomes[ResearchCategory.NEWS] = outcome
