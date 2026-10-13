"""Deterministic claim validation — the final integrity gate for every brief.

Rules (VOICE_AND_AGENTS §2.6, EVALUATION §5.3/§7):
- every claim except a scenario must cite evidence, and every cited ID must exist in the run;
- ``source_fact`` claims need non-calculation evidence; ``calculation`` claims need calculation
  evidence (which records its formula and inputs);
- every number in the text must be traceable to a value in the cited evidence (within the
  rounding implied by its displayed precision); every ISO date must appear in cited evidence;
- causal wording about prices needs hedging, and advice/trading language is never allowed;
- instruction-like text (an echo of injected source content) is never allowed;
- scenarios must state assumptions.
Failing claims are withheld (moved out of the report) with the reason recorded. A draft that
failed a policy check is withheld with its text redacted, so the product never displays
advice or injected instructions, even struck through.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from marketlens_core.models import ClaimType, EvidenceItem, EvidenceKind, ResearchClaim, ValidationStatus
from marketlens_core.policy import PROHIBITED_OUTPUT_PATTERNS
from marketlens_core.untrusted import INJECTION_FLAG, detect_injection

REDACTED_TEXT = "[Draft text not shown: it contained advice, trading or instruction-like language.]"

_SCALE = {
    "billion": Decimal(10) ** 9,
    "b": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "million": Decimal(10) ** 6,
    "m": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "thousand": Decimal(10) ** 3,
    "k": Decimal(10) ** 3,
}
_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?\$?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suffix>\s?%|\s?(?:billion|million|thousand)\b|(?:bn|mn|[BMK])\b)?",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_IGNORED_TOKENS = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b(?:FY|CY)\s?\d{2,4}\b|\bQ[1-4]\b|\b(?:10-K|10-Q|8-K|20-F|6-K)(?:/A)?\b|"
    r"\bus-gaap:\w+|\bev_\w+|\bItem\s+\d+[A-Z]?\b|\b(?:19|20)\d{2}\b(?!-)",
    re.IGNORECASE,
)
_CAUSAL = re.compile(
    r"\b(?:because of|caused|causing|due to|drove|driven by|led to|resulted in|as a result of|triggered|sent shares)\b",
    re.IGNORECASE,
)
_HEDGE = re.compile(
    r"\b(?:may|might|could|possibly|not established|does not establish|cannot be attributed|coincid\w*|"
    r"uncertain|unclear|not proven|timing alone)\b",
    re.IGNORECASE,
)
_PROHIBITED = [re.compile(p, re.IGNORECASE) for p in PROHIBITED_OUTPUT_PATTERNS]
_COMPARISON = re.compile(
    r"\b(?:vs\.?|versus|compared|than|from|grew|growth|increase[ds]?|decrease[ds]?|declined|rose|fell|up|down)\b",
    re.IGNORECASE,
)


@dataclass
class ClaimCheck:
    status: ValidationStatus
    notes: list[str] = field(default_factory=list)
    numbers_checked: int = 0
    numbers_failed: int = 0
    redact: bool = False

    @property
    def passed(self) -> bool:
        return self.status in (ValidationStatus.SUPPORTED, ValidationStatus.NEEDS_QUALIFICATION)


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _walk_numbers(value: Any, sink: list[Decimal]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, Decimal)):
        number = _decimal(str(value))
        if number is not None:
            sink.append(number)
    elif isinstance(value, str):
        stripped = value.strip().rstrip("%")
        if len(stripped) <= 32:
            number = _decimal(stripped)
            if number is not None:
                sink.append(number)
                return
        for match in _NUMBER.finditer(_IGNORED_TOKENS.sub(" ", value)):
            number = _decimal(match.group("num"))
            if number is not None:
                sink.append(number)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_numbers(item, sink)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_numbers(item, sink)


def evidence_numbers(items: Iterable[EvidenceItem]) -> list[Decimal]:
    pool: list[Decimal] = []
    items = list(items)
    for item in items:
        _walk_numbers(item.payload, pool)
        _walk_numbers(item.title or "", pool)
        _walk_numbers(item.excerpt or "", pool)
        if item.freshness and item.freshness.age_days is not None:
            pool.append(Decimal(item.freshness.age_days))
    pool.append(Decimal(len(items)))
    return pool


def evidence_dates(items: Iterable[EvidenceItem]) -> set[str]:
    dates: set[str] = set()
    for item in items:
        for value in (item.published_date, item.period_start, item.period_end):
            if value:
                dates.add(value.isoformat())
        if item.published_at:
            dates.add(item.published_at[:10])
        blob = f"{item.title or ''} {item.excerpt or ''} {item.payload}"
        dates.update(_ISO_DATE.findall(blob))
    return dates


def extract_claim_numbers(text: str) -> list[tuple[Decimal, int, Decimal, int]]:
    """(magnitude, decimal places, scale, sign) per number needing grounding.

    ``sign`` is +1/-1 when the text shows an explicit sign, else 0 (direction words such
    as "fell" carry the sign, so only the magnitude is compared)."""
    cleaned = _IGNORED_TOKENS.sub(" ", text)
    found = []
    for match in _NUMBER.finditer(cleaned):
        raw = match.group("num")
        number = _decimal(raw)
        if number is None:
            continue
        places = len(raw.split(".")[1]) if "." in raw else 0
        suffix = (match.group("suffix") or "").strip().lower()
        scale = _SCALE.get(suffix, Decimal(1))
        sign_text = match.group("sign")
        sign = 0 if not sign_text else (1 if sign_text == "+" else -1)
        found.append((number, places, scale, sign))
    return found


def number_grounded(value: Decimal, places: int, scale: Decimal, pool: list[Decimal], sign: int = 0) -> bool:
    tolerance = Decimal("0.5") * Decimal(10) ** (-places) * scale + Decimal("1e-9")
    target = value * scale
    for candidate in pool:
        if sign > 0 and candidate < 0 or sign < 0 and candidate > 0:
            continue
        if abs(abs(candidate) - target) <= tolerance:
            return True
    return False


def validate_claim(
    claim: ResearchClaim,
    ledger: dict[str, EvidenceItem],
    *,
    window: tuple[dt.date, dt.date] | None = None,
) -> ClaimCheck:
    notes: list[str] = []
    for pattern in _PROHIBITED:
        if pattern.search(claim.text):
            return ClaimCheck(
                ValidationStatus.UNSUPPORTED,
                ["Contains advice or trading language, which is not allowed."],
                redact=True,
            )
    if detect_injection(claim.text):
        return ClaimCheck(
            ValidationStatus.UNSUPPORTED,
            ["Contains instruction-like text; retrieved content is data and is never repeated as instructions."],
            redact=True,
        )
    if claim.claim_type == ClaimType.SCENARIO:
        if not claim.assumptions:
            return ClaimCheck(ValidationStatus.UNSUPPORTED, ["Scenario has no stated assumptions."])
    elif not claim.evidence_ids:
        return ClaimCheck(ValidationStatus.UNSUPPORTED, ["No evidence IDs are cited."])
    unknown = [e for e in claim.evidence_ids if e not in ledger]
    if unknown:
        return ClaimCheck(ValidationStatus.UNSUPPORTED, [f"Cites unknown evidence IDs: {', '.join(unknown[:4])}."])
    cited = [ledger[e] for e in claim.evidence_ids]
    kinds = {e.kind for e in cited}
    if claim.claim_type == ClaimType.SOURCE_FACT and kinds <= {EvidenceKind.CALCULATION}:
        return ClaimCheck(ValidationStatus.UNSUPPORTED, ["A source fact must cite retrieved source evidence."])
    if claim.claim_type == ClaimType.CALCULATION and EvidenceKind.CALCULATION not in kinds:
        return ClaimCheck(ValidationStatus.UNSUPPORTED, ["A calculation must cite a deterministic calculation record."])
    # Quarterly and annual values must not be compared as equivalent periods.
    period_types = {e.payload.get("period_type") for e in cited if e.kind == EvidenceKind.FINANCIAL_METRIC} - {None}
    if len(period_types) > 1 and _COMPARISON.search(claim.text):
        return ClaimCheck(
            ValidationStatus.UNSUPPORTED, ["Compares quarterly and annual periods as if they were equivalent."]
        )
    # Numbers
    pool = evidence_numbers(cited)
    numbers = extract_claim_numbers(claim.text)
    failed = [n for n in numbers if not number_grounded(n[0], n[1], n[2], pool, n[3])]
    if failed:
        shown = ", ".join(str(v) for v, *_ in failed[:3])
        return ClaimCheck(
            ValidationStatus.NUMERIC_MISMATCH,
            [f"Numbers not found in the cited evidence: {shown}."],
            numbers_checked=len(numbers),
            numbers_failed=len(failed),
        )
    # Dates
    allowed_dates = evidence_dates(cited)
    stray = [d for d in _ISO_DATE.findall(claim.text) if d not in allowed_dates]
    if stray:
        return ClaimCheck(
            ValidationStatus.DATE_MISMATCH,
            [f"Dates not present in the cited evidence: {', '.join(stray[:3])}."],
            numbers_checked=len(numbers),
        )
    if window is not None:
        start, end = window
        late_news = [
            e
            for e in cited
            if e.kind == EvidenceKind.NEWS and e.published_date and not (start <= e.published_date <= end)
        ]
        if late_news and claim.claim_type != ClaimType.SCENARIO:
            return ClaimCheck(
                ValidationStatus.DATE_MISMATCH,
                ["Cites news published outside the requested window as if it were in-window."],
                numbers_checked=len(numbers),
            )
    status = ValidationStatus.SUPPORTED
    if _CAUSAL.search(claim.text) and not _HEDGE.search(claim.text):
        return ClaimCheck(
            ValidationStatus.UNSUPPORTED,
            ["States a causal link that the evidence does not establish (timing alone is not causation)."],
            numbers_checked=len(numbers),
        )
    if (
        claim.claim_type == ClaimType.INTERPRETATION
        and not _HEDGE.search(claim.text)
        and any(e.kind == EvidenceKind.NEWS for e in cited)
    ):
        status = ValidationStatus.NEEDS_QUALIFICATION
        notes.append("Interpretation drawing on news should state its uncertainty.")
    flagged = [e for e in cited if INJECTION_FLAG in e.flags]
    if flagged and claim.claim_type != ClaimType.SOURCE_FACT:
        notes.append("Cites a source flagged for instruction-like content; treated as untrusted data.")
    return ClaimCheck(status, notes, numbers_checked=len(numbers))


@dataclass
class ValidationReport:
    kept: list[ResearchClaim]
    withheld: list[ResearchClaim]
    numbers_checked: int
    numbers_failed: int


# Models sometimes write internal references into the sentence ("(evidence_id: ev_calc_…)",
# "us-gaap:Revenues"). The claim's evidence_ids field already carries the link, so the text is
# cleaned before it is checked and stored: no internal IDs or tags reach the API or the screen (D-86).
_ID_REFERENCE = re.compile(
    r"\s*[(\[]\s*(?:evidence[_ ]ids?\s*[:=]\s*)?(?:ev_[a-z_]+_[0-9a-f]{12}[\s,;]*(?:and\s+)?)+[)\]]"
    r"|\bev_[a-z_]+_[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_TAG_REFERENCE = re.compile(
    r"\s*\([^()]*\b(?:us-gaap|ifrs-full|dei):[^()]*\)"
    r"|\s*[—–-]\s*(?:us-gaap|ifrs-full|dei):[A-Za-z]+(?:\s*[—–-](?=\s))?"
    r"|\b(?:us-gaap|ifrs-full|dei):[A-Za-z]+\b"
)
_EMPTY_LABEL = re.compile(r"\s*\(?\s*evidence[_ ]ids?\s*[:=]?\s*\)?(?=[\s.,;:]|$)", re.IGNORECASE)


def clean_claim_text(text: str) -> str:
    cleaned = _EMPTY_LABEL.sub("", _TAG_REFERENCE.sub("", _ID_REFERENCE.sub("", text)))
    cleaned = re.sub(r"\s{2,}", " ", re.sub(r"\s+([.,;:!?])", r"\1", cleaned)).strip()
    return cleaned or text


def validate_claims(
    claims: list[ResearchClaim],
    ledger: dict[str, EvidenceItem],
    *,
    window: tuple[dt.date, dt.date] | None = None,
) -> ValidationReport:
    kept: list[ResearchClaim] = []
    withheld: list[ResearchClaim] = []
    checked = failed = 0
    for claim in claims:
        claim = claim.model_copy(update={"text": clean_claim_text(claim.text)})
        result = validate_claim(claim, ledger, window=window)
        checked += result.numbers_checked
        failed += result.numbers_failed
        update: dict[str, Any] = {"validation_status": result.status, "validation_notes": result.notes}
        if result.redact:
            update["text"] = REDACTED_TEXT
        updated = claim.model_copy(update=update)
        (kept if result.passed else withheld).append(updated)
    return ValidationReport(kept, withheld, checked, failed)


def text_is_grounded(text: str, items: list[EvidenceItem], *, also: str = "") -> bool:
    """True when every number and ISO date in model-written text appears in ``items`` (or in ``also``).

    Used for reviewer explanations, which may quote the claim under review but must not
    introduce values of their own."""
    pool = evidence_numbers(items)
    pool += [value * scale for value, _, scale, _ in extract_claim_numbers(also)]
    if not all(number_grounded(v, places, scale, pool) for v, places, scale, _ in extract_claim_numbers(text)):
        return False
    allowed = evidence_dates(items) | set(_ISO_DATE.findall(also))
    return all(d in allowed for d in _ISO_DATE.findall(text))
