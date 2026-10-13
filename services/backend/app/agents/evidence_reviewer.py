"""Evidence & Risk Reviewer: per-claim verdicts on the supervisor's draft.

The reviewer cannot add evidence. Its verdicts are combined with the deterministic
validator and the *stricter* outcome always wins: a claim failing deterministic checks is
withheld even if the model calls it supported; a model rejection withholds a claim that
passed deterministic checks. If the reviewer cannot complete, the report cannot be marked
completed. Reviewer free text is shown only when it is policy-clean and every number or date
in it is grounded in the cited evidence (or quotes the claim under review).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from marketlens_core.models import EvidenceItem, ResearchClaim, ValidationStatus
from marketlens_core.policy import PROHIBITED_OUTPUT_PATTERNS
from marketlens_core.untrusted import detect_injection

from app.agents.contracts import MAX_NOTES, ReviewOutput
from app.agents.fixture_responses import FixtureContext
from app.agents.prompts import REVIEW_SYSTEM, review_package
from app.agents.runtime import AgentRuntime
from app.services.evidence_collector import EvidenceBundle
from app.services.validation import ValidationReport, text_is_grounded

REVIEW_TASK = "evidence_reviewer"
AGENT = "evidence_risk_reviewer"
_WITHHOLD = {"unsupported", "date_mismatch", "numeric_mismatch", "contradicted"}
_PROHIBITED = [re.compile(p, re.IGNORECASE) for p in PROHIBITED_OUTPUT_PATTERNS]


async def review(
    runtime: AgentRuntime,
    bundle: EvidenceBundle,
    claims: list[ResearchClaim],
    report: ValidationReport,
    contradictions: list[dict[str, Any]],
    ctx: FixtureContext,
) -> ReviewOutput:
    checks = {
        "numbers_checked": report.numbers_checked,
        "numbers_failed": report.numbers_failed,
        "withheld_by_deterministic_checks": [c.claim_id for c in report.withheld],
    }
    return await runtime.run(
        task=REVIEW_TASK,
        system_prompt=REVIEW_SYSTEM,
        payload=review_package(bundle, claims, checks, contradictions),
        output_model=ReviewOutput,
        fixture_context=ctx,
        retries=1,
    )


def safe_note(text: str, *, allow_digits: bool = True) -> str | None:
    """Model-written free text shown to users: bounded, no advice, no instruction echoes."""
    cleaned = " ".join(text.split())[:300]
    if not cleaned or any(p.search(cleaned) for p in _PROHIBITED) or detect_injection(cleaned):
        return None
    if not allow_digits and re.search(r"\d", cleaned):
        return None
    return cleaned


@dataclass
class ReviewResult:
    kept: list[ResearchClaim]
    withheld: list[ResearchClaim]
    complete: bool
    reviewer_contradictions: list[dict[str, Any]] = field(default_factory=list)
    missing_categories: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def apply_review(
    report: ValidationReport,
    output: ReviewOutput | None,
    contradictions: list[dict[str, Any]],
    ledger: dict[str, EvidenceItem],
) -> ReviewResult:
    kept: list[ResearchClaim] = []
    withheld = list(report.withheld)
    verdicts = {v.claim_id: v for v in (output.verdicts if output else [])}
    contradicted_ids: dict[str, str] = {}
    for c in contradictions:
        if c.get("severity") == "material":
            for evidence_id in c["evidence_ids"]:
                contradicted_ids.setdefault(evidence_id, c["description"])
    unreviewed_material = 0
    for claim in report.kept:
        notes = list(claim.validation_notes)
        status = claim.validation_status
        verdict = verdicts.get(claim.claim_id)
        if verdict is None:
            if output is not None and claim.materiality == "material":
                unreviewed_material += 1
            notes.append("Not individually reviewed by the evidence reviewer.")
        else:
            reason = safe_note(verdict.reason)
            cited = [ledger[e] for e in claim.evidence_ids if e in ledger]
            if reason is None:
                reason = "No usable reason given."
            elif not text_is_grounded(reason, cited, also=claim.text):
                reason = "Explanation not shown because it cited values that are not in the evidence."
            if verdict.verdict in _WITHHOLD:
                withheld.append(
                    claim.model_copy(
                        update={
                            "validation_status": ValidationStatus(verdict.verdict),
                            "validation_notes": [*notes, f"Evidence reviewer: {reason}"],
                        }
                    )
                )
                continue
            if verdict.verdict == "needs_qualification":
                status = ValidationStatus.NEEDS_QUALIFICATION
                notes.append(f"Evidence reviewer: {reason}")
        conflict = next((contradicted_ids[e] for e in claim.evidence_ids if e in contradicted_ids), None)
        if conflict:
            status = ValidationStatus.NEEDS_QUALIFICATION
            notes.append(f"Sources disagree: {conflict}")
        kept.append(claim.model_copy(update={"validation_status": status, "validation_notes": notes}))
    reviewer_contradictions = []
    missing: list[str] = []
    review_notes: list[str] = []
    if output is not None:
        for note in output.contradictions[:MAX_NOTES]:
            ids = list(dict.fromkeys(e for e in note.evidence_ids if e in ledger))
            description = safe_note(note.description)
            if ids and description and text_is_grounded(description, [ledger[e] for e in ids]):
                reviewer_contradictions.append(
                    {"description": description, "evidence_ids": ids, "severity": "info", "kind": "reviewer"}
                )
        missing = [m for m in (safe_note(x, allow_digits=False) for x in output.missing_categories[:MAX_NOTES]) if m]
        review_notes = [n for n in (safe_note(x, allow_digits=False) for x in output.notes[:MAX_NOTES]) if n]
    complete = output is not None and unreviewed_material == 0
    return ReviewResult(kept, withheld, complete, reviewer_contradictions, missing, review_notes)
