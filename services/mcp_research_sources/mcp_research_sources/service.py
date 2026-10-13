"""Research Sources server business logic (MCP_TOOLS §5): symbols, filings, facts, news, excerpts.

Read-only. Retrieved text (news summaries, filing excerpts) is untrusted: it is cleaned,
truncated and flagged when it contains instruction-like content, and is only ever
returned as data.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable

from marketlens_core.config import Settings
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.fixtures import FixtureDataset, ReferenceClock, reference_clock
from marketlens_core.models import EvidenceKind, FilingRecord, FinancialMetric, FreshnessStatus, NewsArticle, SourceMode
from marketlens_core.providers import alpha_vantage as av
from marketlens_core.providers import sec_edgar as sec
from marketlens_core.providers import yahoo_news
from marketlens_core.providers.base import (
    ProviderPayload,
    build_evidence,
    reporting_period_freshness,
    source_name_for,
)
from marketlens_core.providers.fixture_transport import FixtureAlphaVantageTransport, FixtureSecTransport
from marketlens_core.status import CapabilityTracker
from marketlens_core.symbols import normalize_symbol
from marketlens_core.tool_models import (
    CapabilityStatus,
    CompanyFactsData,
    CompanyFactsInput,
    FilingExcerptData,
    FilingExcerptInput,
    FilingsData,
    FilingsInput,
    NewsData,
    NewsInput,
    ResolveSymbolData,
    ServerStatusData,
    SourceStatusEntry,
    SymbolInput,
    SymbolMatch,
    SymbolSearchData,
    SymbolSearchInput,
)
from marketlens_core.untrusted import clean_snippet, flags_for, html_to_text

SERVER_ID = "research_sources"
NEWS_TZ_NOTE = "The provider does not specify a time zone for publication timestamps."
MAX_NEWS_RANGE_DAYS = 366


_NAME_SUFFIXES = {"inc", "corp", "corporation", "co", "company", "ltd", "plc", "holdings", "group", "the"}


def _mention_pattern(symbol: str, company_name: str | None) -> re.Pattern[str]:
    """Whole-word match for the ticker or the company's first distinctive name word (Microsoft, Apple)."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'-]+", company_name or "") if w.lower() not in _NAME_SUFFIXES]
    terms = [re.escape(symbol.split("-")[0])] + ([re.escape(words[0])] if words and len(words[0]) >= 3 else [])
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.IGNORECASE)


class ResearchSourcesService:
    def __init__(
        self,
        settings: Settings,
        sec_client: sec.SecEdgarClient,
        news_client: av.AlphaVantageClient,
        clock: Callable[[], ReferenceClock],
        *,
        fixture_dataset: FixtureDataset | None = None,
        fallback_news_client: yahoo_news.YahooNewsClient | None = None,
    ) -> None:
        self.settings = settings
        self.sec = sec_client
        self.news_client = news_client
        self.fallback_news_client = fallback_news_client
        # Set after Alpha Vantage refuses news as premium, so later calls skip it (one request saved each).
        self._premium_news_refused = False
        self.clock = clock
        self.fixture_dataset = fixture_dataset
        self.tracker = CapabilityTracker()

    async def _tracked(self, capability: str, coro):  # type: ignore[no-untyped-def]
        try:
            result = await coro
        except MarketLensError as exc:
            self.tracker.failure(capability, exc)
            raise
        self.tracker.success(capability)
        return result

    @property
    def _is_fixture(self) -> bool:
        return self.fixture_dataset is not None

    # ------------------------------------------------------------------ B6 resolve_symbol
    async def resolve_symbol(self, args: SymbolInput) -> ResolveSymbolData:
        symbol = normalize_symbol(args.symbol)
        mapping = await self._tracked("symbol_mapping", self.sec.ticker_map())
        record = sec.lookup_ticker(mapping, symbol)
        if record is None:
            if self._is_fixture:
                covered = ", ".join(sorted(mapping))
                message = f"{symbol} is not in the demo fixture universe ({covered}). Verify the ticker."
            else:
                message = f"{symbol} was not found in the SEC ticker mapping. Verify the ticker; no similar symbol is substituted."
            raise MarketLensError(ErrorCode.UNKNOWN_SYMBOL, message, details={"symbol": symbol})
        if not self._is_fixture and record.exchange not in sec.ALLOWED_EXCHANGES:
            raise MarketLensError(
                ErrorCode.UNKNOWN_SYMBOL,
                f"{symbol} is listed on {record.exchange or 'an unknown venue'}; MarketLens covers NYSE and Nasdaq only.",
                details={"symbol": symbol, "exchange": record.exchange},
            )
        return ResolveSymbolData(
            symbol=record.symbol,  # the SEC's spelling (BRK-B for BRK.B), used by every later tool call
            company_name=record.company_name,
            cik=record.cik10,
            exchange=record.exchange,
            coverage=SourceMode.FIXTURE if self._is_fixture else SourceMode.LIVE,
            is_fixture=self._is_fixture,
        )

    # ------------------------------------------------------------------ search_symbols
    async def search_symbols(self, args: SymbolSearchInput) -> SymbolSearchData:
        """Suggestions for the research bar's ticker dropdown: tickers starting with the query,
        then company names with a word starting with it; an empty query lists the largest covered
        companies. Only covered symbols are offered (the fixture universe, or NYSE/Nasdaq
        listings). It never picks for the user: research still resolves the exact ticker chosen (D-15)."""
        upper, lower = args.query.upper(), args.query.lower()
        mapping = await self._tracked("symbol_mapping", self.sec.ticker_map())
        ranked: list[tuple[int, int, sec.TickerRecord]] = []
        # Ties keep the SEC list's order, which puts the largest companies first.
        for order, record in enumerate(mapping.values()):
            if not self._is_fixture and record.exchange not in sec.ALLOWED_EXCHANGES:
                continue
            symbol, name = record.symbol, record.company_name.lower()
            if not upper:
                rank = 0
            elif symbol in (upper, upper.replace(".", "-")):
                rank = 0
            elif symbol.startswith(upper):
                rank = 1
            elif name.startswith(lower):
                rank = 2
            elif f" {lower}" in f" {name}":
                rank = 3
            else:
                continue
            ranked.append((rank, order, record))
        ranked.sort(key=lambda item: item[:2])
        return SymbolSearchData(
            query=args.query,
            matches=[
                SymbolMatch(symbol=r.symbol, company_name=r.company_name, exchange=r.exchange)
                for _, _, r in ranked[: args.limit]
            ],
            coverage=SourceMode.FIXTURE if self._is_fixture else SourceMode.LIVE,
            is_fixture=self._is_fixture,
        )

    # ------------------------------------------------------------------ B1 search_company_filings
    async def search_company_filings(self, args: FilingsInput) -> FilingsData:
        identity = await self.resolve_symbol(SymbolInput(symbol=args.symbol))
        payload = await self._tracked("filings", self.sec.submissions(identity.cik))
        company_name, rows = sec.parse_submissions(payload.data)
        wanted = set(args.forms)
        selected = [
            r for r in rows if r.form in wanted and (args.filed_after is None or r.filed_date >= args.filed_after)
        ]
        selected.sort(key=lambda r: (r.filed_date, r.accession_number), reverse=True)
        selected = selected[: args.limit]
        filings: list[FilingRecord] = []
        evidence = []
        source_name = source_name_for(sec.PROVIDER_LABEL, payload)
        for row in selected:
            url = (
                self.sec.filing_url(identity.cik, row.accession_number, row.primary_document)
                if row.primary_document
                else None
            )
            accepted = (
                row.accepted_at.astimezone(dt.UTC).isoformat().replace("+00:00", "Z") if row.accepted_at else None
            )
            item = build_evidence(
                kind=EvidenceKind.FILING,
                payload=payload,
                provider=sec.PROVIDER_ID,
                source_name=source_name,
                identity={"cik": identity.cik, "accession": row.accession_number, "record": "filing_metadata"},
                content={
                    "form": row.form,
                    "filed": row.filed_date,
                    "report": row.report_date,
                    "doc": row.primary_document,
                },
                symbol=identity.symbol,
                title=f"Form {row.form} filed {row.filed_date.isoformat()}"
                + (f" (period ended {row.report_date.isoformat()})" if row.report_date and row.form != "8-K" else ""),
                source_url=url,
                published_at=accepted,
                published_at_tz_known=True if accepted else None,
                published_date=row.filed_date,
                period_end=row.report_date,
                evidence_payload={
                    "record_type": "filing_metadata",
                    "form": row.form,
                    "filed_date": row.filed_date.isoformat(),
                    "report_date": row.report_date.isoformat() if row.report_date else None,
                    "accession_number": row.accession_number,
                    "primary_document": row.primary_document,
                    "description": row.description,
                    "cik": identity.cik,
                },
                notes=[] if accepted else ["Acceptance time not supplied; filing date only."],
            )
            evidence.append(item)
            filings.append(
                FilingRecord(
                    form=row.form,
                    filed_date=row.filed_date,
                    accepted_at=row.accepted_at,
                    period_end=row.report_date,
                    accession_number=row.accession_number,
                    cik=identity.cik,
                    company_name=company_name or identity.company_name,
                    primary_document=row.primary_document,
                    description=row.description,
                    source_url=url,
                    evidence_id=item.evidence_id,
                )
            )
        return FilingsData(
            symbol=identity.symbol,
            company_name=company_name or identity.company_name,
            cik=identity.cik,
            forms=list(args.forms),
            filed_after=args.filed_after,
            filings=filings,
            is_fixture=payload.is_fixture,
            evidence=evidence,
        )

    # ------------------------------------------------------------------ B2 get_sec_company_facts
    async def get_sec_company_facts(self, args: CompanyFactsInput) -> CompanyFactsData:
        identity = await self.resolve_symbol(SymbolInput(symbol=args.symbol))
        payload = await self._tracked("company_facts", self.sec.companyfacts(identity.cik))
        entity_name, parsed, missing, notes = sec.parse_company_facts(
            payload.data, list(args.concepts), args.period_type, args.limit
        )
        clock = self.clock()
        missing, notes = list(missing), list(notes)
        # Latest or nothing (D-85): only figures for the company's newest reporting period are
        # offered. A concept the company no longer reports for that period (it switched labels,
        # e.g. Revenues → RevenueFromContractWithCustomer…) is superseded, and if even the newest
        # period is overdue for a newer report, no figures are offered at all.
        latest_by_concept: dict[str, dt.date] = {}
        for f in parsed:
            latest_by_concept[f.concept] = max(latest_by_concept.get(f.concept, f.end), f.end)
        freshness = None
        if latest_by_concept:
            newest = max(latest_by_concept.values())
            for concept, end in latest_by_concept.items():
                if end < newest:
                    missing.append(concept)
                    notes.append(
                        f"{concept} is not reported for the latest {args.period_type} period (ended {newest.isoformat()}); "
                        f"its last value (period ended {end.isoformat()}) is superseded and not provided."
                    )
            parsed = [f for f in parsed if latest_by_concept[f.concept] == newest]
            freshness = reporting_period_freshness(
                newest,
                clock.now,
                period_type=args.period_type,
                fiscal_period=next((f.fiscal_period for f in parsed if f.end == newest), None),
                what=f"latest {args.period_type} reporting period",
            )
            if freshness.status == FreshnessStatus.STALE:
                missing.extend(dict.fromkeys(f.concept for f in parsed))
                notes.append(
                    f"The newest {args.period_type} figures (period ended {newest.isoformat()}) are older than the filing "
                    "deadlines allow, so a newer report should exist; they are not provided."
                )
                parsed = []
        source_name = source_name_for(sec.PROVIDER_LABEL + " XBRL company facts", payload)
        facts: list[FinancialMetric] = []
        evidence = []
        for concept in dict.fromkeys(f.concept for f in parsed):
            series = [f for f in parsed if f.concept == concept]
            latest = max(f.end for f in series)
            currency = "USD" if series[0].unit.startswith("USD") else None
            item = build_evidence(
                kind=EvidenceKind.FINANCIAL_METRIC,
                payload=payload,
                provider=sec.PROVIDER_ID,
                source_name=source_name,
                identity={
                    "cik": identity.cik,
                    "concept": concept,
                    "unit": series[0].unit,
                    "period_type": args.period_type,
                },
                content=[(f.start, f.end, f.value, f.accession_number) for f in series],
                symbol=identity.symbol,
                title=f"{series[0].label} — us-gaap:{concept} ({args.period_type})",
                source_url=self.sec.concept_url(identity.cik, concept),
                period_start=min(f.start for f in series if f.start),
                period_end=latest,
                currency=currency,
                units=series[0].unit,
                evidence_payload={
                    "taxonomy": "us-gaap",
                    "concept": concept,
                    "metric_name": series[0].metric_name,
                    "unit": series[0].unit,
                    "period_type": args.period_type,
                    "values": [
                        {
                            "start": f.start.isoformat() if f.start else None,
                            "end": f.end.isoformat(),
                            "value": str(f.value),
                            "fiscal_year": f.fiscal_year,
                            "fiscal_period": f.fiscal_period,
                            "form": f.form,
                            "filed": f.filed.isoformat(),
                            "accession_number": f.accession_number,
                            "frame": f.frame,
                        }
                        for f in series
                    ],
                    "selection_rule": "matching duration and form; latest filing wins for each reporting period",
                },
                freshness=freshness,
            )
            evidence.append(item)
            for f in series:
                facts.append(
                    FinancialMetric(
                        metric_name=f.metric_name,
                        label=f.label,
                        value=f.value,
                        unit=f.unit,
                        currency=currency,
                        period_type=args.period_type,
                        period_start=f.start,
                        period_end=f.end,
                        fiscal_year=f.fiscal_year,
                        fiscal_period=f.fiscal_period,
                        form=f.form,
                        filing_or_report_date=f.filed,
                        accession_number=f.accession_number,
                        concept=concept,
                        taxonomy="us-gaap",
                        provider=sec.PROVIDER_ID,
                        evidence_id=item.evidence_id,
                        is_fixture=payload.is_fixture,
                    )
                )
        return CompanyFactsData(
            symbol=identity.symbol,
            cik=identity.cik,
            company_name=entity_name or identity.company_name,
            period_type=args.period_type,
            facts=facts,
            missing_concepts=missing,
            notes=notes,
            is_fixture=payload.is_fixture,
            evidence=evidence,
        )

    # ------------------------------------------------------------------ B3 search_company_news
    async def search_company_news(self, args: NewsInput) -> NewsData:
        identity = await self.resolve_symbol(SymbolInput(symbol=args.symbol))
        symbol = identity.symbol
        if args.to_date < args.from_date:
            raise MarketLensError(ErrorCode.INVALID_INPUT, "to_date cannot precede from_date.")
        if (args.to_date - args.from_date).days > MAX_NEWS_RANGE_DAYS:
            raise MarketLensError(
                ErrorCode.DATE_RANGE_TOO_LARGE, f"News ranges are limited to {MAX_NEWS_RANGE_DAYS} days."
            )
        provider_id, tz_known, fallback_note = av.PROVIDER_ID, False, None
        try:
            if self._premium_news_refused and self.fallback_news_client is not None:
                raise MarketLensError(ErrorCode.CAPABILITY_UNAVAILABLE, "Alpha Vantage news is not in the plan.")
            payload, items = await self.news_client.news(
                symbol, time_from=args.from_date, time_to=args.to_date, limit=args.limit
            )
        except MarketLensError as exc:
            # Alpha Vantage news is a premium endpoint (D-86): with a plan that lacks it, live mode
            # uses Yahoo Finance's public headline feed instead of reporting news as unavailable.
            if exc.code != ErrorCode.CAPABILITY_UNAVAILABLE or self.fallback_news_client is None:
                self.tracker.failure("news", exc)
                raise
            self._premium_news_refused = True
            payload, items = await self._tracked("news", self.fallback_news_client.news(symbol))
            provider_id, tz_known = yahoo_news.PROVIDER_ID, True
            fallback_note = (
                "Alpha Vantage news is not included in the configured plan, so headlines come from Yahoo Finance's "
                "public feed, which lists only the latest headlines."
            )
        else:
            self.tracker.success("news")
        terms = [t.lower() for t in args.query.split() if len(t) > 2]
        out_of_window = without_date = off_topic = 0
        # The fallback feed also lists stories that only touch the company; keep those that name it.
        mentions = _mention_pattern(symbol, identity.company_name) if provider_id == yahoo_news.PROVIDER_ID else None
        kept = []
        for item in items:
            if item.published_date is None:
                without_date += 1
                continue
            if not (args.from_date <= item.published_date <= args.to_date):
                out_of_window += 1
                continue
            haystack = f"{item.title} {item.summary or ''}".lower()
            if terms and not any(t in haystack for t in terms):
                continue
            if mentions is not None and not mentions.search(f"{item.title} {item.summary or ''}"):
                off_topic += 1
                continue
            kept.append(item)
        kept.sort(key=lambda i: i.published_local or "", reverse=True)
        kept = kept[: args.limit]
        articles: list[NewsArticle] = []
        evidence = []
        for item in kept:
            excerpt = clean_snippet(item.summary, 400)
            flags = flags_for(f"{item.title}\n{item.summary or ''}")
            publisher = item.publisher or "Unknown publisher"
            ev = build_evidence(
                kind=EvidenceKind.NEWS,
                payload=payload,
                provider=provider_id,
                source_name=source_name_for(publisher, payload),
                identity={"symbol": symbol, "url": item.url, "title": item.title, "published": item.published_local},
                content={"title": item.title, "summary": excerpt},
                symbol=symbol,
                title=item.title,
                source_url=item.url,
                published_at=item.published_local,
                published_at_tz_known=tz_known,
                published_date=item.published_date,
                excerpt=excerpt,
                evidence_payload={
                    "publisher": publisher,
                    "topics": item.topics,
                    "provider_relevance_score": str(item.relevance) if item.relevance is not None else None,
                    "provider_sentiment_label": item.sentiment_label,
                    "metadata_note": "Relevance and sentiment labels are provider-supplied metadata, not MarketLens findings.",
                },
                flags=flags,
                notes=[] if tz_known else [NEWS_TZ_NOTE],
            )
            evidence.append(ev)
            articles.append(
                NewsArticle(
                    headline=item.title,
                    publisher=publisher,
                    url=item.url,
                    published_at=item.published_local,
                    published_at_tz_known=tz_known,
                    published_date=item.published_date,
                    summary_excerpt=excerpt,
                    topics=item.topics,
                    provider_relevance_score=item.relevance,
                    provider_sentiment_label=item.sentiment_label,
                    in_requested_window=True,
                    flags=flags,
                    evidence_id=ev.evidence_id,
                )
            )
        notes = [fallback_note] if fallback_note else [NEWS_TZ_NOTE]
        if out_of_window:
            notes.append(f"{out_of_window} provider item(s) published outside the requested window were excluded.")
        if without_date:
            notes.append(f"{without_date} provider item(s) without a publication date were excluded.")
        if off_topic:
            notes.append(f"{off_topic} headline(s) that don't name the company were excluded.")
        return NewsData(
            symbol=symbol,
            query=args.query,
            from_date=args.from_date,
            to_date=args.to_date,
            articles=articles,
            excluded_out_of_window=out_of_window,
            excluded_without_date=without_date,
            notes=notes,
            is_fixture=payload.is_fixture,
            evidence=evidence,
        )

    # ------------------------------------------------------------------ B4 get_filing_excerpt
    async def get_filing_excerpt(self, args: FilingExcerptInput) -> FilingExcerptData:
        cik_int, accession_nodash, filename = sec.parse_filing_url(args.filing_url, self.sec.transport.archives_base)
        cik = sec.cik10(cik_int)
        submissions = await self._tracked("filings", self.sec.submissions(cik))
        _, rows = sec.parse_submissions(submissions.data)
        match = next(
            (
                r
                for r in rows
                if r.accession_number.replace("-", "") == accession_nodash and r.primary_document == filename
            ),
            None,
        )
        if match is None or match.form not in sec.EXCERPT_FORMS:
            raise MarketLensError(
                ErrorCode.INVALID_INPUT,
                "The URL is not a 10-K, 10-Q or 8-K document listed in the issuer's SEC submissions metadata.",
            )
        document: ProviderPayload = await self._tracked(
            "filing_excerpt", self.sec.document(cik_int, accession_nodash, filename)
        )
        text = html_to_text(str(document.data))
        excerpt, truncated = sec.extract_section(text, args.section_hint, args.max_chars)
        if not excerpt:
            raise MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                f"No section matching '{args.section_hint}' was found in this filing document.",
            )
        flags = flags_for(excerpt)
        symbols = [s for s, rec in (await self.sec.ticker_map()).items() if rec.cik10 == cik]
        method = "heuristic heading search over HTML-to-text; not a complete legal reading"
        evidence = build_evidence(
            kind=EvidenceKind.FILING,
            payload=document,
            provider=sec.PROVIDER_ID,
            source_name=source_name_for(sec.PROVIDER_LABEL, document),
            identity={"url": args.filing_url, "section": args.section_hint.lower(), "max_chars": args.max_chars},
            content=excerpt,
            symbol=symbols[0] if symbols else None,
            title=f"Form {match.form} excerpt — {args.section_hint} (filed {match.filed_date.isoformat()})",
            source_url=args.filing_url,
            published_date=match.filed_date,
            period_end=match.report_date,
            excerpt=excerpt,
            evidence_payload={
                "record_type": "filing_excerpt",
                "form": match.form,
                "accession_number": match.accession_number,
                "section_hint": args.section_hint,
                "char_count": len(excerpt),
                "truncated": truncated,
                "extraction_method": method,
            },
            flags=flags,
            notes=["Excerpt is untrusted source text; it is quoted, not followed."],
        )
        return FilingExcerptData(
            filing_url=args.filing_url,
            form=match.form,
            filed_date=match.filed_date,
            period_end=match.report_date,
            accession_number=match.accession_number,
            section_hint=args.section_hint,
            section_found=True,
            excerpt=excerpt,
            char_count=len(excerpt),
            truncated=truncated,
            extraction_method=method,
            flags=flags,
            evidence_id=evidence.evidence_id,
            evidence=[evidence],
        )

    # ------------------------------------------------------------------ B5 get_research_sources_status
    async def get_research_sources_status(self) -> ServerStatusData:
        clock = self.clock()
        fixture = self._is_fixture
        mode = SourceMode.FIXTURE if fixture else SourceMode.LIVE
        limited, retry_after = self.tracker.rate_limit_state()

        def cap(name: str, fixture_value: bool | None = True, note: str | None = None) -> CapabilityStatus:
            return CapabilityStatus(
                name=name, available=fixture_value if fixture else self.tracker.available(name), note=note
            )

        sec_entry = SourceStatusEntry(
            provider=sec.PROVIDER_ID,
            configured=fixture or self.settings.sec_user_agent_configured,
            source_mode=mode,
            capabilities=[
                cap("symbol_mapping"),
                cap("filings"),
                cap("company_facts"),
                cap("filing_excerpt", note="Heuristic section extraction; not a complete legal reading."),
            ],
            last_successful_retrieval=self.tracker.latest_success,
            last_error_code=self.tracker.most_recent_error,
            rate_limited=limited,
            retry_after_seconds=retry_after,
            limitations=[
                "Company facts use standard us-gaap concepts; issuer-specific tags are reported as missing, not imputed.",
                "Filing excerpts are short quotations located by heading search.",
            ],
        )
        news_entry = SourceStatusEntry(
            provider=av.PROVIDER_ID,
            configured=fixture or self.settings.alpha_vantage_configured,
            source_mode=mode,
            capabilities=[
                cap("news", note="MSFT news is deliberately unavailable in the demo dataset." if fixture else None)
            ],
            last_successful_retrieval=self.tracker.last_success.get("news"),
            last_error_code=self.tracker.last_error.get("news"),
            rate_limited=limited,
            retry_after_seconds=retry_after,
            limitations=[NEWS_TZ_NOTE, "Full article text is never stored; only short excerpts."],
        )
        return ServerStatusData(
            server=SERVER_ID,
            data_mode=self.settings.data_mode,
            is_fixture=fixture,
            fixture_dataset_version=self.fixture_dataset.version if self.fixture_dataset else None,
            reference_time=clock.now,
            reference_basis=clock.basis,
            sources=[sec_entry, news_entry],
        )


def build_research_sources_service(settings: Settings) -> ResearchSourcesService:
    if settings.is_fixture_data:
        dataset = FixtureDataset(settings.fixture_root)
        return ResearchSourcesService(
            settings,
            sec.SecEdgarClient(FixtureSecTransport(dataset)),
            av.AlphaVantageClient(FixtureAlphaVantageTransport(dataset), fixture_url_base=dataset.url_base),
            lambda: reference_clock(settings, dataset),
            fixture_dataset=dataset,
        )
    from marketlens_core.db import Database, initialize_database
    from marketlens_core.providers.live_transport import LiveAlphaVantageTransport, LiveSecTransport, ProviderCache

    db = Database(settings.database_path)
    initialize_database(db)
    cache = ProviderCache(db)
    fallback = None
    if settings.news_fallback == "yahoo_rss":
        from marketlens_core.providers.live_transport import LiveYahooNewsTransport

        fallback = yahoo_news.YahooNewsClient(LiveYahooNewsTransport(settings, cache=cache))
    return ResearchSourcesService(
        settings,
        sec.SecEdgarClient(LiveSecTransport(settings, cache=cache)),
        av.AlphaVantageClient(LiveAlphaVantageTransport(settings, cache=cache)),
        lambda: reference_clock(settings),
        fallback_news_client=fallback,
    )
