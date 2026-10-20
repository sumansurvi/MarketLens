"""Market Data server business logic (MCP_TOOLS §4): quotes, prices, metrics, calculations.

Read-only. Every result carries provenance and fixture/live labeling; calculations are
deterministic and cite the price-series evidence they used.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

from marketlens_core.calculations import PRICE_CHANGE_FORMULA, price_change
from marketlens_core.config import Settings
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.fixtures import FixtureDataset, ReferenceClock, reference_clock
from marketlens_core.market_calendar import trading_days
from marketlens_core.models import EvidenceKind, FinancialMetric, FreshnessStatus, MarketQuote, PriceSeries, SourceMode
from marketlens_core.providers import alpha_vantage as av
from marketlens_core.providers.base import (
    ProviderPayload,
    build_evidence,
    freshness_for_date,
    reporting_period_freshness,
    source_name_for,
)
from marketlens_core.providers.fixture_transport import FixtureAlphaVantageTransport
from marketlens_core.status import CapabilityTracker
from marketlens_core.symbols import normalize_symbol
from marketlens_core.timeutil import MAX_HORIZON_DAYS, utc_now
from marketlens_core.tool_models import (
    CapabilityStatus,
    FinancialMetricsData,
    FinancialMetricsInput,
    HistoricalPricesData,
    HistoricalPricesInput,
    PriceChangeData,
    PriceChangeInput,
    QuoteData,
    ServerStatusData,
    SourceStatusEntry,
    SymbolInput,
)

SERVER_ID = "market_data"
CURRENCY_NOTE = "Currency is USD by NYSE/Nasdaq listing convention; the provider does not state it per value."
QUOTE_BASIS = (
    "Value for the latest trading date reported by the provider. The configured plan does not supply an "
    "intraday observation time or market state, so none is inferred."
)
QUOTE_STALE_DAYS = 4
SERIES_STALE_DAYS = 5


class MarketDataService:
    def __init__(
        self,
        settings: Settings,
        client: av.AlphaVantageClient,
        clock: Callable[[], ReferenceClock],
        *,
        fixture_dataset: FixtureDataset | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.clock = clock
        self.fixture_dataset = fixture_dataset
        self.tracker = CapabilityTracker()

    # ------------------------------------------------------------------ helpers
    def _symbol(self, raw: str) -> str:
        symbol = normalize_symbol(raw)
        if self.fixture_dataset is not None and symbol not in self.fixture_dataset.symbols:
            covered = ", ".join(sorted(self.fixture_dataset.symbols))
            raise MarketLensError(
                ErrorCode.UNKNOWN_SYMBOL,
                f"{symbol} is not in the demo fixture universe ({covered}).",
                details={"covered_symbols": sorted(self.fixture_dataset.symbols)},
            )
        return symbol

    async def _tracked(self, capability: str, coro):  # type: ignore[no-untyped-def]
        try:
            result = await coro
        except MarketLensError as exc:
            self.tracker.failure(capability, exc)
            raise
        self.tracker.success(capability)
        return result

    def _source_name(self, payload: ProviderPayload) -> str:
        return source_name_for(av.PROVIDER_LABEL, payload)

    # ------------------------------------------------------------------ A1 get_quote
    async def get_quote(self, args: SymbolInput) -> QuoteData:
        symbol = self._symbol(args.symbol)
        payload, parsed = await self._tracked("quote", self.client.global_quote(symbol))
        clock = self.clock()
        freshness = freshness_for_date(parsed.trading_date, clock.now, threshold_days=QUOTE_STALE_DAYS, what="quote")
        evidence = build_evidence(
            kind=EvidenceKind.QUOTE,
            payload=payload,
            provider=av.PROVIDER_ID,
            source_name=self._source_name(payload),
            identity={"symbol": symbol, "function": "GLOBAL_QUOTE"},
            content={"price": parsed.price, "date": parsed.trading_date, "previous_close": parsed.previous_close},
            symbol=symbol,
            title=f"{symbol} latest provider quote",
            source_url=self.client.source_url("GLOBAL_QUOTE", symbol),
            period_end=parsed.trading_date,
            currency="USD",
            units="USD per share",
            evidence_payload={
                "price": str(parsed.price),
                "trading_date": parsed.trading_date.isoformat() if parsed.trading_date else None,
                "previous_close": str(parsed.previous_close) if parsed.previous_close is not None else None,
                "volume": parsed.volume,
                "observation_basis": QUOTE_BASIS,
            },
            freshness=freshness,
            notes=[CURRENCY_NOTE],
        )
        quote = MarketQuote(
            symbol=symbol,
            price=parsed.price,
            currency="USD",
            observed_date=parsed.trading_date,
            observed_at=None,
            observation_basis=QUOTE_BASIS,
            provider=av.PROVIDER_ID,
            market_state="unknown",
            previous_close=parsed.previous_close,
            volume=parsed.volume,
            is_fixture=payload.is_fixture,
            evidence_id=evidence.evidence_id,
        )
        return QuoteData(**quote.model_dump(), evidence=[evidence])

    # ------------------------------------------------------------------ A2 get_historical_prices
    async def get_historical_prices(self, args: HistoricalPricesInput) -> HistoricalPricesData:
        symbol = self._symbol(args.symbol)
        start, end = args.start_date, args.end_date
        if end < start:
            raise MarketLensError(ErrorCode.INVALID_INPUT, "end_date cannot precede start_date.")
        if (end - start).days > MAX_HORIZON_DAYS:
            raise MarketLensError(
                ErrorCode.DATE_RANGE_TOO_LARGE, f"Price ranges are limited to {MAX_HORIZON_DAYS} days."
            )
        payload, parsed, warnings = await self._tracked(
            "daily_prices", self.client.daily_series(symbol, adjusted=args.adjusted)
        )
        bars = [b for b in parsed.bars if start <= b.trading_date <= end]
        available_from, available_to = parsed.bars[0].trading_date, parsed.bars[-1].trading_date
        if not bars:
            raise MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                f"No trading-day data between {start.isoformat()} and {end.isoformat()}. "
                f"Available provider data covers {available_from.isoformat()} to {available_to.isoformat()}.",
                details={"available_from": available_from.isoformat(), "available_to": available_to.isoformat()},
            )
        coverage = list(warnings)
        if available_from > start:
            coverage.append(
                f"Provider data begins {available_from.isoformat()}, after the requested start {start.isoformat()} "
                "(compact output covers the latest 100 trading days)."
            )
        coverage.extend(f"Dropped invalid bar — {reason}" for reason in parsed.dropped)
        observed = {b.trading_date for b in bars}
        span_start, span_end = max(start, available_from), min(end, available_to)
        missing = [d for d in trading_days(span_start, span_end) if d not in observed]
        if missing:
            shown = ", ".join(d.isoformat() for d in missing[:5]) + (" …" if len(missing) > 5 else "")
            coverage.append(
                f"Missing daily bars for {len(missing)} trading day(s) in the range: {shown}. Moves across "
                "missing days are not reported as single-day moves; no values are filled in."
            )
        clock = self.clock()
        last = bars[-1].trading_date
        evidence = build_evidence(
            kind=EvidenceKind.PRICE_SERIES,
            payload=payload,
            provider=av.PROVIDER_ID,
            source_name=self._source_name(payload),
            identity={
                "symbol": symbol,
                "interval": "daily",
                "adjustment": parsed.adjustment,
                "start": start,
                "end": end,
            },
            content=[b.model_dump(mode="json") for b in bars],
            symbol=symbol,
            title=f"{symbol} daily prices {bars[0].trading_date.isoformat()} to {last.isoformat()} ({parsed.adjustment})",
            source_url=self.client.source_url("TIME_SERIES_DAILY", symbol),
            period_start=bars[0].trading_date,
            period_end=last,
            currency="USD",
            units="USD per share; volume in shares",
            evidence_payload={
                "bar_count": len(bars),
                "adjustment": parsed.adjustment,
                "timezone": parsed.timezone,
                "first_date": bars[0].trading_date.isoformat(),
                "last_date": last.isoformat(),
                "bars": [b.model_dump(mode="json") for b in bars],
            },
            # A historical window is judged against its own end date, not today: a series that reaches the
            # requested end is complete for that window. Windows ending now are judged against the clock.
            freshness=freshness_for_date(
                last,
                min(clock.now, dt.datetime.combine(end, dt.time(23, 59, 59), tzinfo=dt.UTC)),
                threshold_days=SERIES_STALE_DAYS,
                what="latest daily bar",
            ),
            notes=[CURRENCY_NOTE, "Bars are keyed by exchange trading date (US/Eastern); no intraday time is implied."],
        )
        series = PriceSeries(
            symbol=symbol,
            currency="USD",
            adjustment=parsed.adjustment,  # type: ignore[arg-type]
            timezone=parsed.timezone or "unknown",
            bars=bars,
            requested_start=start,
            requested_end=end,
            first_date=bars[0].trading_date,
            last_date=last,
            provider=av.PROVIDER_ID,
            is_fixture=payload.is_fixture,
            evidence_id=evidence.evidence_id,
            coverage_warnings=coverage,
        )
        return HistoricalPricesData(**series.model_dump(), evidence=[evidence])

    # ------------------------------------------------------------------ A4 calculate_price_change
    async def calculate_price_change(self, args: PriceChangeInput) -> PriceChangeData:
        symbol = self._symbol(args.symbol)
        series_evidence = []
        try:
            series = await self.get_historical_prices(
                HistoricalPricesInput(symbol=symbol, start_date=args.start_date, end_date=args.end_date)
            )
        except MarketLensError as exc:
            if exc.code != ErrorCode.DATA_UNAVAILABLE:
                raise
            bars, input_ids, is_fixture, source_mode = [], [], self.client.source_mode == SourceMode.FIXTURE, None
            unavailable = exc.message
        else:
            bars, input_ids = series.bars, [series.evidence_id]
            series_evidence = series.evidence
            is_fixture, source_mode, unavailable = series.is_fixture, series.evidence[0].source_mode, None
        result = price_change(bars, args.start_date, args.end_date, args.price_field)
        reason = unavailable or result.unavailable_reason
        calc_payload = ProviderPayload(
            data=None,
            source_mode=source_mode or self.client.source_mode,
            retrieved_at=utc_now(),
            request_label=f"calculation:price_change:{symbol}",
        )
        values = {
            "start_value": result.start_value,
            "start_date": result.start_date,
            "end_value": result.end_value,
            "end_date": result.end_date,
            "absolute_change": result.absolute_change,
            "percentage_change": result.percentage_change,
        }
        evidence = build_evidence(
            kind=EvidenceKind.CALCULATION,
            payload=calc_payload,
            provider="marketlens",
            source_name="MarketLens deterministic calculation",
            identity={
                "symbol": symbol,
                "calc": "price_change",
                "field": args.price_field,
                "start": args.start_date,
                "end": args.end_date,
                "inputs": input_ids,
            },
            content=values,
            symbol=symbol,
            title=f"{symbol} {args.price_field} price change {args.start_date.isoformat()} to {args.end_date.isoformat()}",
            period_start=result.start_date,
            period_end=result.end_date,
            currency="USD",
            units="percent; absolute change in USD per share",
            evidence_payload={
                "formula": PRICE_CHANGE_FORMULA,
                "price_field": args.price_field,
                "input_evidence_ids": input_ids,
                "requested_start": args.start_date.isoformat(),
                "requested_end": args.end_date.isoformat(),
                **{
                    k: (v.isoformat() if isinstance(v, dt.date) else (str(v) if isinstance(v, Decimal) else v))
                    for k, v in values.items()
                },
                "unavailable_reason": reason,
                "selection_rule": "first observation on/after start date; last observation on/before end date",
            },
            notes=result.warnings,
        )
        return PriceChangeData(
            symbol=symbol,
            price_field=args.price_field,
            requested_start=args.start_date,
            requested_end=args.end_date,
            start_value=result.start_value,
            start_date=result.start_date,
            end_value=result.end_value,
            end_date=result.end_date,
            absolute_change=result.absolute_change,
            percentage_change=result.percentage_change,
            formula=PRICE_CHANGE_FORMULA,
            input_evidence_ids=input_ids,
            currency="USD",
            unavailable_reason=reason,
            warnings=result.warnings,
            is_fixture=is_fixture,
            evidence_id=evidence.evidence_id,
            evidence=[evidence, *series_evidence],
        )

    # ------------------------------------------------------------------ A3 get_financial_metrics
    async def get_financial_metrics(self, args: FinancialMetricsInput) -> FinancialMetricsData:
        symbol = self._symbol(args.symbol)
        income_metrics = [m for m in args.metrics if m in av.INCOME_METRICS]
        wants_eps = "reported_eps" in args.metrics
        notes = [av.UNSUPPORTED_BY_PROVIDER[m] for m in args.metrics if m in av.UNSUPPORTED_BY_PROVIDER]
        missing = [m for m in args.metrics if m in av.UNSUPPORTED_BY_PROVIDER]
        parsed: list[av.ParsedMetricValue] = []
        payloads: dict[str, ProviderPayload] = {}
        if income_metrics:
            payload = await self._tracked("fundamentals", self.client.income_statement(symbol))
            values, missing_income = av.parse_income_statement(
                payload.data, income_metrics, args.period_type, args.limit
            )
            parsed.extend(values)
            missing.extend(missing_income)
            payloads["INCOME_STATEMENT"] = payload
        if wants_eps:
            payload = await self._tracked("fundamentals", self.client.earnings(symbol))
            eps_values = av.parse_earnings(payload.data, args.period_type, args.limit)
            if not eps_values:
                missing.append("reported_eps")
            parsed.extend(eps_values)
            payloads["EARNINGS"] = payload
        metrics: list[FinancialMetric] = []
        evidence = []
        clock = self.clock()
        for metric_name in dict.fromkeys(v.metric_name for v in parsed):
            series = [v for v in parsed if v.metric_name == metric_name]
            function = "EARNINGS" if metric_name == "reported_eps" else "INCOME_STATEMENT"
            payload = payloads[function]
            unit = "USD/shares" if metric_name == "reported_eps" else (series[0].currency or "USD")
            latest = max(v.period_end for v in series)
            # Latest or nothing (D-85). This endpoint doesn't say which fiscal quarter a period is,
            # so the quarterly allowance covers the gap after a third quarter (Q4 is only in the 10-K).
            freshness = reporting_period_freshness(
                latest,
                clock.now,
                period_type=args.period_type,
                fiscal_period="Q3" if args.period_type == "quarterly" else None,
                what=f"latest {args.period_type} {series[0].label.lower()} period",
            )
            if freshness.status == FreshnessStatus.STALE:
                missing.append(metric_name)
                continue
            item = build_evidence(
                kind=EvidenceKind.FINANCIAL_METRIC,
                payload=payload,
                provider=av.PROVIDER_ID,
                source_name=self._source_name(payload),
                identity={
                    "symbol": symbol,
                    "function": function,
                    "metric": metric_name,
                    "period_type": args.period_type,
                },
                content=[(v.period_end, v.value) for v in series],
                symbol=symbol,
                title=f"{symbol} {series[0].label} ({args.period_type}, provider-normalized)",
                source_url=self.client.source_url(function, symbol),
                period_start=min(v.period_end for v in series),
                period_end=latest,
                currency="USD",
                units=unit,
                evidence_payload={
                    "metric_name": metric_name,
                    "provider_field": series[0].field,
                    "period_type": args.period_type,
                    "values": [{"period_end": v.period_end.isoformat(), "value": str(v.value)} for v in series],
                },
                freshness=freshness,
                notes=["Provider-normalized fundamentals; period start dates are not supplied by this endpoint."],
            )
            evidence.append(item)
            for v in series:
                metrics.append(
                    FinancialMetric(
                        metric_name=metric_name,
                        label=v.label,
                        value=v.value,
                        unit=unit,
                        currency=v.currency,
                        period_type=args.period_type,
                        period_start=None,
                        period_end=v.period_end,
                        filing_or_report_date=v.reported_date,
                        concept=v.field,
                        taxonomy="alpha_vantage",
                        provider=av.PROVIDER_ID,
                        evidence_id=item.evidence_id,
                        is_fixture=payload.is_fixture,
                    )
                )
        is_fixture = all(p.is_fixture for p in payloads.values()) if payloads else self.fixture_dataset is not None
        return FinancialMetricsData(
            symbol=symbol,
            period_type=args.period_type,
            metrics=metrics,
            missing_metrics=list(dict.fromkeys(missing)),
            notes=notes,
            provider=av.PROVIDER_ID,
            is_fixture=is_fixture,
            evidence=evidence,
        )

    # ------------------------------------------------------------------ A5 get_provider_status
    async def get_provider_status(self) -> ServerStatusData:
        clock = self.clock()
        fixture = self.fixture_dataset is not None
        limited, retry_after = self.tracker.rate_limit_state()
        capabilities = [
            CapabilityStatus(name="quote", available=True if fixture else self.tracker.available("quote")),
            CapabilityStatus(
                name="daily_prices", available=True if fixture else self.tracker.available("daily_prices")
            ),
            CapabilityStatus(
                name="adjusted_daily_prices",
                available=False if fixture else self.tracker.available("adjusted_daily_prices"),
                note="Premium on Alpha Vantage; fixture mode simulates the free plan.",
            ),
            CapabilityStatus(
                name="fundamentals", available=True if fixture else self.tracker.available("fundamentals")
            ),
            CapabilityStatus(name="price_change_calculation", available=True),
        ]
        limitations = [
            "Free-plan quotes update at the end of each trading day; no intraday time or market state is reported.",
            "Compact daily series cover the latest 100 trading days.",
            "Reported EPS from this provider does not state a basis; diluted EPS comes from SEC data.",
        ]
        if fixture:
            limitations.insert(0, "Demo fixtures: synthetic values, not market data.")
        return ServerStatusData(
            server=SERVER_ID,
            data_mode=self.settings.data_mode,
            is_fixture=fixture,
            fixture_dataset_version=self.fixture_dataset.version if self.fixture_dataset else None,
            reference_time=clock.now,
            reference_basis=clock.basis,
            sources=[
                SourceStatusEntry(
                    provider=av.PROVIDER_ID,
                    configured=fixture or self.settings.alpha_vantage_configured,
                    source_mode=SourceMode.FIXTURE if fixture else SourceMode.LIVE,
                    capabilities=capabilities,
                    last_successful_retrieval=self.tracker.latest_success,
                    last_error_code=self.tracker.most_recent_error,
                    rate_limited=limited,
                    retry_after_seconds=retry_after,
                    limitations=limitations,
                )
            ],
        )


def build_market_data_service(settings: Settings) -> MarketDataService:
    """Construct the service for the configured data mode."""
    if settings.is_fixture_data:
        dataset = FixtureDataset(settings.fixture_root)
        client = av.AlphaVantageClient(FixtureAlphaVantageTransport(dataset), fixture_url_base=dataset.url_base)
        return MarketDataService(settings, client, lambda: reference_clock(settings, dataset), fixture_dataset=dataset)
    from marketlens_core.db import initialize_database, open_database
    from marketlens_core.providers.live_transport import LiveAlphaVantageTransport, ProviderCache

    db = open_database(settings)
    initialize_database(db)
    client = av.AlphaVantageClient(LiveAlphaVantageTransport(settings, cache=ProviderCache(db)))
    return MarketDataService(settings, client, lambda: reference_clock(settings))
