"""Canonical MarketLens domain models (CLAUDE_CODE_MASTER §7, architecture §4, MCP_TOOLS §3).

Conventions
- Timestamps are timezone-aware UTC (``datetime``) unless a provider supplies only a date
  or a zone-less timestamp; those are kept as dates or as strings with an explicit
  ``*_tz_known`` flag instead of inventing a zone or time of day.
- Daily price bars are identified by exchange trading date (US/Eastern), not a fabricated
  midnight-UTC timestamp.
- Financial values are ``Decimal`` internally and JSON numbers on the wire (see
  ``JsonDecimal``); calculations never use binary floats.
- Missing values are explicit ``None``; nothing is inferred to fill a gap.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from marketlens_core.errors import ErrorCode

JsonDecimal = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class DomainModel(BaseModel):
    # Serialized outputs always include defaulted fields, so the published (serialization)
    # JSON schema marks them required; request schemas keep them optional.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# --------------------------------------------------------------------------- enums
class SourceMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class DataMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"
    MIXED = "mixed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.PARTIAL, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class RunStage(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    COLLECTING_EVIDENCE = "collecting_evidence"
    SPECIALIST_ANALYSIS = "specialist_analysis"
    SYNTHESIZING = "synthesizing"
    EVIDENCE_REVIEW = "evidence_review"
    FINALIZING = "finalizing"
    DONE = "done"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class EvidenceKind(StrEnum):
    PRICE_SERIES = "price_series"
    QUOTE = "quote"
    FINANCIAL_METRIC = "financial_metric"
    FILING = "filing"
    NEWS = "news"
    CALCULATION = "calculation"


class ClaimType(StrEnum):
    SOURCE_FACT = "source_fact"
    CALCULATION = "calculation"
    INTERPRETATION = "interpretation"
    SCENARIO = "scenario"


class ValidationStatus(StrEnum):
    SUPPORTED = "supported"
    NEEDS_QUALIFICATION = "needs_qualification"
    UNSUPPORTED = "unsupported"
    DATE_MISMATCH = "date_mismatch"
    NUMERIC_MISMATCH = "numeric_mismatch"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class ResearchCategory(StrEnum):
    MARKET = "market"
    FUNDAMENTALS = "fundamentals"
    FILINGS = "filings"
    NEWS = "news"


class CategoryStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"  # capability not offered (plan/coverage) — documented limitation
    FAILED = "failed"  # an error occurred (timeout, rate limit, malformed, server down)
    NOT_REQUESTED = "not_requested"


class BriefSection(StrEnum):
    EXECUTIVE_SUMMARY = "executive_summary"
    MARKET_MOVEMENT = "market_movement"
    FUNDAMENTALS = "fundamentals"
    FILINGS = "filings"
    RECENT_EVENTS = "recent_events"
    POSITIVE_EVIDENCE = "positive_evidence"
    NEGATIVE_EVIDENCE = "negative_evidence"
    RISKS = "risks"
    INTERPRETATION = "interpretation"
    SCENARIOS = "scenarios"


class FocusArea(StrEnum):
    PRICE_MOVEMENT = "price_movement"
    FUNDAMENTALS = "fundamentals"
    FILINGS = "filings"
    NEWS = "news"
    RISKS = "risks"


class ActionType(StrEnum):
    SAVE_RESEARCH_BRIEF = "save_research_brief"
    ADD_WATCHLIST_SYMBOL = "add_watchlist_symbol"
    REMOVE_WATCHLIST_SYMBOL = "remove_watchlist_symbol"


class ActionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


# --------------------------------------------------------------------------- evidence
class Freshness(DomainModel):
    status: FreshnessStatus
    reference_time: dt.datetime
    observed: str | None = None
    age_days: int | None = None
    threshold_days: int | None = None
    note: str


class EvidenceItem(DomainModel):
    """One retrieved or calculated piece of evidence (MCP_TOOLS §3.1)."""

    evidence_id: str
    kind: EvidenceKind
    symbol: str | None = None
    title: str | None = None
    source_name: str
    provider: str
    source_url: str | None = None
    published_at: str | None = None
    published_at_tz_known: bool | None = None
    published_date: dt.date | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    retrieved_at: dt.datetime
    currency: str | None = None
    units: str | None = None
    source_mode: SourceMode
    is_fixture: bool
    content_hash: str
    excerpt: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    freshness: Freshness | None = None
    flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CompanyIdentity(DomainModel):
    symbol: str
    company_name: str | None = None
    cik: str | None = None
    exchange: str | None = None
    coverage: SourceMode
    is_fixture: bool


# --------------------------------------------------------------------------- market data
class MarketQuote(DomainModel):
    symbol: str
    price: JsonDecimal
    currency: str
    observed_date: dt.date | None = None
    observed_at: dt.datetime | None = None
    observation_basis: str
    provider: str
    market_state: Literal["unknown", "open", "closed", "delayed"] = "unknown"
    previous_close: JsonDecimal | None = None
    volume: int | None = None
    is_fixture: bool
    evidence_id: str


class PriceBar(DomainModel):
    trading_date: dt.date
    open: JsonDecimal
    high: JsonDecimal
    low: JsonDecimal
    close: JsonDecimal
    volume: int | None = None


class PriceSeries(DomainModel):
    symbol: str
    interval: Literal["daily"] = "daily"
    currency: str
    adjustment: Literal["adjusted", "unadjusted", "unknown"]
    timezone: str
    bars: list[PriceBar]
    requested_start: dt.date
    requested_end: dt.date
    first_date: dt.date | None = None
    last_date: dt.date | None = None
    provider: str
    is_fixture: bool
    evidence_id: str
    coverage_warnings: list[str] = Field(default_factory=list)


class FinancialMetric(DomainModel):
    metric_name: str
    label: str
    value: JsonDecimal
    unit: str
    currency: str | None = None
    period_type: Literal["annual", "quarterly"]
    period_start: dt.date | None = None
    period_end: dt.date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form: str | None = None
    filing_or_report_date: dt.date | None = None
    accession_number: str | None = None
    concept: str | None = None
    taxonomy: str | None = None
    provider: str
    evidence_id: str
    is_fixture: bool


class PriceChangeCalculation(DomainModel):
    symbol: str
    price_field: Literal["close", "open", "high", "low"] = "close"
    requested_start: dt.date
    requested_end: dt.date
    start_value: JsonDecimal | None = None
    start_date: dt.date | None = None
    end_value: JsonDecimal | None = None
    end_date: dt.date | None = None
    absolute_change: JsonDecimal | None = None
    percentage_change: JsonDecimal | None = None
    formula: str = "(end_value - start_value) / start_value * 100"
    input_evidence_ids: list[str] = Field(default_factory=list)
    currency: str | None = None
    unavailable_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    is_fixture: bool
    evidence_id: str


# --------------------------------------------------------------------------- research sources
class FilingRecord(DomainModel):
    form: str
    filed_date: dt.date
    accepted_at: dt.datetime | None = None
    period_end: dt.date | None = None
    accession_number: str
    cik: str
    company_name: str | None = None
    primary_document: str | None = None
    description: str | None = None
    source_url: str | None = None
    evidence_id: str


class NewsArticle(DomainModel):
    headline: str
    publisher: str | None = None
    url: str | None = None
    published_at: str | None = None
    published_at_tz_known: bool = False
    published_date: dt.date | None = None
    summary_excerpt: str | None = None
    topics: list[str] = Field(default_factory=list)
    provider_relevance_score: JsonDecimal | None = None
    provider_sentiment_label: str | None = None
    in_requested_window: bool = True
    flags: list[str] = Field(default_factory=list)
    evidence_id: str


class FilingExcerpt(DomainModel):
    filing_url: str
    form: str | None = None
    filed_date: dt.date | None = None
    period_end: dt.date | None = None
    accession_number: str | None = None
    section_hint: str | None = None
    section_found: bool
    excerpt: str
    char_count: int
    truncated: bool
    extraction_method: str
    flags: list[str] = Field(default_factory=list)
    evidence_id: str


# --------------------------------------------------------------------------- research runs
class TimeHorizonInput(DomainModel):
    preset: Literal["1m", "3m", "6m", "1y"] | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class TimeHorizon(DomainModel):
    start_date: dt.date
    end_date: dt.date
    label: str
    is_default: bool
    reference_date: dt.date
    basis: str


class ResearchRequest(DomainModel):
    symbol: str = Field(min_length=1, max_length=16)
    question: str = Field(min_length=3, max_length=1000)
    time_horizon: TimeHorizonInput | None = None
    focus_areas: list[FocusArea] = Field(default_factory=list, max_length=5)
    requested_mode: Literal["configured", "fixture", "live"] = "configured"
    client_request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


class RunError(DomainModel):
    code: ErrorCode
    message: str
    category: ResearchCategory | None = None
    step_key: str | None = None
    retryable: bool = False


class ResearchRun(DomainModel):
    run_id: str
    symbol: str
    company: CompanyIdentity | None = None
    question: str
    time_horizon: TimeHorizon
    focus_areas: list[FocusArea] = Field(default_factory=list)
    status: RunStatus
    stage: RunStage
    data_mode: DataMode
    llm_mode: str
    origin: Literal["text", "voice", "eval", "rerun"] = "text"
    parent_run_id: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    is_partial: bool = False
    errors: list[RunError] = Field(default_factory=list)
    cancel_requested: bool = False
    duration_ms: int | None = None


class AgentStep(DomainModel):
    run_id: str
    seq: int
    step_key: str
    agent: str
    status: StepStatus
    summary: str | None = None
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    latency_ms: int | None = None
    error_code: ErrorCode | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(DomainModel):
    call_id: str
    run_id: str | None = None
    seq: int | None = None
    server_id: str
    tool_name: str
    transport: Literal["mcp_stdio", "in_process"]
    input_summary: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "error"]
    error_code: ErrorCode | None = None
    latency_ms: int | None = None
    output_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    idempotency_key: str | None = None


class DataSourceStatus(DomainModel):
    category: ResearchCategory
    status: CategoryStatus
    source_name: str | None = None
    source_mode: SourceMode | None = None
    reason_code: ErrorCode | None = None
    message: str | None = None
    evidence_count: int = 0
    latest_observation: str | None = None
    retrieved_at: dt.datetime | None = None
    tools: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- brief
class ResearchClaim(DomainModel):
    claim_id: str
    section: BriefSection
    text: str
    claim_type: ClaimType
    evidence_ids: list[str] = Field(default_factory=list)
    materiality: Literal["material", "supporting"] = "material"
    validation_status: ValidationStatus = ValidationStatus.UNVERIFIED
    validation_notes: list[str] = Field(default_factory=list)
    confidence_note: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    produced_by: str


class KeyMetric(DomainModel):
    label: str
    value: JsonDecimal | None = None
    display_value: str
    unit: str | None = None
    currency: str | None = None
    as_of: str
    period_label: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    claim_type: ClaimType
    group: Literal["market", "fundamentals"]
    note: str | None = None


class EventEntry(DomainModel):
    evidence_id: str
    headline: str
    publisher: str | None = None
    url: str | None = None
    published_at: str | None = None
    published_at_tz_known: bool = False
    retrieved_at: dt.datetime
    relevance_note: str
    in_requested_window: bool = True
    flags: list[str] = Field(default_factory=list)


class GapItem(DomainModel):
    category: ResearchCategory | None = None
    description: str
    reason_code: str | None = None
    severity: Literal["info", "material"] = "info"


class SourceRef(DomainModel):
    evidence_id: str
    kind: EvidenceKind
    title: str | None = None
    source_name: str
    source_url: str | None = None
    published_at: str | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    retrieved_at: dt.datetime
    is_fixture: bool
    source_mode: SourceMode
    flags: list[str] = Field(default_factory=list)


class AsOfMetadata(DomainModel):
    reference_time: dt.datetime
    reference_basis: str
    market_observation_date: dt.date | None = None
    price_series_end: dt.date | None = None
    latest_filing_date: dt.date | None = None
    latest_fiscal_period_end: dt.date | None = None
    latest_news_published_at: str | None = None
    earliest_retrieved_at: dt.datetime | None = None
    latest_retrieved_at: dt.datetime | None = None
    fixture_dataset_version: str | None = None


class MethodologyStep(DomainModel):
    name: str
    description: str
    agent: str
    tools: list[str] = Field(default_factory=list)


class Methodology(DomainModel):
    steps: list[MethodologyStep]
    deterministic_calculations: list[str]
    model_generated: list[str]
    llm_mode: str
    llm_model: str | None = None
    data_mode: DataMode
    sources_used: list[str]


class ReviewSummary(DomainModel):
    reviewer: str
    claims_total: int
    supported: int
    needs_qualification: int
    withheld: int
    numeric_checks_passed: int
    numeric_checks_failed: int
    notes: list[str] = Field(default_factory=list)


class ResearchBrief(DomainModel):
    brief_version: Literal["1.0"] = "1.0"
    run_id: str
    title: str
    company: CompanyIdentity
    question: str
    time_horizon: TimeHorizon
    generated_at: dt.datetime
    report_status: RunStatus
    data_mode: DataMode
    llm_mode: str
    is_fixture: bool
    as_of: AsOfMetadata
    key_metrics: list[KeyMetric] = Field(default_factory=list)
    recent_events: list[EventEntry] = Field(default_factory=list)
    claims: list[ResearchClaim] = Field(default_factory=list)
    withheld_claims: list[ResearchClaim] = Field(default_factory=list)
    unknowns_and_gaps: list[GapItem] = Field(default_factory=list)
    source_quality_notes: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    source_status: list[DataSourceStatus] = Field(default_factory=list)
    methodology: Methodology
    limitations: list[str] = Field(default_factory=list)
    review: ReviewSummary
    disclaimer: str
    fixture_disclosure: str | None = None


# --------------------------------------------------------------------------- workspace
class WatchlistItem(DomainModel):
    symbol: str
    display_name: str | None = None
    origin: str
    created_at: dt.datetime
    updated_at: dt.datetime
    last_research_at: dt.datetime | None = None


class SavedBriefSummary(DomainModel):
    brief_id: str
    run_id: str
    title: str
    symbol: str
    created_at: dt.datetime
    report_status: RunStatus
    data_mode: DataMode
    is_fixture: bool


class SavedBrief(SavedBriefSummary):
    payload_hash: str
    brief: ResearchBrief
    evidence: list[EvidenceItem] = Field(default_factory=list)


class PendingAction(DomainModel):
    action_id: str
    action_type: ActionType
    run_id: str | None = None
    symbol: str | None = None
    title: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str
    status: ActionStatus
    origin: Literal["ui", "voice", "agent"] = "ui"
    created_at: dt.datetime
    expires_at: dt.datetime
    confirmed_at: dt.datetime | None = None
    consumed_at: dt.datetime | None = None
    result: dict[str, Any] | None = None


class ConfirmationGrant(DomainModel):
    """A one-time write authorization. The secret grant ID itself is never serialized."""

    action_id: str
    issued_at: dt.datetime
    expires_at: dt.datetime
    consumed_at: dt.datetime | None = None
    grant_id: str = Field(exclude=True, repr=False)
