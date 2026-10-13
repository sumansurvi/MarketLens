"""Yahoo Finance headline RSS parsing (D-86): untrusted feed text."""

from __future__ import annotations

import pytest
from marketlens_core.errors import MarketLensError
from marketlens_core.providers.yahoo_news import parse_headlines


def test_doctype_and_broken_feeds_are_refused() -> None:
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel><item><title>&lol;</title></item></channel></rss>'
    for text in (bomb, "<rss><channel>", ""):
        with pytest.raises(MarketLensError) as err:
            parse_headlines(text)
        assert err.value.code.value == "MALFORMED_PROVIDER_RESPONSE"


def test_items_without_a_zoned_time_have_no_date() -> None:
    items = parse_headlines(
        "<rss><channel><item><title>No zone</title><pubDate>Wed, 30 Sep 2026 14:05:00</pubDate></item>"
        "<item><title> </title></item></channel></rss>"
    )
    assert [(i.title, i.published_date) for i in items] == [("No zone", None)]
