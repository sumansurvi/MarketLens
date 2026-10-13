"""SEC EDGAR adapter: ticker mapping, submissions, XBRL company facts and filing text.

Uses the official ``data.sec.gov`` JSON APIs and the documented Archives URL convention
(``/Archives/edgar/data/{cik}/{accession-without-dashes}/{document}``). Filing URLs are
only ever built from submissions metadata, and ``get_filing_excerpt`` accepts a URL only
if it matches that convention *and* appears in the issuer's submissions (MCP_TOOLS §0).

Company facts: concepts are allowlisted standard ``us-gaap`` tags. Issuers use different
revenue tags, so missing concepts are reported explicitly and never imputed.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.models import SourceMode
from marketlens_core.providers.alpha_vantage import parse_decimal
from marketlens_core.providers.base import ProviderPayload

PROVIDER_ID = "sec_edgar"
PROVIDER_LABEL = "SEC EDGAR"
LIVE_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
LIVE_DATA_API_BASE = "https://data.sec.gov"

ALLOWED_FORMS = frozenset({"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A", "DEF 14A", "4"})
DEFAULT_FORMS = ("10-K", "10-Q", "8-K")
EXCERPT_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "8-K"})
ALLOWED_EXCHANGES = frozenset({"Nasdaq", "NYSE"})

# concept -> (metric_name, label, unit key)
CONCEPTS: dict[str, tuple[str, str, str]] = {
    "Revenues": ("revenue", "Revenue", "USD"),
    "RevenueFromContractWithCustomerExcludingAssessedTax": ("revenue", "Revenue", "USD"),
    "SalesRevenueNet": ("revenue", "Revenue", "USD"),
    "GrossProfit": ("gross_profit", "Gross profit", "USD"),
    "OperatingIncomeLoss": ("operating_income", "Operating income (loss)", "USD"),
    "NetIncomeLoss": ("net_income", "Net income (loss)", "USD"),
    "EarningsPerShareDiluted": ("diluted_eps", "Diluted EPS", "USD/shares"),
    "EarningsPerShareBasic": ("basic_eps", "Basic EPS", "USD/shares"),
}
REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
DEFAULT_CONCEPTS = (*REVENUE_CONCEPTS, "OperatingIncomeLoss", "NetIncomeLoss", "EarningsPerShareDiluted")


class SecTransport(Protocol):
    source_mode: SourceMode

    @property
    def archives_base(self) -> str: ...

    @property
    def data_api_base(self) -> str: ...

    async def tickers_exchange(self) -> ProviderPayload: ...

    async def submissions(self, cik10: str) -> ProviderPayload: ...

    async def companyfacts(self, cik10: str) -> ProviderPayload: ...

    async def document(self, cik_int: int, accession_nodash: str, filename: str) -> ProviderPayload: ...


def _malformed(what: str) -> MarketLensError:
    return MarketLensError(ErrorCode.MALFORMED_PROVIDER_RESPONSE, f"SEC EDGAR returned an unexpected {what} format.")


def cik10(cik: int | str) -> str:
    return f"{int(cik):010d}"


# --------------------------------------------------------------------------- tickers
@dataclass(frozen=True)
class TickerRecord:
    symbol: str
    cik10: str
    company_name: str
    exchange: str | None


def parse_tickers_exchange(data: Any) -> dict[str, TickerRecord]:
    if not isinstance(data, dict) or not isinstance(data.get("fields"), list) or not isinstance(data.get("data"), list):
        raise _malformed("ticker mapping")
    fields = data["fields"]
    try:
        i_cik, i_name, i_ticker, i_exchange = (fields.index(k) for k in ("cik", "name", "ticker", "exchange"))
    except ValueError as exc:
        raise _malformed("ticker mapping") from exc
    mapping: dict[str, TickerRecord] = {}
    for row in data["data"]:
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        ticker = str(row[i_ticker]).upper()
        if ticker in mapping:
            continue
        mapping[ticker] = TickerRecord(
            symbol=ticker,
            cik10=cik10(row[i_cik]),
            company_name=str(row[i_name]),
            exchange=str(row[i_exchange]) if row[i_exchange] else None,
        )
    return mapping


def lookup_ticker(mapping: dict[str, TickerRecord], symbol: str) -> TickerRecord | None:
    """Exact match first. Class shares are written BRK.B or BRK-B, and the SEC list uses one
    form, so the other separator is tried too; nothing else is fuzzy-matched."""
    record = mapping.get(symbol)
    if record is None and ("." in symbol or "-" in symbol):
        record = mapping.get(symbol.replace(".", "-")) or mapping.get(symbol.replace("-", "."))
    return record


# --------------------------------------------------------------------------- submissions
@dataclass(frozen=True)
class SubmissionRow:
    accession_number: str
    form: str
    filed_date: dt.date
    report_date: dt.date | None
    accepted_at: dt.datetime | None
    primary_document: str | None
    description: str | None


def parse_submissions(data: Any) -> tuple[str | None, list[SubmissionRow]]:
    try:
        recent = data["filings"]["recent"]
        columns = {k: recent[k] for k in ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")}
    except (KeyError, TypeError) as exc:
        raise _malformed("submissions") from exc
    lengths = {len(v) for v in columns.values() if isinstance(v, list)}
    if len(lengths) != 1 or not all(isinstance(v, list) for v in columns.values()):
        raise _malformed("submissions arrays")
    accepted = recent.get("acceptanceDateTime") or []
    descriptions = recent.get("primaryDocDescription") or []
    rows: list[SubmissionRow] = []
    for i, accession in enumerate(columns["accessionNumber"]):
        try:
            filed = dt.date.fromisoformat(columns["filingDate"][i])
        except (TypeError, ValueError) as exc:
            raise _malformed("filing date") from exc
        report_raw = columns["reportDate"][i]
        report = dt.date.fromisoformat(report_raw) if report_raw else None
        accepted_at = None
        if i < len(accepted) and accepted[i]:
            try:
                accepted_at = dt.datetime.fromisoformat(str(accepted[i]).replace("Z", "+00:00"))
            except ValueError:
                accepted_at = None
        rows.append(
            SubmissionRow(
                accession_number=str(accession),
                form=str(columns["form"][i]),
                filed_date=filed,
                report_date=report,
                accepted_at=accepted_at,
                primary_document=str(columns["primaryDocument"][i]) or None,
                description=str(descriptions[i]) if i < len(descriptions) and descriptions[i] else None,
            )
        )
    name = data.get("name") if isinstance(data.get("name"), str) else None
    return name, rows


def filing_document_url(archives_base: str, cik: str | int, accession: str, document: str) -> str:
    return f"{archives_base}/{int(cik)}/{accession.replace('-', '')}/{document}"


def parse_filing_url(url: str, archives_base: str) -> tuple[int, str, str]:
    """Validate a filing document URL against the Archives convention; return its parts."""
    pattern = re.compile("^" + re.escape(archives_base) + r"/(\d{1,10})/(\d{18})/([A-Za-z0-9][A-Za-z0-9._-]{0,150})$")
    match = pattern.fullmatch(url.strip()) if isinstance(url, str) else None
    if not match or ".." in url:
        raise MarketLensError(
            ErrorCode.INVALID_INPUT,
            "Only SEC filing document URLs returned by search_company_filings are accepted.",
        )
    return int(match.group(1)), match.group(2), match.group(3)


# --------------------------------------------------------------------------- company facts
@dataclass(frozen=True)
class ParsedFact:
    concept: str
    metric_name: str
    label: str
    unit: str
    value: Decimal
    start: dt.date | None
    end: dt.date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    filed: dt.date
    accession_number: str
    frame: str | None


def _duration_ok(start: dt.date | None, end: dt.date, period_type: str) -> bool:
    if start is None:
        return False
    days = (end - start).days
    return 330 <= days <= 400 if period_type == "annual" else 80 <= days <= 100


def parse_company_facts(
    data: Any, concepts: list[str], period_type: str, limit: int
) -> tuple[str | None, list[ParsedFact], list[str], list[str]]:
    """Select facts per concept: right duration/form, deduped to the latest filing per period."""
    if not isinstance(data, dict) or not isinstance(data.get("facts"), dict):
        raise _malformed("company facts")
    gaap = data["facts"].get("us-gaap")
    if not isinstance(gaap, dict):
        gaap = {}
    facts: list[ParsedFact] = []
    missing: list[str] = []
    notes: list[str] = []
    forms = ("10-K", "10-K/A") if period_type == "annual" else ("10-Q", "10-Q/A")
    for concept in concepts:
        metric_name, label, unit = CONCEPTS[concept]
        node = gaap.get(concept)
        if not isinstance(node, dict):
            missing.append(concept)
            continue
        units = node.get("units")
        entries = units.get(unit) if isinstance(units, dict) else None
        if not isinstance(entries, list):
            missing.append(concept)
            available = sorted(units) if isinstance(units, dict) else []
            notes.append(f"{concept}: no values in unit {unit} (units present: {', '.join(available) or 'none'}).")
            continue
        # Value/accession come from the latest filing reporting a period (restatement-aware);
        # fiscal labels come from the earliest one, because SEC ``fy``/``fp`` describe the
        # filing, so a later comparative would mislabel the period's fiscal year.
        selected: dict[tuple[str | None, str], ParsedFact] = {}
        earliest: dict[tuple[str | None, str], tuple[dt.date, str, int | None, str | None]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise _malformed("fact entry")
            try:
                end = dt.date.fromisoformat(entry["end"])
                start = dt.date.fromisoformat(entry["start"]) if entry.get("start") else None
                filed = dt.date.fromisoformat(entry["filed"])
                form = str(entry["form"])
                accn = str(entry["accn"])
            except (KeyError, TypeError, ValueError) as exc:
                raise _malformed("fact entry") from exc
            if form not in forms or not _duration_ok(start, end, period_type):
                continue
            if period_type == "annual" and entry.get("fp") not in (None, "FY"):
                continue
            fact = ParsedFact(
                concept=concept,
                metric_name=metric_name,
                label=label,
                unit=unit,
                value=parse_decimal(entry.get("val"), f"{concept} value"),
                start=start,
                end=end,
                fiscal_year=int(entry["fy"]) if isinstance(entry.get("fy"), int) else None,
                fiscal_period=str(entry["fp"]) if entry.get("fp") else None,
                form=form,
                filed=filed,
                accession_number=accn,
                frame=str(entry["frame"]) if entry.get("frame") else None,
            )
            key = (start.isoformat() if start else None, end.isoformat())
            current = selected.get(key)
            if current is None or (fact.filed, fact.accession_number) > (current.filed, current.accession_number):
                selected[key] = fact
            first = earliest.get(key)
            if first is None or (filed, accn) < (first[0], first[1]):
                earliest[key] = (filed, accn, fact.fiscal_year, fact.fiscal_period)
        for key, fact in list(selected.items()):
            _, _, fiscal_year, fiscal_period = earliest[key]
            selected[key] = ParsedFact(**{**fact.__dict__, "fiscal_year": fiscal_year, "fiscal_period": fiscal_period})
        chosen = sorted(selected.values(), key=lambda f: f.end, reverse=True)[:limit]
        if not chosen:
            missing.append(concept)
            notes.append(f"{concept}: no {period_type} values with a matching reporting duration.")
            continue
        facts.extend(chosen)
    name = data.get("entityName") if isinstance(data.get("entityName"), str) else None
    return name, facts, missing, notes


# --------------------------------------------------------------------------- filing text
_ITEM_HEADING = re.compile(r"^item\s+\d+[a-z]?\.", re.IGNORECASE)


def extract_section(text: str, section_hint: str, max_chars: int) -> tuple[str | None, bool]:
    """Return (excerpt, truncated). Picks the occurrence of the heading with the longest body,
    which skips table-of-contents entries. Heuristic — not a complete legal reading."""
    hint = section_hint.strip().lower()
    lines = text.split("\n")
    best = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        heading_like = bool(_ITEM_HEADING.match(stripped)) or len(stripped) <= len(hint) + 25
        if hint not in stripped.lower() or not heading_like:
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if _ITEM_HEADING.match(following.strip()):
                break
            body.append(following)
        candidate = "\n".join(body).strip()
        if len(candidate) > len(best):
            best = candidate
    if len(best) < 40:
        return None, False
    if len(best) <= max_chars:
        return best, False
    cut = best[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind(".\n"))
    if boundary > max_chars * 0.5:
        cut = cut[: boundary + 1]
    return cut.rstrip(), True


# --------------------------------------------------------------------------- client
class SecEdgarClient:
    def __init__(self, transport: SecTransport) -> None:
        self.transport = transport
        self._tickers: dict[str, TickerRecord] | None = None

    @property
    def source_mode(self) -> SourceMode:
        return self.transport.source_mode

    async def ticker_map(self) -> dict[str, TickerRecord]:
        if self._tickers is None:
            payload = await self.transport.tickers_exchange()
            self._tickers = parse_tickers_exchange(payload.data)
        return self._tickers

    async def submissions(self, cik: str) -> ProviderPayload:
        return await self.transport.submissions(cik)

    async def companyfacts(self, cik: str) -> ProviderPayload:
        return await self.transport.companyfacts(cik)

    async def document(self, cik_int: int, accession_nodash: str, filename: str) -> ProviderPayload:
        return await self.transport.document(cik_int, accession_nodash, filename)

    def filing_url(self, cik: str | int, accession: str, document: str) -> str:
        return filing_document_url(self.transport.archives_base, cik, accession, document)

    def concept_url(self, cik: str, concept: str) -> str:
        return f"{self.transport.data_api_base}/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"

    def submissions_url(self, cik: str) -> str:
        return f"{self.transport.data_api_base}/submissions/CIK{cik}.json"
