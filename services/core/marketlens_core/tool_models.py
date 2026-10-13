"""MCP tool contracts shared by the servers (producers) and the backend (consumer).

- ``*Input`` models carry the argument constraints from MCP_TOOLS.md (allowlists, bounds).
- ``*Data`` models are the ``data`` payloads; every data-returning tool also returns the
  full ``EvidenceItem`` records it produced under ``evidence`` so the caller can build an
  evidence ledger without re-deriving provenance.
- ``ToolResult`` is the MCP_TOOLS §2 envelope: ``ok`` + ``data`` | ``error`` + ``meta``.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import Field, StringConstraints, field_validator

from marketlens_core.errors import ErrorCode, ErrorInfo, MarketLensError
from marketlens_core.hashing import new_id
from marketlens_core.models import (
    ActionType,
    CompanyIdentity,
    DomainModel,
    EvidenceItem,
    FilingExcerpt,
    FilingRecord,
    FinancialMetric,
    MarketQuote,
    NewsArticle,
    PendingAction,
    PriceChangeCalculation,
    PriceSeries,
    SavedBrief,
    SavedBriefSummary,
    SourceMode,
    WatchlistItem,
)
from marketlens_core.providers.sec_edgar import DEFAULT_CONCEPTS, DEFAULT_FORMS

logger = logging.getLogger("marketlens.tools")

# --------------------------------------------------------------------------- shared argument types
# MCP tool signatures and the *Input models below use these same aliases, so the advertised
# JSON schemas (enums, bounds, patterns) and server-side validation cannot drift apart.
SymbolArg = Annotated[
    str,
    StringConstraints(min_length=1, max_length=16),
    Field(description="US equity ticker (NYSE/Nasdaq), e.g. AAPL. Normalized and validated server-side."),
]
DateArg = Annotated[dt.date, Field(description="Calendar date YYYY-MM-DD (inclusive).")]
MetricName = Literal[
    "revenue", "gross_profit", "operating_income", "net_income", "ebitda", "reported_eps", "diluted_eps"
]
FormType = Literal["10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A", "DEF 14A", "4"]
ConceptName = Literal[
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
]
PeriodType = Literal["annual", "quarterly"]
PriceField = Literal["close", "open", "high", "low"]
ActionTypeArg = Literal["save_research_brief", "add_watchlist_symbol", "remove_watchlist_symbol"]
QueryArg = Annotated[
    str,
    StringConstraints(max_length=200),
    Field(description="Optional keywords; matched against headline and summary."),
]
FilingUrlArg = Annotated[
    str,
    StringConstraints(min_length=10, max_length=400),
    Field(description="Filing document URL exactly as returned by search_company_filings."),
]
SectionHintArg = Annotated[str, StringConstraints(min_length=3, max_length=80), Field(description="Heading to locate.")]
GrantArg = Annotated[
    str,
    StringConstraints(min_length=16, max_length=128),
    Field(description="One-time confirmation grant issued by the backend after a user confirmation click."),
]
PayloadHashArg = Annotated[
    str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"), Field(description="sha256 of the approved payload.")
]
SessionArg = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
    Field(description="Server-owned session identifier (hashed by the backend); never supplied by a model or browser."),
]
RunIdArg = Annotated[str, StringConstraints(pattern=r"^run_[0-9a-f]{16}$")]
BriefIdArg = Annotated[str, StringConstraints(pattern=r"^brief_[0-9a-f]{16}$")]
TitleArg = Annotated[str, StringConstraints(min_length=3, max_length=200)]


def _dedupe(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


# --------------------------------------------------------------------------- inputs
class ToolInput(DomainModel):
    pass


class SymbolInput(ToolInput):
    symbol: SymbolArg


SymbolQueryArg = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=40),
    Field(
        description="Start of a ticker or of a word in the company name, e.g. 'AA' or 'apple'. Empty lists the largest covered companies."
    ),
]


class SymbolSearchInput(ToolInput):
    query: SymbolQueryArg = ""
    limit: Annotated[int, Field(ge=1, le=20)] = 8


class HistoricalPricesInput(ToolInput):
    symbol: SymbolArg
    interval: Literal["daily"] = "daily"
    start_date: DateArg
    end_date: DateArg
    adjusted: bool = False


class FinancialMetricsInput(ToolInput):
    symbol: SymbolArg
    metrics: Annotated[list[MetricName], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: ["revenue", "operating_income", "net_income"]
    )
    period_type: PeriodType = "quarterly"
    limit: Annotated[int, Field(ge=1, le=12)] = 4

    _unique = field_validator("metrics")(_dedupe)


class PriceChangeInput(ToolInput):
    symbol: SymbolArg
    start_date: DateArg
    end_date: DateArg
    price_field: PriceField = "close"


class FilingsInput(ToolInput):
    symbol: SymbolArg
    forms: Annotated[list[FormType], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: list(DEFAULT_FORMS)
    )
    limit: Annotated[int, Field(ge=1, le=25)] = 8
    filed_after: DateArg | None = None

    _unique = field_validator("forms")(_dedupe)


class CompanyFactsInput(ToolInput):
    symbol: SymbolArg
    concepts: Annotated[list[ConceptName], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: list(DEFAULT_CONCEPTS)
    )
    period_type: PeriodType = "quarterly"
    limit: Annotated[int, Field(ge=1, le=12)] = 8

    _unique = field_validator("concepts")(_dedupe)


class NewsInput(ToolInput):
    symbol: SymbolArg
    query: QueryArg = ""
    from_date: DateArg
    to_date: DateArg
    limit: Annotated[int, Field(ge=1, le=50)] = 10


class FilingExcerptInput(ToolInput):
    filing_url: FilingUrlArg
    section_hint: SectionHintArg = "Risk Factors"
    max_chars: Annotated[int, Field(ge=1, le=5000)] = 3500


class EmptyInput(ToolInput):
    pass


class PayloadSummary(DomainModel):
    symbol: SymbolArg | None = None
    title: TitleArg | None = None
    display_name: Annotated[str, StringConstraints(max_length=120)] | None = None


class CreatePendingActionInput(ToolInput):
    action_type: ActionTypeArg
    run_id: RunIdArg | None = None
    payload_summary: PayloadSummary
    payload_hash: PayloadHashArg
    session_id: SessionArg
    origin: Literal["ui", "voice", "agent"] = "ui"


class WatchlistWriteInput(ToolInput):
    symbol: SymbolArg
    confirmation_grant_id: GrantArg
    expected_payload_hash: PayloadHashArg
    session_id: SessionArg


class SaveBriefInput(ToolInput):
    run_id: RunIdArg
    brief_title: TitleArg
    confirmation_grant_id: GrantArg
    expected_payload_hash: PayloadHashArg
    session_id: SessionArg


class ListSavedBriefsInput(ToolInput):
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    symbol: SymbolArg | None = None


class GetSavedBriefInput(ToolInput):
    brief_id: BriefIdArg


# --------------------------------------------------------------------------- data payloads
class QuoteData(MarketQuote):
    evidence: list[EvidenceItem]


class HistoricalPricesData(PriceSeries):
    evidence: list[EvidenceItem]


class PriceChangeData(PriceChangeCalculation):
    evidence: list[EvidenceItem]


class FinancialMetricsData(DomainModel):
    symbol: str
    period_type: Literal["annual", "quarterly"]
    metrics: list[FinancialMetric]
    missing_metrics: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    provider: str
    is_fixture: bool
    evidence: list[EvidenceItem]


class ResolveSymbolData(CompanyIdentity):
    pass


class SymbolMatch(DomainModel):
    symbol: str
    company_name: str
    exchange: str | None = None


class SymbolSearchData(DomainModel):
    query: str
    matches: list[SymbolMatch]
    coverage: SourceMode
    is_fixture: bool


class FilingsData(DomainModel):
    symbol: str
    company_name: str | None = None
    cik: str
    forms: list[str]
    filed_after: dt.date | None = None
    filings: list[FilingRecord]
    is_fixture: bool
    evidence: list[EvidenceItem]


class CompanyFactsData(DomainModel):
    symbol: str
    cik: str
    company_name: str | None = None
    period_type: Literal["annual", "quarterly"]
    facts: list[FinancialMetric]
    missing_concepts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    is_fixture: bool
    evidence: list[EvidenceItem]


class NewsData(DomainModel):
    symbol: str
    query: str
    from_date: dt.date
    to_date: dt.date
    articles: list[NewsArticle]
    excluded_out_of_window: int = 0
    excluded_without_date: int = 0
    notes: list[str] = Field(default_factory=list)
    is_fixture: bool
    evidence: list[EvidenceItem]


class FilingExcerptData(FilingExcerpt):
    evidence: list[EvidenceItem]


class CapabilityStatus(DomainModel):
    name: str
    available: bool | None
    note: str | None = None


class SourceStatusEntry(DomainModel):
    provider: str
    configured: bool
    source_mode: SourceMode
    capabilities: list[CapabilityStatus]
    last_successful_retrieval: dt.datetime | None = None
    last_error_code: ErrorCode | None = None
    rate_limited: bool = False
    retry_after_seconds: float | None = None
    limitations: list[str] = Field(default_factory=list)


class ServerStatusData(DomainModel):
    server: str
    data_mode: Literal["fixtures", "live"]
    is_fixture: bool
    fixture_dataset_version: str | None = None
    reference_time: dt.datetime
    reference_basis: str
    sources: list[SourceStatusEntry]
    warnings: list[str] = Field(default_factory=list)


class WatchlistData(DomainModel):
    items: list[WatchlistItem]


class WatchlistWriteData(DomainModel):
    symbol: str
    action_id: str
    item: WatchlistItem | None = None
    already_exists: bool = False
    removed: bool = False
    already_absent: bool = False


class SaveBriefData(DomainModel):
    brief_id: str
    run_id: str
    title: str
    created_at: dt.datetime
    report_status: str
    data_mode: str
    is_fixture: bool
    action_id: str


class SavedBriefListData(DomainModel):
    briefs: list[SavedBriefSummary]


class SavedBriefData(DomainModel):
    saved: SavedBrief


class PendingActionData(DomainModel):
    action: PendingAction
    action_types: list[ActionType] = Field(default_factory=lambda: list(ActionType))


# --------------------------------------------------------------------------- envelope
T = TypeVar("T")


class ToolMeta(DomainModel):
    request_id: str
    server: str
    tool: str
    source_mode: SourceMode | None = None
    is_fixture: bool | None = None
    retrieved_at: dt.datetime | None = None
    source_name: str | None = None
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int | None = None


class ToolResult(DomainModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ErrorInfo | None = None
    meta: ToolMeta


def _meta_from_data(data: Any) -> dict[str, Any]:
    evidence = getattr(data, "evidence", None) or []
    extras: dict[str, Any] = {}
    if evidence:
        extras["retrieved_at"] = max(e.retrieved_at for e in evidence)
        extras["source_name"] = evidence[0].source_name
        extras["source_mode"] = evidence[0].source_mode
        extras["is_fixture"] = all(e.is_fixture for e in evidence)
    elif hasattr(data, "is_fixture"):
        extras["is_fixture"] = bool(data.is_fixture)
        extras["source_mode"] = SourceMode.FIXTURE if data.is_fixture else SourceMode.LIVE
    warnings = list(getattr(data, "coverage_warnings", None) or []) + list(getattr(data, "warnings", None) or [])
    if warnings:
        extras["warnings"] = warnings
    return extras


async def execute_tool(
    server: str,
    tool: str,
    operation: Callable[[], Awaitable[DomainModel]],
    *,
    request_id: str | None = None,
) -> ToolResult[Any]:
    """Run a tool body and wrap the outcome in the standard envelope (never raises)."""
    started = time.monotonic()
    rid = request_id or new_id("req")
    try:
        data = await operation()
    except MarketLensError as exc:
        return ToolResult(
            ok=False,
            error=exc.to_info(),
            meta=ToolMeta(request_id=rid, server=server, tool=tool, duration_ms=_ms(started)),
        )
    except Exception:  # noqa: BLE001 - tool boundary: never leak internals
        logger.exception("tool %s.%s failed unexpectedly", server, tool)
        return ToolResult(
            ok=False,
            error=ErrorInfo(code=ErrorCode.INTERNAL_ERROR, message="The tool failed unexpectedly."),
            meta=ToolMeta(request_id=rid, server=server, tool=tool, duration_ms=_ms(started)),
        )
    return ToolResult(
        ok=True,
        data=data,
        meta=ToolMeta(request_id=rid, server=server, tool=tool, duration_ms=_ms(started), **_meta_from_data(data)),
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def validation_error(exc: Exception) -> MarketLensError:
    """Translate a Pydantic validation failure into a safe INVALID_INPUT error."""
    errors = getattr(exc, "errors", None)
    details: list[dict[str, Any]] = []
    if callable(errors):
        for err in errors()[:10]:
            details.append({"field": ".".join(str(p) for p in err.get("loc", ())), "issue": err.get("msg", "invalid")})
    return MarketLensError(ErrorCode.INVALID_INPUT, "The tool arguments are invalid.", details={"fields": details})
