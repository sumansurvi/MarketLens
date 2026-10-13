"""Research Supervisor: bounded plan, specialist coordination results and final synthesis.

The plan is deterministic (categories, horizon, budgets). The supervisor's LLM step only
synthesizes *validated* specialist findings; claim IDs, materiality and section placement
are assigned here in code, never by a model.
"""

from __future__ import annotations

from typing import Any

from marketlens_core.config import Settings
from marketlens_core.models import BriefSection, ClaimType, ResearchCategory, ResearchClaim, ResearchRun

from app.agents.contracts import MAX_SYNTHESIS_CLAIMS, FindingDraft, SynthesisClaimDraft, SynthesisOutput
from app.agents.fixture_responses import FixtureContext, supervisor_synthesis
from app.agents.prompts import SYNTHESIS_SYSTEM, synthesis_package
from app.agents.runtime import AgentRuntime
from app.services.evidence_collector import EvidenceBundle

SYNTHESIS_TASK = "supervisor_synthesis"
AGENT = "research_supervisor"
SYNTHESIS_SECTIONS = frozenset(
    {
        BriefSection.EXECUTIVE_SUMMARY,
        BriefSection.POSITIVE_EVIDENCE,
        BriefSection.NEGATIVE_EVIDENCE,
        BriefSection.RISKS,
        BriefSection.INTERPRETATION,
        BriefSection.SCENARIOS,
    }
)
_MATERIAL_SECTIONS = {
    BriefSection.EXECUTIVE_SUMMARY,
    BriefSection.MARKET_MOVEMENT,
    BriefSection.FUNDAMENTALS,
    BriefSection.FILINGS,
    BriefSection.POSITIVE_EVIDENCE,
    BriefSection.NEGATIVE_EVIDENCE,
}


def clip_text(text: str, limit: int) -> str:
    """Shorten to ``limit`` characters at the last full sentence, else the last whole word with "…",
    so a statement never ends mid-word (D-86)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit // 2:
        return cut[: end + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut[: limit - 1]).rstrip(",;:—- ") + "…"


def build_plan(run: ResearchRun, settings: Settings, llm_mode: str) -> dict[str, Any]:
    horizon = run.time_horizon
    assumptions = []
    if horizon.is_default:
        assumptions.append(
            f"No horizon was given, so the default {horizon.label.split(' (')[0].lower()} window is used."
        )
    return {
        "categories": [c.value for c in ResearchCategory],
        "mandatory": ["market", "fundamentals", "filings"],
        "optional": ["news"],
        "horizon": horizon.model_dump(mode="json"),
        "assumptions": assumptions,
        "specialists": ["market_data_specialist", "news_events_specialist", "fundamentals_specialist"],
        "reviewer": "evidence_risk_reviewer",
        "llm_mode": llm_mode,
        "budget": {
            "max_provider_calls": settings.max_provider_calls,
            "max_research_steps": settings.max_research_steps,
            "max_llm_calls": settings.max_llm_calls_per_run,
            "max_parallel_specialists": settings.max_parallel_specialists,
        },
    }


class ClaimAllocator:
    """Deterministic claim IDs per section (``cl_mark_1``…)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, section: BriefSection) -> str:
        prefix = section.value[:4]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"cl_{prefix}_{self._counters[prefix]}"


def materiality(section: BriefSection, claim_type: ClaimType) -> str:
    material = section in _MATERIAL_SECTIONS and claim_type in (ClaimType.SOURCE_FACT, ClaimType.CALCULATION)
    return "material" if material else "supporting"


def drafts_to_claims(
    drafts: list[FindingDraft] | list[SynthesisClaimDraft],
    *,
    allowed: frozenset[BriefSection],
    produced_by: str,
    allocator: ClaimAllocator,
) -> tuple[list[ResearchClaim], list[str]]:
    """Convert model drafts to claims; drafts outside the agent's scope are dropped and noted."""
    claims: list[ResearchClaim] = []
    dropped: list[str] = []
    for draft in drafts:
        section = BriefSection(draft.section)
        if section not in allowed:
            dropped.append(f"{produced_by} wrote outside its scope ({section.value}); the draft was discarded.")
            continue
        claim_type = ClaimType(draft.claim_type)
        limitations = getattr(draft, "limitations", []) or []
        claims.append(
            ResearchClaim(
                claim_id=allocator.next(section),
                section=section,
                text=clip_text(draft.text, 600),
                claim_type=claim_type,
                evidence_ids=list(dict.fromkeys(draft.evidence_ids))[:12],
                materiality=materiality(section, claim_type),  # type: ignore[arg-type]
                confidence_note=("; ".join(limitations)[:240] or None),
                assumptions=[a[:240] for a in draft.assumptions][:5],
                produced_by=produced_by,
            )
        )
    return claims, dropped


async def synthesize(
    runtime: AgentRuntime,
    bundle: EvidenceBundle,
    question: str,
    findings: list[ResearchClaim],
    contradictions: list[dict[str, Any]],
    ctx: FixtureContext,
) -> tuple[SynthesisOutput, int]:
    """The synthesis draft and how many claims beyond the limit were discarded."""
    output = await runtime.run(
        task=SYNTHESIS_TASK,
        system_prompt=SYNTHESIS_SYSTEM,
        payload=synthesis_package(bundle, question, findings, contradictions),
        output_model=SynthesisOutput,
        fixture_context=ctx,
    )
    dropped = max(0, len(output.claims) - MAX_SYNTHESIS_CLAIMS)
    output.claims = output.claims[:MAX_SYNTHESIS_CLAIMS]
    return output, dropped


def fallback_synthesis(ctx: FixtureContext) -> SynthesisOutput:
    """Deterministic synthesis used only if the model step fails (clearly labeled)."""
    return supervisor_synthesis({}, ctx)  # type: ignore[return-value]
