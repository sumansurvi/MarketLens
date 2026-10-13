"""Deterministic responders for the fixture LLM (no network, no model).

Each responder returns a known payload for its task: the specialists and supervisor return
the deterministic composer's findings for the same evidence the real model would receive;
the reviewer mirrors the deterministic validator. Every brief built this way is labeled
``llm_mode: fixture``. Evaluation cases attach a fault hook to corrupt or fail a task so the
validators can be shown to catch bad model output.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from marketlens_core.models import EvidenceItem, ResearchClaim
from pydantic import BaseModel

from app.agents.contracts import (
    ClaimVerdict,
    ContradictionNote,
    FindingDraft,
    ReviewOutput,
    SpecialistOutput,
    SynthesisClaimDraft,
    SynthesisOutput,
)
from app.services.brief_composer import BriefComposer
from app.services.validation import validate_claim


@dataclass
class FixtureContext:
    """Deterministic context the fixture LLM may use; never sent to a real model."""

    composer: BriefComposer | None = None
    claims: list[ResearchClaim] = field(default_factory=list)
    ledger: dict[str, EvidenceItem] = field(default_factory=dict)
    window: tuple[dt.date, dt.date] | None = None
    contradictions: list[dict[str, Any]] = field(default_factory=list)


def _draft(claim: ResearchClaim) -> FindingDraft:
    return FindingDraft(
        section=claim.section.value,  # type: ignore[arg-type]
        claim_type=claim.claim_type.value,  # type: ignore[arg-type]
        text=claim.text,
        evidence_ids=list(claim.evidence_ids),
        limitations=[claim.confidence_note] if claim.confidence_note else [],
        assumptions=list(claim.assumptions),
    )


def _specialist(name: str, claims: list[ResearchClaim], missing: list[str]) -> SpecialistOutput:
    return SpecialistOutput(
        specialist=name,  # type: ignore[arg-type]
        status="completed" if claims else "partial",
        summary=f"{len(claims)} finding(s) drawn only from the provided evidence records.",
        findings=[_draft(c) for c in claims],
        missing_evidence=missing if not claims else [],
        warnings=[],
    )


def market_specialist(payload: dict[str, Any], ctx: FixtureContext) -> BaseModel:
    assert ctx.composer is not None
    return _specialist(
        "market_data", ctx.composer.market_findings(), ["Market data for the research window was not available."]
    )


def news_specialist(payload: dict[str, Any], ctx: FixtureContext) -> BaseModel:
    assert ctx.composer is not None
    return _specialist(
        "news_events", ctx.composer.news_findings(), ["Dated news for the research window was not available."]
    )


def fundamentals_specialist(payload: dict[str, Any], ctx: FixtureContext) -> BaseModel:
    assert ctx.composer is not None
    composer = ctx.composer
    claims = composer.fundamentals_findings() + composer.filings_findings() + composer.risk_quotes()
    return _specialist("fundamentals", claims, ["Company fundamentals and filings were not available."])


def supervisor_synthesis(payload: dict[str, Any], ctx: FixtureContext) -> BaseModel:
    assert ctx.composer is not None
    claims = ctx.composer.synthesis() + ctx.composer.scenarios()
    return SynthesisOutput(
        claims=[
            SynthesisClaimDraft(
                section=c.section.value,  # type: ignore[arg-type]
                claim_type=c.claim_type.value,  # type: ignore[arg-type]
                text=c.text,
                evidence_ids=list(c.evidence_ids),
                assumptions=list(c.assumptions),
            )
            for c in claims
        ],
        unknowns=[],
    )


_VERDICTS = {
    "supported": "supported",
    "needs_qualification": "needs_qualification",
    "unsupported": "unsupported",
    "date_mismatch": "date_mismatch",
    "numeric_mismatch": "numeric_mismatch",
    "contradicted": "contradicted",
    "unverified": "unsupported",
}


def evidence_reviewer(payload: dict[str, Any], ctx: FixtureContext) -> BaseModel:
    verdicts = []
    for claim in ctx.claims:
        check = validate_claim(claim, ctx.ledger, window=ctx.window)
        verdicts.append(
            ClaimVerdict(
                claim_id=claim.claim_id,
                verdict=_VERDICTS[check.status.value],  # type: ignore[arg-type]
                reason="; ".join(check.notes) or "Claim matches its cited evidence.",
            )
        )
    missing = [
        s["category"] for s in payload.get("category_status", []) if s.get("status") in ("unavailable", "failed")
    ]
    return ReviewOutput(
        verdicts=verdicts,
        contradictions=[
            ContradictionNote(description=c["description"], evidence_ids=list(c["evidence_ids"]))
            for c in ctx.contradictions
        ],
        missing_categories=missing,
        notes=[],
    )


RESPONDERS = {
    "market_specialist": market_specialist,
    "news_specialist": news_specialist,
    "fundamentals_specialist": fundamentals_specialist,
    "supervisor_synthesis": supervisor_synthesis,
    "evidence_reviewer": evidence_reviewer,
}
