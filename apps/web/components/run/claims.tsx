"use client";

import type { EvidenceItem, ResearchClaim } from "@/lib/api/types";
import { naturalize } from "@/lib/natural";
import { sourceName } from "@/lib/readable";

import { Hint } from "../hint";
import { ClaimTypeBadge, cn } from "../ui";

const KIND_DOT: Record<string, string> = {
  quote: "bg-[#2a78d6]",
  price_series: "bg-[#2a78d6]",
  calculation: "bg-[#4a3aa7] dark:bg-[#9085e9]",
  financial_metric: "bg-[#1f8a70]",
  filing: "bg-ink-3",
  news: "bg-[#eda100]",
};

/** A short, distinct name for a piece of evidence ("Price change", "Revenue (quarter) · Jun 27, 2026"). */
export function evidenceLabel(item: EvidenceItem | undefined): string {
  return sourceName(item);
}

export function EvidenceChip({
  id,
  evidence,
  selected,
  onSelect,
}: {
  id: string;
  evidence: Map<string, EvidenceItem>;
  selected: boolean;
  onSelect: (id: string, trigger: HTMLElement) => void;
}) {
  const item = evidence.get(id);
  const flagged = item?.flags?.includes("suspected_prompt_injection");
  return (
    <button
      type="button"
      onClick={(e) => onSelect(id, e.currentTarget)}
      aria-pressed={selected}
      title={naturalize(item?.title) || sourceName(item)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition",
        selected ? "border-accent bg-accent-wash text-accent" : "border-line bg-raised text-ink-2 hover:border-accent hover:text-accent",
        flagged && "border-warning",
      )}
    >
      <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", KIND_DOT[item?.kind ?? ""] ?? "bg-ink-3")} />
      {evidenceLabel(item)}
      <span className="sr-only">: show this source</span>
    </button>
  );
}

export function ClaimItem({
  claim,
  evidence,
  selectedEvidence,
  onSelectEvidence,
}: {
  claim: ResearchClaim;
  evidence: Map<string, EvidenceItem>;
  selectedEvidence: string | null;
  onSelectEvidence: (id: string, trigger: HTMLElement) => void;
}) {
  // The "not individually reviewed" note is reported once in unknowns, not under every claim.
  const reasons =
    claim.validation_status === "needs_qualification"
      ? (claim.validation_notes ?? []).filter((n) => !n.startsWith("Not individually reviewed"))
      : [];
  return (
    <li className="scroll-mt-32 break-inside-avoid py-3" id={claim.claim_id}>
      <div className="flex flex-wrap items-start gap-2">
        <ClaimTypeBadge type={claim.claim_type} />
        <p className="min-w-0 flex-1 text-[15px] leading-relaxed text-ink">{naturalize(claim.text)}</p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 pl-0 sm:pl-[6.5rem]">
        {(claim.evidence_ids ?? []).map((id) => (
          <EvidenceChip key={id} id={id} evidence={evidence} selected={selectedEvidence === id} onSelect={onSelectEvidence} />
        ))}
        {claim.validation_status === "needs_qualification" && (
          <Hint
            focusable={false}
            text="Passed the fact-check with a condition, explained below the statement."
            className="rounded-full border border-warning/60 bg-warning/10 px-2 py-0.5 text-[11px] text-warning-text"
          >
            read with care
          </Hint>
        )}
        {claim.produced_by === "deterministic_fallback" && (
          <Hint
            focusable={false}
            text="Written by code straight from the retrieved data, because an AI step failed. Nothing is invented."
            className="rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-3"
          >
            built from data, not the AI
          </Hint>
        )}
        {claim.confidence_note && <span className="text-[11px] text-ink-3">{claim.confidence_note}</span>}
      </div>
      {reasons.length > 0 && <p className="mt-1 text-xs text-warning-text sm:pl-[6.5rem]">{naturalize(reasons.join(" "))}</p>}
      {claim.assumptions && claim.assumptions.length > 0 && (
        <p className="mt-1 text-xs text-ink-3 sm:pl-[6.5rem]">Assumptions: {claim.assumptions.join("; ")}</p>
      )}
    </li>
  );
}

export function ClaimList({
  claims,
  evidence,
  selectedEvidence,
  onSelectEvidence,
  empty,
}: {
  claims: ResearchClaim[];
  evidence: Map<string, EvidenceItem>;
  selectedEvidence: string | null;
  onSelectEvidence: (id: string, trigger: HTMLElement) => void;
  empty?: string;
}) {
  if (claims.length === 0) {
    return <p className="text-sm text-ink-3">{empty ?? "No supported findings for this section."}</p>;
  }
  return (
    <ul className="divide-y divide-hairline">
      {claims.map((c) => (
        <ClaimItem key={c.claim_id} claim={c} evidence={evidence} selectedEvidence={selectedEvidence} onSelectEvidence={onSelectEvidence} />
      ))}
    </ul>
  );
}
