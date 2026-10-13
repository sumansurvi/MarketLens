"""Alpha Vantage adapter: endpoint mapping, response classification and parsing.

All endpoint knowledge lives in this module (architecture §3.5). Observations from the
provider's ``demo`` responses (decision D-22):
- Errors and plan limits arrive as HTTP 200 bodies with ``Information``, ``Note`` or
  ``Error Message`` keys and must be classified, not parsed as data.
- ``GLOBAL_QUOTE`` gives a trading *date* only; free-tier quotes update at the end of
  each trading day, so no intraday time or market state is inferred.
- Daily series are keyed by US/Eastern trading date; ``outputsize=compact`` returns the
  latest 100 bars; adjusted series are premium.
- News ``time_published`` (``YYYYMMDDTHHMMSS``) carries no time zone.
- ``EARNINGS.reportedEPS`` has no stated basis, so it is exposed as ``reported_eps`` and
  never relabeled as diluted EPS. Estimate fields are ignored (no consensus figures).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.models import PriceBar, SourceMode
from marketlens_core.providers.base import ProviderPayload

PROVIDER_ID = "alpha_vantage"
PROVIDER_LABEL = "Alpha Vantage"
DOC_URLS = {
    "TIME_SERIES_DAILY": "https://www.alphavantage.co/documentation/#daily",
    "TIME_SERIES_DAILY_ADJUSTED": "https://www.alphavantage.co/documentation/#dailyadj",
    "GLOBAL_QUOTE": "https://www.alphavantage.co/documentation/#latestprice",
    "NEWS_SENTIMENT": "https://www.alphavantage.co/documentation/#news-sentiment",
    "INCOME_STATEMENT": "https://www.alphavantage.co/documentation/#income-statement",
    "EARNINGS": "https://www.alphavantage.co/documentation/#earnings",
}

# metric_name -> (INCOME_STATEMENT field, label)
INCOME_METRICS = {
    "revenue": ("totalRevenue", "Total revenue"),
    "gross_profit": ("grossProfit", "Gross profit"),
    "operating_income": ("operatingIncome", "Operating income"),
    "net_income": ("netIncome", "Net income"),
    "ebitda": ("ebitda", "EBITDA (provider-calculated)"),
}
EPS_METRICS = {"reported_eps": "Reported EPS (provider definition; basis not stated)"}
UNSUPPORTED_BY_PROVIDER = {
    "diluted_eps": "Alpha Vantage does not label EPS as diluted; use SEC EarningsPerShareDiluted."
}
ALLOWED_METRICS = frozenset(INCOME_METRICS) | frozenset(EPS_METRICS) | frozenset(UNSUPPORTED_BY_PROVIDER)

_MISSING = {"", "None", "none", "null", "-", "N/A"}


class AlphaVantageTransport(Protocol):
    source_mode: SourceMode

    async def query(self, params: dict[str, str]) -> ProviderPayload: ...


def classify_message(data: Any) -> MarketLensError | None:
    """Translate Alpha Vantage message-only bodies into structured errors."""
    if not isinstance(data, dict):
        return MarketLensError(ErrorCode.MALFORMED_PROVIDER_RESPONSE, "Market-data provider returned non-JSON data.")
    message_keys = [k for k in ("Error Message", "Information", "Note") if k in data]
    if not message_keys or len(data) > 2:
        return None
    key = message_keys[0]
    text = str(data.get(key, ""))
    low = text.lower()
    diagnostic = {"provider_message": re.sub(r"[A-Z0-9]{12,}", "[redacted]", text)[:240], "provider_key": key}
    if any(s in low for s in ("rate limit", "requests per", "call frequency", "spreading out", "per day")):
        # Free keys have a burst limit (about one request per second) and a daily quota; older
        # messages describe a per-minute frequency. Only the daily quota does not clear on its own.
        if any(s in low for s in ("per second", "spreading out", "burst")):
            limit = "burst"
        elif "per minute" in low or "call frequency" in low:
            limit = "minute"
        elif "per day" in low or "daily" in low:
            limit = "daily"
        else:
            limit = "unspecified"
        if limit == "daily":
            return MarketLensError(
                ErrorCode.RATE_LIMITED,
                "The Alpha Vantage daily request limit for this API key has been reached; new requests work again tomorrow.",
                retryable=False,
                details={**diagnostic, "limit": limit},
            )
        return MarketLensError(
            ErrorCode.RATE_LIMITED,
            "The market-data provider temporarily limited requests.",
            retry_after_seconds=60,
            details={**diagnostic, "limit": limit},
        )
    if "premium" in low or "subscribe" in low or "entitlement" in low:
        return MarketLensError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "This market-data capability is not included in the configured provider plan.",
            details=diagnostic,
        )
    if ("apikey" in low or "api key" in low) and any(s in low for s in ("invalid", "missing", "demo")):
        return MarketLensError(
            ErrorCode.PROVIDER_AUTH_FAILED, "The market-data provider rejected the API key.", details=diagnostic
        )
    if key == "Error Message":
        return MarketLensError(
            ErrorCode.DATA_UNAVAILABLE,
            "The market-data provider has no data for this request (check the symbol).",
            details=diagnostic,
        )
    return MarketLensError(ErrorCode.DATA_UNAVAILABLE, "The market-data provider returned no data.", details=diagnostic)


def _malformed(what: str) -> MarketLensError:
    return MarketLensError(
        ErrorCode.MALFORMED_PROVIDER_RESPONSE,
        f"The market-data provider returned an unexpected {what} format.",
    )


def parse_decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise _malformed(what)
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise _malformed(what) from exc
    if not number.is_finite():
        raise _malformed(what)
    return number


def optional_decimal(value: Any, what: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and value.strip() in _MISSING):
        return None
    return parse_decimal(value, what)


def parse_date(value: Any, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise _malformed(what) from exc


# --------------------------------------------------------------------------- daily series
@dataclass
class ParsedDaily:
    bars: list[PriceBar]
    timezone: str | None
    last_refreshed: dt.date | None
    adjustment: str
    dropped: list[str] = field(default_factory=list)


def parse_daily(data: dict[str, Any], *, adjusted: bool) -> ParsedDaily:
    series = data.get("Time Series (Daily)")
    meta = data.get("Meta Data")
    if not isinstance(series, dict) or not isinstance(meta, dict):
        raise _malformed("daily price series")
    if not series:
        raise MarketLensError(ErrorCode.DATA_UNAVAILABLE, "The provider returned an empty price series.")
    bars: list[PriceBar] = []
    dropped: list[str] = []
    for day_text, row in series.items():
        if not isinstance(row, dict):
            raise _malformed("daily bar")
        day = parse_date(day_text, "bar date")
        o = parse_decimal(row.get("1. open"), "open price")
        h = parse_decimal(row.get("2. high"), "high price")
        low = parse_decimal(row.get("3. low"), "low price")
        c = parse_decimal(row.get("4. close"), "close price")
        volume_raw = row.get("6. volume") if adjusted else row.get("5. volume")
        volume = int(parse_decimal(volume_raw, "volume")) if volume_raw not in (None, "") else None
        if adjusted:
            adj_close = parse_decimal(row.get("5. adjusted close"), "adjusted close")
            if c == 0:
                dropped.append(f"{day.isoformat()}: zero close cannot be adjusted")
                continue
            factor = adj_close / c
            o, h, low, c = (o * factor, h * factor, low * factor, adj_close)
        if min(o, h, low, c) <= 0 or low > min(o, c) or h < max(o, c) or low > h:
            dropped.append(f"{day.isoformat()}: OHLC values are not internally consistent")
            continue
        bars.append(PriceBar(trading_date=day, open=o, high=h, low=low, close=c, volume=volume))
    if not bars:
        raise _malformed("daily price series (no valid bars)")
    bars.sort(key=lambda b: b.trading_date)
    last_refreshed_raw = meta.get("3. Last Refreshed")
    last_refreshed = None
    if isinstance(last_refreshed_raw, str) and last_refreshed_raw:
        try:
            last_refreshed = dt.date.fromisoformat(last_refreshed_raw[:10])
        except ValueError:
            last_refreshed = None
    timezone = meta.get("5. Time Zone") or meta.get("6. Time Zone")
    return ParsedDaily(
        bars=bars,
        timezone=str(timezone) if timezone else None,
        last_refreshed=last_refreshed,
        adjustment="adjusted" if adjusted else "unadjusted",
        dropped=dropped,
    )


# --------------------------------------------------------------------------- quote
@dataclass
class ParsedQuote:
    price: Decimal
    trading_date: dt.date | None
    previous_close: Decimal | None
    volume: int | None


def parse_quote(data: dict[str, Any]) -> ParsedQuote:
    quote = data.get("Global Quote")
    if not isinstance(quote, dict):
        raise _malformed("quote")
    if not quote:
        raise MarketLensError(ErrorCode.DATA_UNAVAILABLE, "The provider returned no quote for this symbol.")
    day_raw = quote.get("07. latest trading day")
    trading_date = parse_date(day_raw, "quote date") if day_raw else None
    volume = optional_decimal(quote.get("06. volume"), "quote volume")
    return ParsedQuote(
        price=parse_decimal(quote.get("05. price"), "quote price"),
        trading_date=trading_date,
        previous_close=optional_decimal(quote.get("08. previous close"), "previous close"),
        volume=int(volume) if volume is not None else None,
    )


# --------------------------------------------------------------------------- news
@dataclass
class ParsedNewsItem:
    title: str
    url: str | None
    published_local: str | None  # ISO without offset (provider gives no zone)
    published_date: dt.date | None
    summary: str | None
    publisher: str | None
    topics: list[str]
    relevance: Decimal | None
    sentiment_label: str | None


_NEWS_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?$")


def parse_news_time(raw: Any) -> tuple[str | None, dt.date | None]:
    if not isinstance(raw, str):
        return None, None
    match = _NEWS_TIME.match(raw.strip())
    if not match:
        return None, None
    year, month, day, hour, minute, second = match.groups()
    try:
        moment = dt.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
    except ValueError:
        return None, None
    return moment.isoformat(), moment.date()


def parse_news(data: dict[str, Any], symbol: str) -> list[ParsedNewsItem]:
    feed = data.get("feed")
    if not isinstance(feed, list):
        raise _malformed("news feed")
    items: list[ParsedNewsItem] = []
    for entry in feed:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
            raise _malformed("news item")
        url = entry.get("url")
        if not (isinstance(url, str) and url.startswith(("https://", "http://"))):
            url = None
        published_local, published_date = parse_news_time(entry.get("time_published"))
        relevance = None
        label = None
        for ts in entry.get("ticker_sentiment") or []:
            if isinstance(ts, dict) and ts.get("ticker") == symbol:
                relevance = optional_decimal(ts.get("relevance_score"), "relevance score")
                label = ts.get("ticker_sentiment_label") if isinstance(ts.get("ticker_sentiment_label"), str) else None
        topics = [
            t["topic"] for t in entry.get("topics") or [] if isinstance(t, dict) and isinstance(t.get("topic"), str)
        ]
        items.append(
            ParsedNewsItem(
                title=entry["title"].strip(),
                url=url,
                published_local=published_local,
                published_date=published_date,
                summary=entry.get("summary") if isinstance(entry.get("summary"), str) else None,
                publisher=entry.get("source") if isinstance(entry.get("source"), str) else None,
                topics=topics,
                relevance=relevance,
                sentiment_label=label,
            )
        )
    return items


# --------------------------------------------------------------------------- fundamentals
@dataclass
class ParsedMetricValue:
    metric_name: str
    label: str
    value: Decimal
    period_end: dt.date
    currency: str | None
    field: str
    reported_date: dt.date | None = None


def parse_income_statement(
    data: dict[str, Any], metrics: list[str], period_type: str, limit: int
) -> tuple[list[ParsedMetricValue], list[str]]:
    key = "annualReports" if period_type == "annual" else "quarterlyReports"
    reports = data.get(key)
    if not isinstance(reports, list):
        raise _malformed("income statement")
    wanted = [m for m in metrics if m in INCOME_METRICS]
    values: list[ParsedMetricValue] = []
    found: set[str] = set()
    ordered = sorted(
        (r for r in reports if isinstance(r, dict) and r.get("fiscalDateEnding")),
        key=lambda r: str(r["fiscalDateEnding"]),
        reverse=True,
    )[:limit]
    for report in ordered:
        period_end = parse_date(report["fiscalDateEnding"], "fiscal period end")
        currency = report.get("reportedCurrency") if isinstance(report.get("reportedCurrency"), str) else None
        for metric in wanted:
            field_name, label = INCOME_METRICS[metric]
            value = optional_decimal(report.get(field_name), field_name)
            if value is None:
                continue
            found.add(metric)
            values.append(ParsedMetricValue(metric, label, value, period_end, currency, field_name))
    missing = [m for m in wanted if m not in found]
    return values, missing


def parse_earnings(data: dict[str, Any], period_type: str, limit: int) -> list[ParsedMetricValue]:
    key = "annualEarnings" if period_type == "annual" else "quarterlyEarnings"
    rows = data.get(key)
    if not isinstance(rows, list):
        raise _malformed("earnings")
    ordered = sorted(
        (r for r in rows if isinstance(r, dict) and r.get("fiscalDateEnding")),
        key=lambda r: str(r["fiscalDateEnding"]),
        reverse=True,
    )[:limit]
    values: list[ParsedMetricValue] = []
    for row in ordered:
        eps = optional_decimal(row.get("reportedEPS"), "reported EPS")
        if eps is None:
            continue
        reported_raw = row.get("reportedDate")
        reported = None
        if isinstance(reported_raw, str) and reported_raw not in _MISSING:
            reported = parse_date(reported_raw, "reported date")
        values.append(
            ParsedMetricValue(
                "reported_eps",
                EPS_METRICS["reported_eps"],
                eps,
                parse_date(row["fiscalDateEnding"], "fiscal period end"),
                "USD",
                "reportedEPS",
                reported,
            )
        )
    return values


# --------------------------------------------------------------------------- client
class AlphaVantageClient:
    """Endpoint calls. Transport supplies payloads; this class validates them."""

    def __init__(self, transport: AlphaVantageTransport, *, fixture_url_base: str | None = None) -> None:
        self.transport = transport
        self.fixture_url_base = fixture_url_base

    @property
    def source_mode(self) -> SourceMode:
        return self.transport.source_mode

    def source_url(self, function: str, symbol: str) -> str:
        """Public reference URL; never includes the API key."""
        if self.transport.source_mode == SourceMode.FIXTURE and self.fixture_url_base:
            return f"{self.fixture_url_base}/alpha_vantage/{function}/{symbol}"
        return DOC_URLS.get(function, "https://www.alphavantage.co/documentation/")

    async def call(self, function: str, **params: str) -> ProviderPayload:
        payload = await self.transport.query({"function": function, **params})
        error = classify_message(payload.data)
        if error:
            raise error
        return payload

    async def daily_series(self, symbol: str, *, adjusted: bool) -> tuple[ProviderPayload, ParsedDaily, list[str]]:
        """Daily bars; falls back to the unadjusted series when adjusted data is not in the plan."""
        warnings: list[str] = []
        if adjusted:
            try:
                payload = await self.call("TIME_SERIES_DAILY_ADJUSTED", symbol=symbol, outputsize="compact")
                return payload, parse_daily(payload.data, adjusted=True), warnings
            except MarketLensError as exc:
                if exc.code != ErrorCode.CAPABILITY_UNAVAILABLE:
                    raise
                warnings.append(
                    "Adjusted prices are not available on the configured plan; returned the unadjusted "
                    "(as-traded) series instead."
                )
        payload = await self.call("TIME_SERIES_DAILY", symbol=symbol, outputsize="compact")
        return payload, parse_daily(payload.data, adjusted=False), warnings

    async def global_quote(self, symbol: str) -> tuple[ProviderPayload, ParsedQuote]:
        payload = await self.call("GLOBAL_QUOTE", symbol=symbol)
        return payload, parse_quote(payload.data)

    async def news(
        self, symbol: str, *, time_from: dt.date, time_to: dt.date, limit: int
    ) -> tuple[ProviderPayload, list[ParsedNewsItem]]:
        payload = await self.call(
            "NEWS_SENTIMENT",
            tickers=symbol,
            time_from=time_from.strftime("%Y%m%dT0000"),
            time_to=time_to.strftime("%Y%m%dT2359"),
            sort="LATEST",
            limit=str(max(limit, 50)),
        )
        return payload, parse_news(payload.data, symbol)

    async def income_statement(self, symbol: str) -> ProviderPayload:
        return await self.call("INCOME_STATEMENT", symbol=symbol)

    async def earnings(self, symbol: str) -> ProviderPayload:
        return await self.call("EARNINGS", symbol=symbol)
