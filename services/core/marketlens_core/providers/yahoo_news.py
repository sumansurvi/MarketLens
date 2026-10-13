"""Yahoo Finance headline RSS: the news fallback when Alpha Vantage news isn't in the plan (D-86).

The feed (``…/rss/2.0/headline?s=AAPL``) is public and needs no key. It lists the latest ~20
headlines for a ticker with an RFC 822 publication time (zone included), a link and a short
description. It has no relevance or sentiment scores. Feed text is untrusted: it is parsed with
defusedxml (no DTDs or entity expansion), and summaries are reduced to plain text here and
cleaned and flagged by the research-sources server like any other news.
"""

from __future__ import annotations

import datetime as dt
import email.utils
from typing import Protocol
from urllib.parse import urlparse

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as parse_xml

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.providers.alpha_vantage import ParsedNewsItem
from marketlens_core.providers.base import ProviderPayload
from marketlens_core.untrusted import html_to_text

PROVIDER_ID = "yahoo_finance"
PROVIDER_LABEL = "Yahoo Finance"
DOCS_URL = "https://finance.yahoo.com/"


class NewsTransport(Protocol):
    async def headlines(self, symbol: str) -> ProviderPayload: ...


def _malformed() -> MarketLensError:
    return MarketLensError(
        ErrorCode.MALFORMED_PROVIDER_RESPONSE, "Yahoo Finance returned a news feed that could not be read."
    )


def _publisher(url: str | None) -> str | None:
    host = urlparse(url).hostname if url else None
    if not host:
        return None
    return host.removeprefix("www.").removeprefix("finance.")


def parse_headlines(text: str) -> list[ParsedNewsItem]:
    """Items with a title and a parseable time. Times become UTC ISO strings ending in ``Z``."""
    if not isinstance(text, str):
        raise _malformed()
    try:
        root = parse_xml(text, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as exc:
        raise _malformed() from exc
    items: list[ParsedNewsItem] = []
    for node in root.iter("item"):
        title = html_to_text(node.findtext("title") or "").strip()
        if not title:
            continue
        link = (node.findtext("link") or "").strip() or None
        if link and urlparse(link).scheme not in ("http", "https"):
            link = None
        published: dt.datetime | None = None
        raw_date = node.findtext("pubDate")
        if raw_date:
            try:
                published = email.utils.parsedate_to_datetime(raw_date.strip())
            except (TypeError, ValueError):
                published = None
        if published is not None and published.tzinfo is None:
            published = None  # a time without a zone can't be placed on the calendar reliably
        utc = published.astimezone(dt.UTC) if published else None
        summary = html_to_text(node.findtext("description") or "").strip() or None
        items.append(
            ParsedNewsItem(
                title=title,
                url=link,
                published_local=utc.strftime("%Y-%m-%dT%H:%M:%SZ") if utc else None,
                published_date=utc.date() if utc else None,
                summary=summary,
                publisher=_publisher(link),
                topics=[],
                relevance=None,
                sentiment_label=None,
            )
        )
    return items


class YahooNewsClient:
    def __init__(self, transport: NewsTransport) -> None:
        self.transport = transport

    async def news(self, symbol: str) -> tuple[ProviderPayload, list[ParsedNewsItem]]:
        payload = await self.transport.headlines(symbol)
        return payload, parse_headlines(str((payload.data or {}).get("rss", "")))
