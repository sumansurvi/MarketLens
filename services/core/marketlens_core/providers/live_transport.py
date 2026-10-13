"""Live HTTP transports for Alpha Vantage and SEC EDGAR.

- Finite timeouts, bounded retries with exponential backoff + jitter for transient reads
  (timeouts, connection errors, HTTP 429/5xx). Nothing here ever writes upstream.
- Rate limiting and TTL caching in SQLite. Alpha Vantage requests are paced across processes
  through the shared database (both read-only MCP servers call it; free keys allow about one
  request per second), identical concurrent requests share one upstream call, and a burst
  rejection is retried once. SEC stays ≤ the configured requests/second plus a concurrency cap,
  below SEC's documented 10 requests/second. Cache keys never contain the API key; filing
  documents are not cached.
- Errors are translated to stable codes. The API key never appears in labels, errors or
  logs; only the request label (function + symbol) is recorded.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import random
import time
from typing import Any

import httpx2

from marketlens_core.config import Settings
from marketlens_core.db.connection import Database
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.models import SourceMode
from marketlens_core.providers.alpha_vantage import classify_message
from marketlens_core.providers.base import ProviderPayload
from marketlens_core.timeutil import iso_z, parse_iso_datetime, utc_now

logger = logging.getLogger("marketlens.providers")

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 4.0
# Requests are spaced at twice the configured minimum interval. In a real run (2026-10-11) a free
# key still rejected a request sent about 1.1 s after the previous one: arrival times vary with
# connection setup, and a rejected request may still count against the daily quota. A burst
# rejection is retried once after BURST_RETRY_SECONDS.
PACING_MARGIN = 2.0
BURST_RETRY_SECONDS = 1.5


class RateLimiter:
    """Spaces requests at least ``interval`` seconds apart (process-local)."""

    def __init__(self, requests: float, per_seconds: float) -> None:
        self.interval = per_seconds / max(requests, 0.001)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next = max(now, self._next) + self.interval


class SharedRateLimiter:
    """Spaces requests ``interval`` seconds apart across every process using the same database.

    Each caller reserves the next free slot in ``provider_rate_limits`` inside a ``BEGIN
    IMMEDIATE`` transaction, then sleeps until its slot. Without a database it falls back to
    process-local spacing.
    """

    def __init__(self, db: Database | None, provider: str, interval: float) -> None:
        self.db = db
        self.provider = provider
        self.interval = interval
        self._local = RateLimiter(1, interval)

    def reserve(self) -> float:
        """Claim the next slot; returns how long to wait for it (seconds)."""
        assert self.db is not None
        now = time.time()
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT next_allowed_at FROM provider_rate_limits WHERE provider = ?", (self.provider,)
            ).fetchone()
            start = max(now, float(row["next_allowed_at"]) if row else 0.0)
            conn.execute(
                """INSERT INTO provider_rate_limits (provider, next_allowed_at) VALUES (?, ?)
                ON CONFLICT(provider) DO UPDATE SET next_allowed_at = excluded.next_allowed_at""",
                (self.provider, start + self.interval),
            )
        return start - now

    async def acquire(self) -> None:
        if self.db is None:
            await self._local.acquire()
            return
        wait = self.reserve()
        if wait > 0:
            await asyncio.sleep(wait)


class ProviderCache:
    """TTL cache in the ``provider_cache`` table; disabled when no database is given."""

    def __init__(self, db: Database | None) -> None:
        self.db = db

    def get(self, key: str) -> tuple[Any, dt.datetime] | None:
        if self.db is None:
            return None
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT payload_json, created_at, expires_at FROM provider_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if row is None or parse_iso_datetime(row["expires_at"]) <= utc_now():
            return None
        return json.loads(row["payload_json"]), parse_iso_datetime(row["created_at"])

    def put(self, key: str, provider: str, data: Any, retrieved_at: dt.datetime, ttl_seconds: int) -> None:
        if self.db is None or ttl_seconds <= 0:
            return
        expires = retrieved_at + dt.timedelta(seconds=ttl_seconds)
        with self.db.write() as conn:
            conn.execute(
                """INSERT INTO provider_cache (cache_key, provider, expires_at, payload_json, created_at)
                VALUES (?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET expires_at = excluded.expires_at,
                payload_json = excluded.payload_json, created_at = excluded.created_at""",
                (key, provider, iso_z(expires), json.dumps(data), iso_z(retrieved_at)),
            )


def _backoff(attempt: int) -> float:
    return min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2**attempt) + random.uniform(0, 0.25)  # noqa: S311


def _retry_after(response: httpx2.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


async def _get_with_retries(
    client: httpx2.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None,
    label: str,
    provider: str,
    max_retries: int,
    limiter: RateLimiter | SharedRateLimiter,
    semaphore: asyncio.Semaphore | None = None,
) -> httpx2.Response:
    last: MarketLensError | None = None
    for attempt in range(max_retries + 1):
        await limiter.acquire()
        try:
            if semaphore is not None:
                async with semaphore:
                    response = await client.get(url, params=params)
            else:
                response = await client.get(url, params=params)
        except httpx2.TimeoutException:
            last = MarketLensError(
                ErrorCode.PROVIDER_TIMEOUT, f"{provider} did not respond in time.", details={"request": label}
            )
        except httpx2.TransportError:
            last = MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                f"{provider} could not be reached.",
                retryable=True,
                details={"request": label},
            )
        else:
            status = response.status_code
            if status == 429:
                last = MarketLensError(
                    ErrorCode.RATE_LIMITED,
                    f"{provider} temporarily limited requests.",
                    retry_after_seconds=_retry_after(response) or 60,
                    details={"request": label, "provider_status": status},
                )
            elif status >= 500:
                last = MarketLensError(
                    ErrorCode.DATA_UNAVAILABLE,
                    f"{provider} returned a server error.",
                    retryable=True,
                    details={"request": label, "provider_status": status},
                )
            elif status in (401, 403):
                hint = " Check SEC_USER_AGENT." if provider == "SEC EDGAR" else ""
                raise MarketLensError(
                    ErrorCode.PROVIDER_AUTH_FAILED,
                    f"{provider} rejected the request.{hint}",
                    details={"request": label, "provider_status": status},
                )
            elif status == 404:
                raise MarketLensError(
                    ErrorCode.DATA_UNAVAILABLE,
                    f"{provider} has no data for this request.",
                    details={"request": label, "provider_status": status},
                )
            elif status >= 400:
                raise MarketLensError(
                    ErrorCode.DATA_UNAVAILABLE,
                    f"{provider} rejected the request.",
                    details={"request": label, "provider_status": status},
                )
            else:
                return response
        if attempt < max_retries and (last.code != ErrorCode.RATE_LIMITED or attempt == 0):
            delay = _backoff(attempt)
            if last.code == ErrorCode.RATE_LIMITED and last.retry_after_seconds:
                delay = min(delay, 2.0)  # never block a run for the full window; one short retry only
            logger.info("retrying %s after %s (attempt %d)", label, last.code.value, attempt + 1)
            await asyncio.sleep(delay)
            continue
        break
    assert last is not None
    raise last


def _parse_json(response: httpx2.Response, provider: str, label: str) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise MarketLensError(
            ErrorCode.MALFORMED_PROVIDER_RESPONSE,
            f"{provider} returned data that is not valid JSON.",
            details={"request": label},
        ) from exc


class LiveAlphaVantageTransport:
    source_mode = SourceMode.LIVE

    def __init__(
        self, settings: Settings, *, cache: ProviderCache | None = None, client: httpx2.AsyncClient | None = None
    ) -> None:
        if not settings.alpha_vantage_api_key:
            raise MarketLensError(ErrorCode.CAPABILITY_UNAVAILABLE, "Alpha Vantage is not configured.")
        self._key = settings.alpha_vantage_api_key.get_secret_value()
        self.settings = settings
        self.cache = cache or ProviderCache(None)
        self.client = client or httpx2.AsyncClient(
            timeout=settings.market_data_timeout_seconds, headers={"User-Agent": "MarketLens/0.1"}
        )
        interval = 60 / max(settings.alpha_vantage_max_requests_per_minute, 1) * PACING_MARGIN
        self.limiter = SharedRateLimiter(self.cache.db, "alpha_vantage", interval)
        self._inflight: dict[str, tuple[asyncio.Lock, list[int]]] = {}

    def _ttl(self, function: str) -> int:
        if function == "GLOBAL_QUOTE":
            return self.settings.market_data_quote_cache_ttl_seconds
        return self.settings.market_data_cache_ttl_seconds

    def _cached(self, cache_key: str, label: str) -> ProviderPayload | None:
        cached = self.cache.get(cache_key)
        if cached is None:
            return None
        data, retrieved_at = cached
        return ProviderPayload(
            data=data, source_mode=SourceMode.LIVE, retrieved_at=retrieved_at, request_label=label, from_cache=True
        )

    async def query(self, params: dict[str, str]) -> ProviderPayload:
        function = params.get("function", "")
        symbol = params.get("symbol") or params.get("tickers") or ""
        label = f"alpha_vantage:{function}:{symbol}"
        cache_key = "av:" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self._cached(cache_key, label)
        if cached is not None:
            return cached
        # Identical concurrent requests (e.g. the price history and the price-change tool both
        # need the same daily series) wait for the first one and then read it from the cache.
        lock, waiting = self._inflight.setdefault(cache_key, (asyncio.Lock(), [0]))
        waiting[0] += 1
        try:
            async with lock:
                cached = self._cached(cache_key, label)
                if cached is not None:
                    return cached
                return await self._fetch(params, label, cache_key, function)
        finally:
            waiting[0] -= 1
            if waiting[0] == 0:
                self._inflight.pop(cache_key, None)

    async def _fetch(self, params: dict[str, str], label: str, cache_key: str, function: str) -> ProviderPayload:
        for attempt in range(2):
            response = await _get_with_retries(
                self.client,
                self.settings.alpha_vantage_base_url,
                params={**params, "apikey": self._key},
                label=label,
                provider="Alpha Vantage",
                max_retries=self.settings.market_data_max_retries,
                limiter=self.limiter,
            )
            data = _parse_json(response, "Alpha Vantage", label)
            retrieved_at = utc_now()
            problem = classify_message(data)
            if problem is None:
                self.cache.put(cache_key, "alpha_vantage", data, retrieved_at, self._ttl(function))
            elif problem.code == ErrorCode.RATE_LIMITED:
                limit = problem.details.get("limit")
                logger.warning(
                    "%s limited by the provider (%s limit): %s", label, limit, problem.details.get("provider_message")
                )
                if limit == "burst" and attempt == 0:
                    await asyncio.sleep(BURST_RETRY_SECONDS)  # the shared limiter spaces the retry as well
                    continue
            else:
                # Plan, key or input problems: keep the provider's own words (key-like tokens redacted).
                logger.warning(
                    "%s refused by the provider (%s): %s",
                    label,
                    problem.code.value,
                    problem.details.get("provider_message"),
                )
            # Error and limit messages are never cached; the adapter classifies them for the caller.
            return ProviderPayload(
                data=data, source_mode=SourceMode.LIVE, retrieved_at=retrieved_at, request_label=label
            )
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self.client.aclose()


class LiveSecTransport:
    source_mode = SourceMode.LIVE

    def __init__(
        self, settings: Settings, *, cache: ProviderCache | None = None, client: httpx2.AsyncClient | None = None
    ) -> None:
        if not settings.sec_user_agent_configured:
            raise MarketLensError(ErrorCode.CAPABILITY_UNAVAILABLE, "SEC_USER_AGENT is not configured.")
        self.settings = settings
        self.cache = cache or ProviderCache(None)
        self.client = client or httpx2.AsyncClient(
            timeout=settings.sec_timeout_seconds,
            headers={"User-Agent": settings.sec_user_agent or "", "Accept-Encoding": "gzip, deflate"},
            follow_redirects=False,
        )
        self.limiter = RateLimiter(settings.sec_max_requests_per_second, 1)
        self.semaphore = asyncio.Semaphore(settings.sec_max_concurrent_requests)
        self._www = settings.sec_www_base_url.rstrip("/")
        self._data = settings.sec_data_base_url.rstrip("/")

    @property
    def archives_base(self) -> str:
        return f"{self._www}/Archives/edgar/data"

    @property
    def data_api_base(self) -> str:
        return self._data

    async def _json(self, url: str, label: str) -> ProviderPayload:
        cache_key = f"sec:{url}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            data, retrieved_at = cached
            return ProviderPayload(
                data=data, source_mode=SourceMode.LIVE, retrieved_at=retrieved_at, request_label=label, from_cache=True
            )
        response = await _get_with_retries(
            self.client,
            url,
            params=None,
            label=label,
            provider="SEC EDGAR",
            max_retries=self.settings.market_data_max_retries,
            limiter=self.limiter,
            semaphore=self.semaphore,
        )
        data = _parse_json(response, "SEC EDGAR", label)
        retrieved_at = utc_now()
        self.cache.put(cache_key, "sec_edgar", data, retrieved_at, self.settings.sec_cache_ttl_seconds)
        return ProviderPayload(data=data, source_mode=SourceMode.LIVE, retrieved_at=retrieved_at, request_label=label)

    async def tickers_exchange(self) -> ProviderPayload:
        return await self._json(
            f"{self._www}/files/company_tickers_exchange.json", "sec_edgar:company_tickers_exchange"
        )

    async def submissions(self, cik10: str) -> ProviderPayload:
        return await self._json(f"{self._data}/submissions/CIK{cik10}.json", f"sec_edgar:submissions:{cik10}")

    async def companyfacts(self, cik10: str) -> ProviderPayload:
        return await self._json(
            f"{self._data}/api/xbrl/companyfacts/CIK{cik10}.json", f"sec_edgar:companyfacts:{cik10}"
        )

    async def document(self, cik_int: int, accession_nodash: str, filename: str) -> ProviderPayload:
        label = f"sec_edgar:document:{cik_int}/{accession_nodash}/{filename}"
        url = f"{self.archives_base}/{int(cik_int)}/{accession_nodash}/{filename}"
        response = await _get_with_retries(
            self.client,
            url,
            params=None,
            label=label,
            provider="SEC EDGAR",
            max_retries=self.settings.market_data_max_retries,
            limiter=self.limiter,
            semaphore=self.semaphore,
        )
        if len(response.content) > MAX_DOCUMENT_BYTES:
            raise MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                "The filing document is too large to excerpt safely.",
                details={"request": label},
            )
        return ProviderPayload(
            data=response.text, source_mode=SourceMode.LIVE, retrieved_at=utc_now(), request_label=label
        )

    async def aclose(self) -> None:
        await self.client.aclose()


class LiveYahooNewsTransport:
    """Yahoo Finance's public headline RSS (no key). Cached like other news; one request at a time."""

    source_mode = SourceMode.LIVE

    def __init__(
        self, settings: Settings, *, cache: ProviderCache | None = None, client: httpx2.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.cache = cache or ProviderCache(None)
        self.client = client or httpx2.AsyncClient(
            timeout=settings.market_data_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MarketLens/0.1)", "Accept": "application/rss+xml"},
            follow_redirects=False,
        )
        self.limiter = RateLimiter(1, 1)
        self._url = settings.yahoo_news_base_url

    async def headlines(self, symbol: str) -> ProviderPayload:
        label = f"yahoo_finance:headlines:{symbol}"
        cache_key = f"yahoo:{self._url}?s={symbol}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            data, retrieved_at = cached
            return ProviderPayload(
                data=data, source_mode=SourceMode.LIVE, retrieved_at=retrieved_at, request_label=label, from_cache=True
            )
        response = await _get_with_retries(
            self.client,
            self._url,
            params={"s": symbol, "region": "US", "lang": "en-US"},
            label=label,
            provider="Yahoo Finance",
            max_retries=self.settings.market_data_max_retries,
            limiter=self.limiter,
        )
        data = {"rss": response.text[:500_000]}
        retrieved_at = utc_now()
        self.cache.put(cache_key, "yahoo_finance", data, retrieved_at, self.settings.market_data_cache_ttl_seconds)
        return ProviderPayload(data=data, source_mode=SourceMode.LIVE, retrieved_at=retrieved_at, request_label=label)
