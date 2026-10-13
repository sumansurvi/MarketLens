"use client";

import { useEffect, useRef } from "react";

import type { EvidenceItem, ResearchClaim } from "@/lib/api/types";
import { naturalize, niceProviderTime, niceRange, niceTimestamp } from "@/lib/natural";
import { describeSource, flagText, providerName, unitName } from "@/lib/readable";

import { ModeBadge, Notice } from "../ui";
import { evidenceLabel } from "./claims";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-2 py-1.5 text-sm">
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

function linkHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "the source";
  }
}

function isFixtureUrl(url: string): boolean {
  return url.startsWith("https://example.com/marketlens-fixture");
}

/** The source's values in plain words (no field names, IDs or code-style formulas). */
function SourceDetails({
  item,
  evidence,
  onSelect,
}: {
  item: EvidenceItem;
  evidence: Map<string, EvidenceItem>;
  onSelect: (id: string, trigger: HTMLElement) => void;
}) {
  const described = describeSource(item);
  const inputs = item.kind === "calculation" ? (((item.payload ?? {}) as Record<string, unknown>).input_evidence_ids as string[] | undefined) ?? [] : [];
  if (!described.method && described.rows.length === 0 && !described.table) return null;
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-3">{item.kind === "calculation" ? "How it was calculated" : "What the source says"}</p>
      {described.method && <p className="rounded-lg bg-sunken px-3 py-2 text-sm text-ink-2">{described.method}</p>}
      {described.rows.length > 0 && (
        <dl className="divide-y divide-hairline rounded-lg border border-line">
          {described.rows.map(([label, value]) => (
            <div key={label} className="flex items-baseline justify-between gap-3 px-3 py-1.5 text-sm">
              <dt className="text-ink-3">{label}</dt>
              <dd className="text-right font-medium tabular text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {described.table && (
        <div className="max-h-64 overflow-auto rounded-lg border border-line">
          <table className="w-full text-left text-xs tabular">
            <thead className="bg-sunken text-ink-3">
              <tr>
                <th className="px-3 py-1.5 font-medium">{described.table.head[0]}</th>
                <th className="px-3 py-1.5 text-right font-medium">{described.table.head[1]}</th>
                <th className="px-3 py-1.5 font-medium">{described.table.head[2]}</th>
              </tr>
            </thead>
            <tbody>
              {described.table.rows.map((row, i) => (
                <tr key={i} className="border-t border-hairline">
                  <td className="px-3 py-1.5">{row[0]}</td>
                  <td className="px-3 py-1.5 text-right font-medium text-ink">{row[1]}</td>
                  <td className="px-3 py-1.5 text-ink-2">{row[2]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {described.table.note && <p className="border-t border-hairline px-3 py-1.5 text-[11px] text-ink-3">{described.table.note}</p>}
        </div>
      )}
      {inputs.length > 0 && (
        <div>
          <p className="text-xs font-medium text-ink-3">Calculated from</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {inputs.map((id) => (
              <li key={id}>
                <button
                  type="button"
                  onClick={(e) => onSelect(id, e.currentTarget)}
                  className="rounded-full border border-line bg-raised px-2 py-0.5 text-[11px] font-medium text-accent hover:border-accent"
                >
                  {evidenceLabel(evidence.get(id))}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function EvidenceInspector({
  item,
  evidence,
  claims,
  onSelect,
  onClose,
}: {
  item: EvidenceItem;
  evidence: Map<string, EvidenceItem>;
  claims: ResearchClaim[];
  onSelect: (id: string, trigger: HTMLElement) => void;
  onClose: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, [item.evidence_id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const citedBy = claims.filter((c) => (c.evidence_ids ?? []).includes(item.evidence_id));
  const injection = item.flags?.includes("suspected_prompt_injection");

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-3">Source · {evidenceLabel(item)}</p>
          <h2 ref={headingRef} tabIndex={-1} className="mt-1 text-base font-semibold leading-snug text-ink outline-none">
            {naturalize(item.title) || "Evidence item"}
          </h2>
          <div className="mt-2">
            <ModeBadge isFixture={item.is_fixture} />
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-2 py-1 text-sm text-ink-2 hover:bg-sunken"
          aria-label="Close evidence inspector"
        >
          ✕
        </button>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {injection && (
          <Notice tone="warning" title="Untrusted content flagged">
            This source contains instruction-like text. MarketLens treats it as data only: it is excluded from the
            analysis and nothing in it was followed.
          </Notice>
        )}
        <dl className="divide-y divide-hairline">
          <Field label="Source">{item.source_name}</Field>
          <Field label="Provider">{providerName(item.provider)}</Field>
          {item.source_url && (
            <Field label="Link">
              <a href={item.source_url} target="_blank" rel="noopener noreferrer nofollow" className="text-accent underline-offset-2 hover:underline">
                Open on {linkHost(item.source_url)} ↗
              </a>
              {isFixtureUrl(item.source_url) && (
                <span className="mt-0.5 block text-xs text-ink-3">Synthetic placeholder link on a documentation domain, not a real document.</span>
              )}
            </Field>
          )}
          {(item.published_at || item.published_date) && (
            <Field label="Published">{niceProviderTime(item.published_at ?? item.published_date, item.published_at_tz_known)}</Field>
          )}
          {(item.period_start || item.period_end) && <Field label="Period">{niceRange(item.period_start, item.period_end)}</Field>}
          <Field label="Fetched">{niceTimestamp(item.retrieved_at)}</Field>
          {(item.currency || item.units) && <Field label="Units">{unitName(item.currency, item.units)}</Field>}
          {item.freshness && (
            <Field label="Freshness">
              <span className={item.freshness.status === "stale" ? "font-semibold text-warning-text" : undefined}>
                {item.freshness.status === "stale" ? "Older than expected" : item.freshness.status === "fresh" ? "✓ Latest available" : "Date not reported"}
              </span>
            </Field>
          )}
          {item.flags && item.flags.length > 0 && <Field label="Note">{item.flags.map(flagText).join("; ")}</Field>}
        </dl>

        {item.excerpt && (
          <div>
            <p className="text-xs font-medium text-ink-3">Excerpt (untrusted source text, quoted not followed)</p>
            <blockquote className="mt-1 whitespace-pre-line border-l-2 border-baseline pl-3 text-sm text-ink-2">{item.excerpt}</blockquote>
          </div>
        )}

        <SourceDetails item={item} evidence={evidence} onSelect={onSelect} />

        {item.notes && item.notes.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-3">
            {item.notes.map((n) => (
              <li key={n}>{naturalize(n)}</li>
            ))}
          </ul>
        )}

        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-3">
            {citedBy.length === 1 ? "1 statement uses this source" : `${citedBy.length} statements use this source`}
          </p>
          <ul className="mt-1 space-y-1.5">
            {citedBy.map((c) => (
              <li key={c.claim_id} className="rounded-md bg-sunken px-2.5 py-1.5 text-xs text-ink-2">
                <a href={`#${c.claim_id}`} className="hover:underline">
                  {naturalize(c.text)}
                </a>
              </li>
            ))}
            {citedBy.length === 0 && <li className="text-xs text-ink-3">Used as an input to a calculation, or listed under Sources.</li>}
          </ul>
        </div>

      </div>
    </div>
  );
}
