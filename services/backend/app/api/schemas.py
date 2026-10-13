"""HTTP response contracts. The web client's TypeScript types are generated from these."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from marketlens_core.errors import ErrorCode
from marketlens_core.models import (
    ActionType,
    AgentStep,
    DataSourceStatus,
    EvidenceItem,
    PendingAction,
    PriceChangeCalculation,
    ResearchBrief,
    ResearchRun,
    SavedBrief,
    SavedBriefSummary,
    ToolCallRecord,
    WatchlistItem,
)
from marketlens_core.tool_models import ServerStatusData, SymbolMatch
from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class ApiError(ApiModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    retry_after_seconds: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(ApiModel):
    error: ApiError


class PolicyInfo(ApiModel):
    research_only: bool
    trading_supported: bool
    brokerage_integration: bool
    personalized_recommendations: bool
    persistent_writes: list[str]
    writes_require_confirmation_grant: bool
    notice: str


class McpServerStatus(ApiModel):
    ready: bool
    tools: list[str]
    restarts: int
    last_error: str | None = None


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: str = "marketlens-backend"
    version: str
    app_env: str
    app_mode: str
    data_mode: str
    llm_mode: str
    voice_mode: str
    voice_enabled: bool
    capabilities: dict[str, bool]
    tool_transport: Literal["mcp_stdio", "in_process"]
    mcp_servers: dict[str, McpServerStatus] = Field(default_factory=dict)
    demo_notice: str | None = None
    research_only_notice: str
    policy: PolicyInfo
    warnings: list[str] = Field(default_factory=list)
    server_time: dt.datetime


class DataStatusResponse(ApiModel):
    data_mode: str
    is_fixture: bool
    fixture_dataset_version: str | None = None
    reference_time: dt.datetime | None = None
    reference_basis: str | None = None
    servers: list[ServerStatusData]
    unavailable_servers: list[str] = Field(default_factory=list)
    latest_research_by_symbol: dict[str, dt.datetime] = Field(default_factory=dict)
    capabilities: dict[str, bool]
    notices: list[str] = Field(default_factory=list)


class CreateResearchResponse(ApiModel):
    run: ResearchRun
    poll_url: str
    poll_after_ms: int = 800


class ResearchRunDetailResponse(ApiModel):
    run: ResearchRun
    steps: list[AgentStep]
    tool_calls: list[ToolCallRecord]
    source_status: list[DataSourceStatus]
    evidence: list[EvidenceItem]
    brief: ResearchBrief | None = None
    brief_payload_hash: str | None = None
    is_active: bool
    poll_after_ms: int | None = None


class RunListResponse(ApiModel):
    runs: list[ResearchRun]


class RunSearchResult(ApiModel):
    run: ResearchRun
    # Where the best match is: the ticker, the company name, the question or a kept finding.
    matched: Literal["ticker", "company", "question", "finding"]
    # Part of a finding containing search words the ticker, company and question don't contain.
    snippet: str | None = None


class RunSearchResponse(ApiModel):
    query: str
    results: list[RunSearchResult]


class BriefListResponse(ApiModel):
    briefs: list[SavedBriefSummary]


class SavedBriefResponse(ApiModel):
    saved: SavedBrief


class WatchlistResponse(ApiModel):
    items: list[WatchlistItem]


class ProposeActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    run_id: str | None = Field(default=None, pattern=r"^run_[0-9a-f]{16}$")
    symbol: str | None = Field(default=None, min_length=1, max_length=16)
    title: str | None = Field(default=None, min_length=3, max_length=200)


class WatchlistProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)


class PendingActionResponse(ApiModel):
    action: PendingAction
    requires_confirmation: bool = True


class ConfirmActionResponse(ApiModel):
    action: PendingAction
    result: dict[str, Any]


class VoiceSessionResponse(ApiModel):
    """Connection details for one voice session (token is room-scoped and short-lived)."""

    server_url: str
    room_name: str
    participant_identity: str
    participant_token: str
    expires_at: dt.datetime
    agent_name: str


class VoiceSessionState(ApiModel):
    room_name: str
    expires_at: dt.datetime
    ended: bool
    active_run_id: str | None = None
    active_run_status: str | None = None


class ComparePeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: dt.date
    end_date: dt.date


class ComparePeriodsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    periods: list[ComparePeriod] = Field(min_length=2, max_length=2)


class PeriodChange(ApiModel):
    label: str
    calculation: PriceChangeCalculation


class ComparePeriodsResponse(ApiModel):
    """Two deterministic price-change calculations with the observation dates actually used."""

    symbol: str
    company_name: str | None = None
    periods: list[PeriodChange]
    difference_percentage_points: float | None = None
    difference_note: str
    is_fixture: bool
    evidence: list[EvidenceItem]


class SymbolSearchResponse(ApiModel):
    """Ticker suggestions for the research bar (symbol or company-name prefix, covered symbols only)."""

    query: str
    matches: list[SymbolMatch]
    is_fixture: bool


class AnalyticsEventRequest(BaseModel):
    """Events the browser may report; everything else is recorded server-side."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["voice_connection_succeeded", "voice_connection_failed"]
    reason: (
        Literal["permission_denied", "unreachable", "agent_failed", "agent_left", "rate_limited", "other"] | None
    ) = None


class RunAnalytics(ApiModel):
    started: int
    finished: int
    by_status: dict[str, int]
    completion_rate: float | None = None
    partial_rate: float | None = None
    median_duration_ms: float | None = None
    p95_duration_ms: float | None = None


class VoiceAnalytics(ApiModel):
    sessions_created: int
    connections_succeeded: int
    connections_failed: int
    failure_reasons: dict[str, int]


class WorkspaceAnalytics(ApiModel):
    briefs_saved: int
    watchlist_changes: dict[str, int]


class AnalyticsSummary(ApiModel):
    """Aggregate local analytics: counts and rates only, no personal data or free text."""

    event_counts: dict[str, int]
    runs: RunAnalytics
    categories: dict[str, dict[str, int]]
    voice: VoiceAnalytics
    workspace: WorkspaceAnalytics
    periods_compared: int
