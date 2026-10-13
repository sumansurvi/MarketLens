"""Deterministic, speakable views of a research run for the voice agent.

The voice model never summarizes the brief on its own: it receives sentences built here
from *validated* claims and run metadata, plus explicit caveats (partial status, synthetic
data, qualified claims). Text is lightly adapted for speech (ISO dates spoken as dates,
XBRL concept names and evidence identifiers removed); no number is added or changed.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from marketlens_core.models import (
    TERMINAL_RUN_STATUSES,
    AgentStep,
    BriefSection,
    ResearchBrief,
    ResearchClaim,
    ResearchRun,
    RunStatus,
    StepStatus,
)

Focus = Literal["overview", "market", "financials", "risks", "news", "unknowns"]
FOCUS_SECTIONS: dict[str, tuple[tuple[BriefSection, int], ...]] = {
    "overview": ((BriefSection.EXECUTIVE_SUMMARY, 3),),
    "market": ((BriefSection.MARKET_MOVEMENT, 3),),
    "financials": ((BriefSection.FUNDAMENTALS, 3), (BriefSection.FILINGS, 1)),
    "risks": ((BriefSection.RISKS, 2), (BriefSection.NEGATIVE_EVIDENCE, 1)),
    "news": ((BriefSection.RECENT_EVENTS, 1), (BriefSection.INTERPRETATION, 1)),
    "unknowns": (),
}
FOLLOW_UPS = ("risks", "financials", "market", "news", "unknowns")
STAGE_LABELS = {
    "created": "getting started",
    "validating": "checking the request",
    "collecting_evidence": "collecting market data, filings and news",
    "specialist_analysis": "analyzing the evidence",
    "synthesizing": "drafting the summary",
    "evidence_review": "checking every claim against its evidence",
    "finalizing": "finishing the report",
    "done": "done",
}
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TECHNICAL_PARENS = re.compile(r"\s*\([^()]*(?:us-gaap:|ifrs-full:|dei:|ev_)[^()]*\)")


def spoken_date(value: dt.date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def for_speech(text: str) -> str:
    """Adapt validated claim text for text-to-speech without changing any value."""
    cleaned = _TECHNICAL_PARENS.sub("", text)

    def _date(match: re.Match[str]) -> str:
        try:
            return spoken_date(dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            return match.group(0)

    cleaned = _ISO_DATE.sub(_date, cleaned)
    return " ".join(cleaned.split())


def _window(run: ResearchRun) -> str:
    horizon = run.time_horizon
    label = "the default three-month window" if horizon.is_default else "the requested window"
    return f"{spoken_date(horizon.start_date)} to {spoken_date(horizon.end_date)} ({label})"


def status_sentence(run: ResearchRun) -> str:
    name = run.company.company_name if run.company else run.symbol
    if run.status == RunStatus.COMPLETED:
        return f"The research report on {name} is complete."
    if run.status == RunStatus.PARTIAL:
        return f"The research report on {name} is partial, so some parts are missing or qualified."
    if run.status == RunStatus.FAILED:
        reason = run.errors[-1].message if run.errors else "an unexpected error"
        return f"The research on {name} failed: {reason} No report was produced."
    if run.status == RunStatus.CANCELLED:
        return f"The research on {name} was cancelled; no report was produced."
    return f"The research on {name} is still running."


def partial_reasons(run: ResearchRun, limit: int = 3) -> list[str]:
    reasons: list[str] = []
    for error in run.errors:
        text = error.message.rstrip(".") + "."
        if text not in reasons:
            reasons.append(text)
    return reasons[:limit]


def current_step(steps: list[AgentStep]) -> str | None:
    running = [s for s in steps if s.status == StepStatus.RUNNING]
    return running[-1].summary if running else None


def run_status_view(run: ResearchRun, steps: list[AgentStep], now: dt.datetime) -> dict[str, object]:
    terminal = run.status in TERMINAL_RUN_STATUSES
    started = run.started_at or run.created_at
    end = run.completed_at if terminal and run.completed_at else now
    return {
        "run_id": run.run_id,
        "symbol": run.symbol,
        "company_name": run.company.company_name if run.company else None,
        "status": run.status.value,
        "is_terminal": terminal,
        "stage": run.stage.value,
        "stage_label": STAGE_LABELS.get(run.stage.value, run.stage.value),
        "current_step": None if terminal else current_step(steps),
        "steps_completed": sum(1 for s in steps if s.status == StepStatus.COMPLETED),
        "elapsed_seconds": max(0, int((end - started).total_seconds())),
        "status_sentence": status_sentence(run),
        "partial_reasons": partial_reasons(run) if run.status == RunStatus.PARTIAL else [],
        "data_mode": run.data_mode.value,
    }


def _pick(claims: list[ResearchClaim], focus: str) -> list[ResearchClaim]:
    chosen: list[ResearchClaim] = []
    for section, limit in FOCUS_SECTIONS.get(focus, ()):
        chosen += [c for c in claims if c.section == section][:limit]
    return chosen


def brief_summary_view(run: ResearchRun, brief: ResearchBrief | None, focus: str) -> dict[str, object]:
    """Speakable summary for one focus area; only validated claims and recorded gaps are used."""
    base: dict[str, object] = {
        "run_id": run.run_id,
        "symbol": run.symbol,
        "status": run.status.value,
        "focus": focus,
        "status_sentence": status_sentence(run),
        "sentences": [],
        "caveats": [],
        "follow_up_topics": [t for t in FOLLOW_UPS if t != focus],
        "details_on_screen": "The full report with sources and citations is on screen.",
    }
    if brief is None or run.status not in (RunStatus.COMPLETED, RunStatus.PARTIAL):
        return base
    caveats: list[str] = []
    if brief.is_fixture:
        caveats.append("These figures come from synthetic demo data, not real market data.")
    if run.status == RunStatus.PARTIAL:
        caveats += [f"Partial because: {r}" for r in partial_reasons(run)]
    if focus == "unknowns":
        gaps = [g for g in brief.unknowns_and_gaps if g.severity == "material"][:3]
        sentences = [for_speech(g.description) for g in gaps] or ["No material gaps were recorded."]
    else:
        chosen = _pick(brief.claims, focus)
        sentences = [for_speech(c.text) for c in chosen]
        if any(c.validation_status.value == "needs_qualification" for c in chosen):
            caveats.append("Some of these points are qualified because sources disagree; see the notes on screen.")
        if focus == "news" and chosen:
            caveats.append("Dated news is context only; it does not establish why the stock moved.")
        if not sentences:
            sentences = [f"The report has no validated findings for {focus}."]
    base.update(
        {
            "window": _window(run),
            "is_fixture": brief.is_fixture,
            "sentences": sentences,
            "caveats": caveats,
        }
    )
    return base
