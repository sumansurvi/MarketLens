"use client";

import type { BriefSection, EvidenceItem, ResearchBrief, ResearchClaim } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import {
  categoryLabel,
  modelName,
  naturalize,
  niceDate,
  niceProviderTime,
  niceRange,
  niceTimestamp,
  timeAgo,
  withoutIds,
} from "@/lib/natural";

import { Hint } from "../hint";
import { Card, ClaimTypeBadge, cn, ModeBadge, Notice, StatusBadge } from "../ui";
import { ClaimList, EvidenceChip, evidenceLabel } from "./claims";
import { jumpToSection } from "./jump";
import { SectionActions } from "./share";
import { type ChartBar, type ChartMarker, PriceChart } from "./price-chart";

type Select = (id: string, trigger: HTMLElement) => void;

function bySection(claims: ResearchClaim[], section: BriefSection): ResearchClaim[] {
  return claims.filter((c) => c.section === section);
}

type SectionKind = "market" | "fundamentals" | "filings" | "news";
const SECTION_KINDS: Record<SectionKind, string[]> = {
  market: ["quote", "price_series"],
  fundamentals: ["financial_metric"],
  filings: ["filing"],
  news: ["news"],
};

function sectionEvidence(kind: SectionKind, evidence: Map<string, EvidenceItem>): EvidenceItem[] {
  return [...evidence.values()].filter((e) => SECTION_KINDS[kind].includes(e.kind));
}

/** "Last 3 months (default — not user-selected)" → "Last 3 months". */
function windowName(label: string): string {
  return label.replace(/\s*\(.*\)\s*$/, "");
}

function analysisLabel(brief: ResearchBrief): string {
  if (brief.llm_mode === "fixture") return "Scripted demo analysis (no AI model)";
  const model = modelName(brief.review.reviewer);
  return model ? `AI analysis by ${model}` : "AI analysis";
}

/** One chip per data category: what date the data describes, and whether it is fresh. */
function Freshness({ brief, evidence }: { brief: ResearchBrief; evidence: Map<string, EvidenceItem> }) {
  const a = brief.as_of;
  const chips: { label: string; value: string; kind: SectionKind | null; hint: string }[] = [
    {
      label: "Prices",
      value: a.price_series_end || a.market_observation_date ? niceDate(a.price_series_end ?? a.market_observation_date) : "not available",
      kind: "market",
      hint: "Last trading day covered by the price data.",
    },
    {
      label: "Financials",
      value: a.latest_fiscal_period_end ? `period ended ${niceDate(a.latest_fiscal_period_end)}` : "not available",
      kind: "fundamentals",
      hint: "End of the latest reporting period in the company's SEC filings.",
    },
    {
      label: "Filings",
      value: a.latest_filing_date ? niceDate(a.latest_filing_date) : "not available",
      kind: "filings",
      hint: "Date of the most recent SEC filing (10-K, 10-Q or 8-K).",
    },
    {
      label: "News",
      value: a.latest_news_published_at ? niceDate(a.latest_news_published_at.slice(0, 10)) : "none available",
      kind: "news",
      hint: "Publication date of the latest news story.",
    },
  ];
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Data freshness">
      {chips.map((chip) => {
        const items = chip.kind ? sectionEvidence(chip.kind, evidence) : [];
        const stale = items.some((e) => e.freshness?.status === "stale");
        const missing = chip.value.startsWith("not") || chip.value.startsWith("none");
        const hint = missing
          ? "Couldn't be retrieved, so it is left out rather than guessed."
          : `${chip.hint}${stale ? " Older than expected for this kind of data." : ""}`;
        return (
          <li key={chip.label}>
            <Hint
              text={hint}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs",
                missing ? "border-dashed border-baseline text-ink-3" : "border-line bg-raised text-ink-2",
              )}
            >
              <span className="font-semibold text-ink">{chip.label}</span>
              <span>{chip.value}</span>
              {stale && <span className="font-semibold text-warning-text">· out of date</span>}
            </Hint>
          </li>
        );
      })}
      <li>
        <Hint
          text={`When MarketLens downloaded the data: ${niceTimestamp(a.latest_retrieved_at)}.`}
          className="inline-flex items-center gap-1.5 rounded-full border border-line bg-raised px-3 py-1 text-xs text-ink-2"
        >
          <span className="font-semibold text-ink">Fetched</span>
          <span>{timeAgo(a.latest_retrieved_at)}</span>
        </Hint>
      </li>
    </ul>
  );
}

/** How much of the draft survived checking against the sources. */
function FactCheck({ brief }: { brief: ResearchBrief }) {
  const r = brief.review;
  const total = Math.max(r.claims_total, 1);
  const numbers = r.numeric_checks_passed + (r.numeric_checks_failed ?? 0);
  const segments = [
    { n: r.supported, label: "verified", bar: "bg-good", dot: "bg-good", hint: "Every number and date matches a retrieved source." },
    {
      n: r.needs_qualification,
      label: "read with care",
      bar: "bg-warning",
      dot: "bg-warning",
      hint: "Passed with a condition, explained under the statement.",
    },
    {
      n: r.withheld,
      label: "set aside",
      bar: "bg-critical/70",
      dot: "bg-critical/70",
      hint: "Failed the fact-check, so it isn't shown as a finding. Listed at the bottom with the reason.",
    },
  ];
  return (
    <div className="rounded-xl border border-line bg-raised p-3.5" data-testid="fact-check">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-ink">
          Fact-check: {r.supported} of {r.claims_total} statement{r.claims_total === 1 ? "" : "s"} verified
        </p>
        <p className="text-xs text-ink-3">
          {numbers > 0 ? `${r.numeric_checks_passed} of ${numbers} numbers matched their source` : "No numbers to check"}
        </p>
      </div>
      <div className="mt-2.5 flex h-2 overflow-hidden rounded-full bg-sunken" aria-hidden="true">
        {segments.map((s) =>
          s.n > 0 ? <span key={s.label} className={cn("h-full first:rounded-l-full last:rounded-r-full", s.bar)} style={{ width: `${(s.n / total) * 100}%` }} /> : null,
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
        {segments.map((s) => (
          <li key={s.label}>
            <Hint text={s.hint} className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", s.dot)} />
              {s.n} {s.label}
            </Hint>
          </li>
        ))}
        {r.withheld > 0 && (
          <li>
            <a href="#withheld" onClick={(e) => jumpToSection(e, "withheld")} className="font-medium text-accent hover:underline">
              Why some were set aside ↓
            </a>
          </li>
        )}
      </ul>
    </div>
  );
}

function BriefIntro({ brief, evidence }: { brief: ResearchBrief; evidence: Map<string, EvidenceItem> }) {
  const h = brief.time_horizon;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-3">Research brief</p>
          <h2 id="brief-title" className="mt-1 text-2xl font-semibold tracking-tight text-ink">
            {brief.company.company_name}{" "}
            <span className="font-mono text-lg font-medium text-ink-3">{brief.company.symbol}</span>
          </h2>
          <p className="mt-1 text-sm text-ink-2">
            {windowName(h.label)} · {niceRange(h.start_date, h.end_date)}
            {h.is_default && (
              <Hint
                text="No time window was chosen, so the last 3 months were used."
                className="ml-2 rounded-full bg-sunken px-2 py-0.5 text-[11px] text-ink-3"
              >
                default window
              </Hint>
            )}
            {brief.company.exchange && <span className="text-ink-3"> · {brief.company.exchange}</span>}
          </p>
          <p className="mt-0.5 text-xs text-ink-3" title={niceTimestamp(brief.generated_at)}>
            {analysisLabel(brief)} · written {timeAgo(brief.generated_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={brief.report_status} explain />
          <ModeBadge isFixture={brief.is_fixture} explain />
        </div>
      </div>
      <div className="mt-4 space-y-3">
        <Freshness brief={brief} evidence={evidence} />
        <FactCheck brief={brief} />
        {brief.fixture_disclosure && (
          <Notice tone="warning" title="Synthetic demo data">
            {brief.fixture_disclosure}
          </Notice>
        )}
      </div>
    </Card>
  );
}

type Tone = "positive" | "negative" | "risk" | "interpretation" | "scenario";

const TONES: Record<Tone, { frame: string; head: string; badge: string; count: string; icon: React.ReactNode }> = {
  positive: {
    frame: "border-good/35",
    head: "bg-good/10",
    badge: "bg-good/20 text-good-text",
    count: "bg-good/15 text-good-text",
    icon: <path d="M12 5l7 9H5z" fill="currentColor" stroke="none" />,
  },
  negative: {
    frame: "border-critical/35",
    head: "bg-critical/10",
    badge: "bg-critical/20 text-critical-text",
    count: "bg-critical/15 text-critical-text",
    icon: <path d="M12 19l-7-9h14z" fill="currentColor" stroke="none" />,
  },
  risk: {
    frame: "border-warning/50",
    head: "bg-warning/12",
    badge: "bg-warning/25 text-warning-text",
    count: "bg-warning/20 text-warning-text",
    icon: (
      <>
        <path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" />
        <path d="M12 8v4.5M12 16v.01" strokeLinecap="round" />
      </>
    ),
  },
  interpretation: {
    frame: "border-accent/35",
    head: "bg-accent-wash",
    badge: "bg-accent/20 text-accent",
    count: "bg-accent/15 text-accent",
    icon: (
      <>
        <path d="M9 18h6M10 21h4" strokeLinecap="round" />
        <path d="M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.9V16h5v-.2c0-.8.4-1.5 1-1.9A6 6 0 0 0 12 3z" />
      </>
    ),
  },
  scenario: {
    frame: "border-[#7c5cd6]/35",
    head: "bg-[#7c5cd6]/10",
    badge: "bg-[#7c5cd6]/20 text-[#5b3fb5] dark:text-[#b9a6f5]",
    count: "bg-[#7c5cd6]/15 text-[#5b3fb5] dark:text-[#b9a6f5]",
    icon: (
      <>
        <path d="M6 3v6a6 6 0 0 0 6 6h6" strokeLinecap="round" />
        <path d="M6 9v12M15 12l3 3-3 3" strokeLinecap="round" strokeLinejoin="round" />
      </>
    ),
  },
};

/** A findings section with its own colour and icon, so positive, negative, risks, interpretation and scenarios read apart at a glance. */
function ToneSection({
  tone,
  title,
  description,
  claims,
  brief,
  children,
}: {
  tone: Tone;
  title: string;
  description: string;
  claims: ResearchClaim[];
  brief: ResearchBrief;
  children: React.ReactNode;
}) {
  const t = TONES[tone];
  return (
    <section className={cn("overflow-hidden rounded-2xl border bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.04)]", t.frame)} data-tone={tone}>
      <header className={cn("flex items-center gap-3 px-5 py-3", t.head)}>
        <span aria-hidden="true" className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-xl", t.badge)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
            {t.icon}
          </svg>
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold tracking-tight text-ink">{title}</h2>
          <p className="text-xs text-ink-2">{description}</p>
        </div>
        <span className={cn("shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold tabular", t.count)}>
          {claims.length} {claims.length === 1 ? "point" : "points"}
        </span>
      </header>
      <div className="px-5 pb-2 pt-1">
        {children}
        <SectionActions brief={brief} title={title} claims={claims} />
      </div>
    </section>
  );
}

/** Per-section "as of": what the data describes vs when it was fetched, plus freshness. */
function SectionAsOf({ kind, brief, evidence }: { kind: SectionKind; brief: ResearchBrief; evidence: Map<string, EvidenceItem> }) {
  const a = brief.as_of;
  const items = sectionEvidence(kind, evidence);
  if (items.length === 0) return null;
  const retrieved = items.map((e) => e.retrieved_at).sort().at(-1);
  const stale = items.find((e) => e.freshness?.status === "stale");
  const fresh = items.some((e) => e.freshness?.status === "fresh");
  const observed = {
    market: a.price_series_end
      ? `Daily closes through ${niceDate(a.price_series_end)}${a.market_observation_date ? ` · quote for ${niceDate(a.market_observation_date)}` : ""}`
      : `Quote for ${niceDate(a.market_observation_date)}`,
    fundamentals: `Latest reporting period ended ${niceDate(a.latest_fiscal_period_end)}`,
    filings: `Latest filing ${niceDate(a.latest_filing_date)}`,
    news: `Latest story ${niceProviderTime(a.latest_news_published_at, false)}`,
  }[kind];
  return (
    <p className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-3" data-testid={`as-of-${kind}`}>
      <span>
        <span className="font-medium text-ink-2">As of </span>
        {observed} · fetched <span title={niceTimestamp(retrieved)}>{timeAgo(retrieved)}</span>
      </span>
      {stale ? (
        <Hint
          text={`Older than expected for this kind of data.${stale.freshness?.note ? ` ${stale.freshness.note}` : ""}`}
          className="rounded-full border border-warning/60 bg-warning/10 px-2 py-0.5 text-[11px] text-warning-text"
        >
          out of date
        </Hint>
      ) : fresh ? (
        <Hint
          text="The latest data available for this kind of source."
          className="rounded-full border border-good/40 bg-good/10 px-2 py-0.5 text-[11px] text-good-text"
        >
          ✓ current
        </Hint>
      ) : null}
    </p>
  );
}

function KeyMetrics({ brief, evidence, selected, onSelect }: { brief: ResearchBrief; evidence: Map<string, EvidenceItem>; selected: string | null; onSelect: Select }) {
  const groups: ["market" | "fundamentals", string][] = [
    ["market", "Market"],
    ["fundamentals", "Financial results"],
  ];
  return (
    <div className="space-y-5">
      {groups.map(([group, label]) => {
        const rows = (brief.key_metrics ?? []).filter((m) => m.group === group);
        if (rows.length === 0) return null;
        return (
          <div key={group}>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-3">{label}</h3>
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {rows.map((m) => {
                const signed = /^[+−-]/.test(m.display_value);
                const up = m.display_value.startsWith("+");
                return (
                  <li key={m.label} className="rounded-xl border border-line bg-raised p-3.5">
                    <p className="text-xs text-ink-3">{m.label}</p>
                    <p className={cn("mt-1 text-2xl font-semibold tracking-tight tabular", signed && (up ? "text-good-text" : "text-critical-text"))}>
                      {signed && <span aria-hidden="true" className="mr-1 text-base">{up ? "▲" : "▼"}</span>}
                      {m.display_value}
                    </p>
                    <p className="text-xs text-ink-3">{naturalize(m.as_of)}</p>
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                      <ClaimTypeBadge type={m.claim_type} />
                      {(m.evidence_ids ?? []).slice(0, 2).map((id) => (
                        <EvidenceChip key={id} id={id} evidence={evidence} selected={selected === id} onSelect={onSelect} />
                      ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function chartData(brief: ResearchBrief, evidence: Map<string, EvidenceItem>): { bars: ChartBar[]; markers: ChartMarker[]; subtitle: string } | null {
  const series = [...evidence.values()].find((e) => e.kind === "price_series");
  if (!series) return null;
  const payload = (series.payload ?? {}) as Record<string, unknown>;
  const bars = ((payload.bars as ChartBar[] | undefined) ?? []).map((b) => ({
    ...b,
    open: Number(b.open),
    high: Number(b.high),
    low: Number(b.low),
    close: Number(b.close),
  }));
  const markers: ChartMarker[] = [];
  for (const e of evidence.values()) {
    if (e.kind !== "calculation") continue;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    if (typeof p.trading_date === "string" && p.percentage_change !== undefined && p.category === "market") {
      const pct = Number(p.percentage_change);
      markers.push({ date: p.trading_date, pct, kind: pct < 0 ? "decline" : "gain" });
    }
  }
  const subtitle = [
    `Trading days ${niceRange(String(payload.first_date), String(payload.last_date))}`,
    String(payload.adjustment ?? "unknown adjustment"),
    brief.is_fixture ? "synthetic demo data" : series.source_name,
  ].join(" · ");
  return { bars, markers, subtitle };
}

const WITHHELD_REASON: Record<string, string> = {
  numeric_mismatch: "Numbers didn't match the sources",
  date_mismatch: "Dates weren't in the sources",
  unsupported: "Not backed by a retrieved source",
  contradicted: "Contradicted by the sources",
  unverified: "Couldn't be verified",
  needs_qualification: "Needed qualification",
};

function Gaps({ brief, hiddenCodes }: { brief: ResearchBrief; hiddenCodes: string[] }) {
  const gaps = (brief.unknowns_and_gaps ?? []).filter((g) => !g.reason_code || !hiddenCodes.includes(g.reason_code));
  const notes = gaps.filter((g) => g.reason_code === "AGENT_NOTE");
  const main = gaps.filter((g) => g.reason_code !== "AGENT_NOTE");
  const quality = brief.source_quality_notes ?? [];
  if (main.length === 0 && notes.length === 0 && quality.length === 0) return null;
  return (
    <Card title="What this brief couldn't establish" description="Stated plainly instead of guessed.">
      {main.length > 0 && (
        <ul className="grid gap-2.5 sm:grid-cols-2">
          {main.map((gap, i) => {
            const material = gap.severity === "material";
            return (
              <li key={i} className={cn("flex gap-3 rounded-xl border p-3 text-sm", material ? "border-warning/50 bg-warning/[0.07]" : "border-line bg-raised")}>
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold",
                    material ? "bg-warning/30 text-warning-text" : "bg-sunken text-ink-3",
                  )}
                >
                  {material ? "!" : "i"}
                </span>
                <span className="text-ink-2">
                  <span className="sr-only">{material ? "Important: " : "Note: "}</span>
                  {gap.category && <span className="font-medium text-ink">{categoryLabel(gap.category)}: </span>}
                  {naturalize(gap.description)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {quality.length > 0 && (
        <div className="mt-4">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-3">About the sources</h3>
          <ul className="mt-1.5 space-y-1 text-sm text-ink-2">
            {quality.map((n) => (
              <li key={n}>{naturalize(n)}</li>
            ))}
          </ul>
        </div>
      )}
      {notes.length > 0 && (
        <details className="group mt-4 rounded-xl border border-line bg-raised px-3.5 py-2.5">
          <summary className="cursor-pointer text-sm font-medium text-ink-2 hover:text-ink">Notes from the AI analysts ({notes.length})</summary>
          <ul className="mt-2 space-y-1.5 text-sm text-ink-2">
            {notes.map((n, i) => (
              <li key={i}>{naturalize(n.description)}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

function SetAside({ brief }: { brief: ResearchBrief }) {
  const withheld = brief.withheld_claims ?? [];
  if (withheld.length === 0) return null;
  return (
    <Card
      id="withheld"
      title={`Set aside by the fact-checker (${withheld.length})`}
      description="Draft statements that failed checks against the sources. They are not part of the findings."
    >
      <details className="rounded-xl border border-line bg-raised px-3.5 py-2.5">
        <summary className="cursor-pointer text-sm font-medium text-ink-2 hover:text-ink">Show what was set aside and why</summary>
        <ul className="mt-3 space-y-2.5">
          {withheld.map((c) => (
            <li key={c.claim_id} className="rounded-lg border border-line bg-surface p-3 text-sm">
              <span className="inline-flex rounded-full border border-critical/30 bg-critical/10 px-2 py-0.5 text-[11px] font-medium text-critical-text">
                {WITHHELD_REASON[c.validation_status] ?? humanize(c.validation_status)}
              </span>
              <p className="mt-1.5 italic leading-relaxed text-ink-3">“{naturalize(c.text)}”</p>
              {(c.validation_notes ?? []).length > 0 && <p className="mt-1 text-xs text-ink-2">{withoutIds((c.validation_notes ?? []).join(" "))}</p>}
            </li>
          ))}
        </ul>
      </details>
    </Card>
  );
}

function Sources({ brief, evidence, selected, onSelect }: { brief: ResearchBrief; evidence: Map<string, EvidenceItem>; selected: string | null; onSelect: Select }) {
  const sources = brief.sources ?? [];
  return (
    <details className="rounded-2xl border border-line bg-surface p-5">
      <summary className="cursor-pointer font-semibold tracking-tight text-ink">
        Sources <span className="font-normal text-ink-3">({sources.length})</span>
      </summary>
      <p className="mt-1 text-sm text-ink-3">Every item retrieved or calculated for this brief. Select one to inspect it.</p>
      <ul className="mt-3 divide-y divide-hairline">
        {sources.map((s) => {
          const item = evidence.get(s.evidence_id);
          const when = s.published_at
            ? niceProviderTime(s.published_at, item?.published_at_tz_known)
            : s.period_end
              ? niceRange(s.period_start, s.period_end)
              : null;
          return (
            <li key={s.evidence_id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5 text-sm">
              <EvidenceChip id={s.evidence_id} evidence={evidence} selected={selected === s.evidence_id} onSelect={onSelect} />
              <span className="min-w-0 flex-1">
                <span className="block text-ink">{naturalize(s.title) || evidenceLabel(item)}</span>
                <span className="block text-xs text-ink-3">
                  {s.source_name}
                  {when ? ` · ${when}` : ""} · fetched {timeAgo(s.retrieved_at)}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function Method({ brief }: { brief: ResearchBrief }) {
  return (
    <details className="rounded-2xl border border-line bg-surface p-5">
      <summary className="cursor-pointer font-semibold tracking-tight text-ink">How this analysis was prepared</summary>
      <ol className="mt-4 space-y-2.5 text-sm">
        {brief.methodology.steps.map((step, i) => (
          <li key={step.name} className="flex gap-3">
            <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-sunken text-[11px] font-semibold text-ink-2">{i + 1}</span>
            <span>
              <span className="font-medium text-ink">{step.name}</span> <span className="text-xs text-ink-3">({humanize(step.agent)})</span>
              <span className="block text-ink-2">{step.description}</span>
            </span>
          </li>
        ))}
      </ol>
      <div className="mt-5 grid gap-4 text-sm md:grid-cols-2">
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-3">Calculated by code, not the AI</h3>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-ink-2">
            {brief.methodology.deterministic_calculations.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-3">Written by the AI</h3>
          <p className="mt-1.5 text-ink-2">
            {brief.methodology.model_generated.length === 0
              ? "Nothing: this brief was composed from retrieved values without a language model."
              : brief.methodology.model_generated.join("; ")}
          </p>
          <h3 className="mt-4 text-[11px] font-semibold uppercase tracking-wider text-ink-3">Limitations</h3>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-ink-2">
            {(brief.limitations ?? []).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </div>
      </div>
    </details>
  );
}

export function BriefView({
  brief,
  evidence,
  selectedEvidence,
  onSelectEvidence,
  hiddenGapCodes = [],
}: {
  brief: ResearchBrief;
  evidence: Map<string, EvidenceItem>;
  selectedEvidence: string | null;
  onSelectEvidence: Select;
  /** Gap reasons already explained in the run's "Heads up" card. */
  hiddenGapCodes?: string[];
}) {
  const claims = brief.claims ?? [];
  const common = { evidence, selectedEvidence, onSelectEvidence };
  const chart = chartData(brief, evidence);
  const scenarios = bySection(claims, "scenarios");

  return (
    <article className="space-y-6" aria-labelledby="brief-title">
      <BriefIntro brief={brief} evidence={evidence} />

      <Card title="Key numbers" description="Each value shows the period it describes; select a source to see where it came from.">
        <KeyMetrics brief={brief} evidence={evidence} selected={selectedEvidence} onSelect={onSelectEvidence} />
      </Card>

      <Card title="Price movement">
        <SectionAsOf kind="market" brief={brief} evidence={evidence} />
        {chart && chart.bars.length > 0 && (
          <div className="mb-4">
            <PriceChart symbol={brief.company.symbol} bars={chart.bars} markers={chart.markers} subtitle={chart.subtitle} />
          </div>
        )}
        <ClaimList claims={bySection(claims, "market_movement")} {...common} empty="Price history wasn't available for this run." />
      </Card>

      <Card title="Financial results" description="Reported values with units, currency and reporting period.">
        <SectionAsOf kind="fundamentals" brief={brief} evidence={evidence} />
        <ClaimList claims={bySection(claims, "fundamentals")} {...common} empty="Financial results weren't available for this run." />
      </Card>

      <Card title="SEC filings">
        <SectionAsOf kind="filings" brief={brief} evidence={evidence} />
        <ClaimList claims={bySection(claims, "filings")} {...common} empty="Filing details weren't available for this run." />
      </Card>

      <Card title="News and events" description="Dated context only: a story published near a price move doesn't prove it caused it.">
        <SectionAsOf kind="news" brief={brief} evidence={evidence} />
        {(brief.recent_events ?? []).length === 0 ? (
          <p className="text-sm text-ink-3">No news stories from this period are available for this run.</p>
        ) : (
          <ol className="relative space-y-4 border-l-2 border-hairline pl-5">
            {(brief.recent_events ?? []).map((event) => (
              <li key={event.evidence_id} className="relative">
                <span aria-hidden="true" className="absolute -left-[1.6rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-surface bg-[#eda100]" />
                <p className="text-xs font-medium text-ink-3">{niceProviderTime(event.published_at, event.published_at_tz_known)}</p>
                <p className="mt-0.5 font-medium leading-snug text-ink">{event.headline}</p>
                <p className="mt-0.5 text-sm text-ink-2">{naturalize(event.relevance_note)}</p>
                <p className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-ink-3">
                  <span>{event.publisher ?? "Unknown publisher"}</span>
                  <EvidenceChip id={event.evidence_id} evidence={evidence} selected={selectedEvidence === event.evidence_id} onSelect={onSelectEvidence} />
                </p>
              </li>
            ))}
          </ol>
        )}
        {bySection(claims, "recent_events").length > 0 && (
          <div className="mt-4 border-t border-hairline pt-2">
            <ClaimList claims={bySection(claims, "recent_events")} {...common} />
          </div>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <ToneSection brief={brief} tone="positive" title="What looks positive" description="Evidence in the company's favour." claims={bySection(claims, "positive_evidence")}>
          <ClaimList claims={bySection(claims, "positive_evidence")} {...common} empty="Nothing positive stood out in the evidence." />
        </ToneSection>
        <ToneSection brief={brief} tone="negative" title="What looks negative" description="Evidence that weighs against it." claims={bySection(claims, "negative_evidence")}>
          <ClaimList claims={bySection(claims, "negative_evidence")} {...common} empty="Nothing negative stood out in the evidence." />
        </ToneSection>
      </div>

      <ToneSection brief={brief} tone="risk" title="Risks" description="Quoted from filings or drawn from cited evidence." claims={bySection(claims, "risks")}>
        <ClaimList claims={bySection(claims, "risks")} {...common} empty="No risk statements passed the fact-check for this run." />
      </ToneSection>

      <ToneSection
        brief={brief}
        tone="interpretation"
        title="Interpretation"
        description="Analysis, not fact. The wording says how certain it is."
        claims={bySection(claims, "interpretation")}
      >
        <ClaimList claims={bySection(claims, "interpretation")} {...common} empty="No interpretations passed the fact-check." />
      </ToneSection>

      {scenarios.length > 0 && (
        <ToneSection brief={brief} tone="scenario" title="Scenarios" description="If…, then…: conditional, with stated assumptions. Not forecasts." claims={scenarios}>
          <ClaimList claims={scenarios} {...common} />
        </ToneSection>
      )}

      <Gaps brief={brief} hiddenCodes={hiddenGapCodes} />
      <SetAside brief={brief} />
      <Sources brief={brief} evidence={evidence} selected={selectedEvidence} onSelect={onSelectEvidence} />
      <Method brief={brief} />

      {/* The summary closes the brief (D-91): the evidence and findings come first, the takeaway last. */}
      <Card id="summary" title="Summary" description="The takeaway from everything above. A balanced overview, not a recommendation to buy or sell.">
        <ClaimList claims={bySection(claims, "executive_summary")} {...common} empty="No summary statements passed the fact-check for this run." />
        <SectionActions brief={brief} title="Summary" claims={bySection(claims, "executive_summary")} />
      </Card>

      <p className="rounded-xl border border-line bg-sunken px-4 py-3 text-xs leading-relaxed text-ink-2" data-testid="brief-disclaimer">
        {brief.disclaimer}
      </p>
    </article>
  );
}
