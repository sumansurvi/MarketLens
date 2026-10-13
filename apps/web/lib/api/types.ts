// Convenience aliases over the types generated from the FastAPI OpenAPI schema
// (`pnpm gen:api`). Never hand-edit schema.d.ts; regenerate it when the API changes.
import type { components } from "./schema";

type S = components["schemas"];

export type Health = S["HealthResponse"];
export type DataStatus = S["DataStatusResponse"];
export type ServerStatus = S["ServerStatusData"];
export type ResearchRequest = S["ResearchRequest"];
export type CreateResearchResponse = S["CreateResearchResponse"];
export type ResearchRun = S["ResearchRun"];
export type RunStatus = S["RunStatus"];
export type RunDetail = S["ResearchRunDetailResponse"];
export type RunList = S["RunListResponse"];
export type RunSearch = S["RunSearchResponse"];
export type RunSearchResult = S["RunSearchResult"];
export type SymbolSearch = S["SymbolSearchResponse"];
export type SymbolMatch = S["SymbolMatch"];
export type ResearchBrief = S["ResearchBrief"];
export type ResearchClaim = S["ResearchClaim"];
export type ClaimType = S["ClaimType"];
export type BriefSection = S["BriefSection"];
export type EvidenceItem = S["EvidenceItem"];
export type EvidenceKind = S["EvidenceKind"];
export type KeyMetric = S["KeyMetric"];
export type EventEntry = S["EventEntry"];
export type GapItem = S["GapItem"];
export type SourceRef = S["SourceRef"];
export type AgentStep = S["AgentStep"];
export type ToolCallRecord = S["ToolCallRecord"];
export type DataSourceStatus = S["DataSourceStatus"];
export type WatchlistItem = S["WatchlistItem"];
export type WatchlistResponse = S["WatchlistResponse"];
export type SavedBriefSummary = S["SavedBriefSummary"];
export type BriefListResponse = S["BriefListResponse"];
export type ApiErrorBody = S["ApiError"];
export type ErrorResponse = S["ErrorResponse"];
export type PendingAction = S["PendingAction"];
export type PendingActionResponse = S["PendingActionResponse"];
export type ConfirmActionResponse = S["ConfirmActionResponse"];
export type SavedBrief = S["SavedBrief"];
export type SavedBriefResponse = S["SavedBriefResponse"];
export type McpServerStatus = S["McpServerStatus"];
export type VoiceSessionResponse = S["VoiceSessionResponse"];
export type VoiceSessionState = S["VoiceSessionState"];
export type ComparePeriodsResponse = S["ComparePeriodsResponse"];
export type PeriodChange = S["PeriodChange"];
export type AnalyticsSummary = S["AnalyticsSummary"];
