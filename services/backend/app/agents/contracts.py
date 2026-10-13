"""Typed input/output contracts between the workflow and its LLM-backed agents.

These models are sent as OpenAI strict structured-output schemas, so every field is
required (no defaults, no length/pattern constraints); bounds are enforced in code after
parsing. Agents return *drafts*: claim IDs, validation status and final placement are
assigned by deterministic code, never by a model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ClaimTypeLiteral = Literal["source_fact", "calculation", "interpretation", "scenario"]
SpecialistName = Literal["market_data", "news_events", "fundamentals"]
SpecialistSection = Literal["market_movement", "recent_events", "interpretation", "fundamentals", "filings", "risks"]
SynthesisSection = Literal[
    "executive_summary", "positive_evidence", "negative_evidence", "risks", "interpretation", "scenarios"
]
VerdictLiteral = Literal[
    "supported", "needs_qualification", "unsupported", "date_mismatch", "numeric_mismatch", "contradicted"
]

MAX_FINDINGS_PER_SPECIALIST = 16
MAX_SYNTHESIS_CLAIMS = 16
MAX_NOTES = 8


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingDraft(StrictModel):
    """One candidate claim. Every number must come from the cited evidence."""

    section: SpecialistSection
    claim_type: ClaimTypeLiteral
    text: str
    evidence_ids: list[str]
    limitations: list[str]
    assumptions: list[str]


class SpecialistOutput(StrictModel):
    specialist: SpecialistName
    status: Literal["completed", "partial", "failed"]
    summary: str
    findings: list[FindingDraft]
    missing_evidence: list[str]
    warnings: list[str]


class SynthesisClaimDraft(StrictModel):
    section: SynthesisSection
    claim_type: ClaimTypeLiteral
    text: str
    evidence_ids: list[str]
    assumptions: list[str]


class SynthesisOutput(StrictModel):
    claims: list[SynthesisClaimDraft]
    unknowns: list[str]


class ClaimVerdict(StrictModel):
    claim_id: str
    verdict: VerdictLiteral
    reason: str


class ContradictionNote(StrictModel):
    description: str
    evidence_ids: list[str]


class ReviewOutput(StrictModel):
    verdicts: list[ClaimVerdict]
    contradictions: list[ContradictionNote]
    missing_categories: list[str]
    notes: list[str]
