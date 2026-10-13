"use client";

import { useEffect, useState } from "react";

import type { AgentStep, DataSourceStatus, RunDetail, ToolCallRecord } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { categoryLabel, elapsed, modelName, naturalize, niceRange, niceTimestamp } from "@/lib/natural";

import { buttonClass, cn } from "../ui";

const STEP_ICON: Record<string, { icon: string; className: string; label: string }> = {
  pending: { icon: "○", className: "text-ink-3", label: "Waiting" },
  running: { icon: "◔", className: "text-accent", label: "Running" },
  completed: { icon: "✓", className: "text-good-text", label: "Done" },
  failed: { icon: "✕", className: "text-critical-text", label: "Failed" },
  skipped: { icon: "–", className: "text-ink-3", label: "Skipped" },
  cancelled: { icon: "–", className: "text-ink-3", label: "Cancelled" },
};

const STEP_LABEL: Record<string, string> = {
  plan: "Plan the research",
  collect_evidence: "Gather evidence through the MCP servers",
  calculations: "Calculate price moves and comparisons",
  market_specialist: "Market specialist",
  news_specialist: "News & events specialist",
  fundamentals_specialist: "Financials specialist",
  supervisor_synthesis: "Supervisor writes the brief",
  evidence_review: "Fact-check (AI reviewer + code checks)",
  finalize: "Decide the report status",
  failure: "Run stopped",
  cancelled: "Cancelled",
};

function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function StepsList({ steps }: { steps: AgentStep[] }) {
  if (steps.length === 0) return <p className="text-sm text-ink-3">Waiting for the workflow to start…</p>;
  return (
    <ol className="space-y-2.5" aria-live="polite">
      {steps.map((step) => {
        const style = STEP_ICON[step.status] ?? STEP_ICON.pending;
        return (
          <li key={step.seq} className="flex gap-3 text-sm">
            <span className={cn("mt-0.5 w-4 shrink-0 text-center font-bold", style.className)} aria-hidden="true">
              {style.icon}
            </span>
            <span className="min-w-0 flex-1">
              <span className="font-medium text-ink">{STEP_LABEL[step.step_key] ?? humanize(step.step_key)}</span>{" "}
              <span className="text-xs text-ink-3">
                {style.label}
                {step.latency_ms !== null && step.latency_ms !== undefined ? ` · ${duration(step.latency_ms)}` : ""}
              </span>
              {step.summary && <span className="block text-ink-2">{naturalize(step.summary)}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

const CATEGORY_STATUS: Record<string, { icon: string; label: string; className: string }> = {
  available: { icon: "✓", label: "Available", className: "text-good-text" },
  partial: { icon: "!", label: "Partly available", className: "text-warning-text" },
  unavailable: { icon: "–", label: "Not available", className: "text-warning-text" },
  failed: { icon: "✕", label: "Couldn't fetch", className: "text-critical-text" },
  not_requested: { icon: "○", label: "Not requested", className: "text-ink-3" },
};

export function SourceStatusGrid({ statuses }: { statuses: DataSourceStatus[] }) {
  if (statuses.length === 0) return null;
  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {statuses.map((s) => {
        const style = CATEGORY_STATUS[s.status] ?? CATEGORY_STATUS.not_requested;
        return (
          <li key={s.category} className="rounded-xl border border-line bg-raised px-3 py-2.5">
            <p className="flex items-center justify-between text-sm">
              <span className="font-medium text-ink">{categoryLabel(s.category)}</span>
              <span className={cn("text-xs font-semibold", style.className)}>
                <span aria-hidden="true">{style.icon}</span> {style.label}
              </span>
            </p>
            <p className="mt-0.5 text-xs text-ink-3">
              {s.evidence_count} source{s.evidence_count === 1 ? "" : "s"}
              {s.latest_observation ? ` · latest ${naturalize(s.latest_observation)}` : ""}
              {s.source_mode ? ` · ${s.source_mode === "fixture" ? "demo data" : "live data"}` : ""}
            </p>
            {s.message && <p className="mt-0.5 text-xs text-ink-2">{naturalize(s.message)}</p>}
          </li>
        );
      })}
    </ul>
  );
}

const SERVER_NAME: Record<string, string> = {
  market_data: "Market data",
  research_sources: "Research sources",
  research_workspace: "Workspace",
};

const TOOL_ACTION: Record<string, string> = {
  resolve_symbol: "Checked the ticker",
  search_symbols: "Searched tickers",
  get_quote: "Fetched the latest quote",
  get_historical_prices: "Fetched daily prices",
  calculate_price_change: "Calculated the price change",
  get_financial_metrics: "Fetched provider financials",
  get_provider_status: "Checked data-provider status",
  get_sec_company_facts: "Fetched SEC financial results",
  search_company_filings: "Searched SEC filings",
  get_filing_excerpt: "Read a filing section",
  search_company_news: "Searched news",
  get_research_sources_status: "Checked research-source status",
};

const ERROR_TEXT: Record<string, string> = {
  CAPABILITY_UNAVAILABLE: "not included in the data plan",
  RATE_LIMITED: "the provider asked us to slow down",
  PROVIDER_TIMEOUT: "the provider didn't answer in time",
  DATA_UNAVAILABLE: "no data available",
  UNKNOWN_SYMBOL: "ticker not found",
  MALFORMED_PROVIDER_RESPONSE: "the provider sent unreadable data",
  MCP_SERVER_UNAVAILABLE: "the data service was restarting",
};

/** What a call was about, in words ("MSFT · Jul 12 – Oct 12, 2026 · quarterly"). */
function callDetail(input: Record<string, unknown>): string {
  const parts: string[] = [];
  const symbol = input.symbol ?? input.query;
  if (typeof symbol === "string" && symbol) parts.push(symbol);
  const start = input.start_date ?? input.from_date;
  const end = input.end_date ?? input.to_date;
  if (typeof start === "string" || typeof end === "string") parts.push(niceRange(start as string | null, end as string | null));
  if (typeof input.period_type === "string") parts.push(input.period_type);
  if (Array.isArray(input.forms)) parts.push(input.forms.join(", "));
  if (Array.isArray(input.concepts)) parts.push(`${input.concepts.length} figures`);
  if (typeof input.section_hint === "string") parts.push(`"${input.section_hint}"`);
  return parts.join(" · ");
}

export function ToolTrace({ calls }: { calls: ToolCallRecord[] }) {
  if (calls.length === 0) return <p className="text-sm text-ink-3">No data requests recorded yet.</p>;
  return (
    <ol className="divide-y divide-hairline rounded-lg border border-line">
      {calls.map((call) => {
        const ok = call.status === "ok";
        const detail = callDetail((call.input_summary ?? {}) as Record<string, unknown>);
        return (
          <li key={call.call_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
            <span className="w-6 shrink-0 tabular text-xs text-ink-3">{call.seq ?? "—"}</span>
            <span className="min-w-0 flex-1">
              <span className="font-medium text-ink">{TOOL_ACTION[call.tool_name] ?? call.tool_name.replace(/_/g, " ")}</span>
              {detail && <span className="text-ink-2"> · {detail}</span>}
              <span className="block text-xs text-ink-3">{SERVER_NAME[call.server_id] ?? call.server_id} service</span>
            </span>
            <span className={cn("text-xs font-semibold", ok ? "text-good-text" : "text-critical-text")}>
              {ok ? "✓ Done" : `✕ Failed: ${ERROR_TEXT[call.error_code ?? ""] ?? "error"}`}
            </span>
            <span className="w-14 shrink-0 text-right tabular text-xs text-ink-3">{duration(call.latency_ms) || "—"}</span>
          </li>
        );
      })}
    </ol>
  );
}

const PHASES = [
  { label: "Gathering data", detail: "Prices, filings and news", steps: ["plan", "collect_evidence", "calculations"] },
  { label: "Specialist analysis", detail: "Market, news and financials", steps: ["market_specialist", "news_specialist", "fundamentals_specialist"] },
  { label: "Writing the brief", detail: "From checked findings only", steps: ["supervisor_synthesis"] },
  { label: "Fact-checking", detail: "Every number against its source", steps: ["evidence_review", "finalize"] },
];

type PhaseState = "done" | "active" | "waiting" | "problem";

function phaseState(steps: AgentStep[], keys: string[]): PhaseState {
  const found = steps.filter((s) => keys.includes(s.step_key));
  if (found.some((s) => s.status === "running")) return "active";
  if (found.length === keys.length && found.every((s) => s.status !== "pending")) {
    return found.some((s) => s.status === "failed") ? "problem" : "done";
  }
  return found.length > 0 ? "active" : "waiting";
}

/** Ticks once a second while `running`; null until the first tick (no clock reads during render). */
function useClock(running: boolean): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

/** Friendly four-phase progress for a running research run. */
export function RunProgress({ detail, onCancel, cancelling }: { detail: RunDetail; onCancel: () => void; cancelling: boolean }) {
  const { run, steps } = detail;
  const now = useClock(true);
  const since = run.started_at ?? run.created_at;
  const spent = now !== null && since ? now - new Date(since).getTime() : null;
  const slow = run.llm_mode !== "fixture" && spent !== null && spent > 30_000;
  return (
    <section className="rounded-2xl border border-accent/30 bg-accent-wash/40 p-5" aria-live="polite" aria-label="Research progress">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-hairline border-t-accent" aria-hidden="true" />
          <h2 className="font-semibold tracking-tight text-ink">Researching {run.symbol}…</h2>
          {spent !== null && <span className="text-sm tabular text-ink-3">{elapsed(spent)}</span>}
        </div>
        <button type="button" className={buttonClass.secondarySm} onClick={onCancel} disabled={cancelling}>
          {cancelling ? "Cancelling…" : "Cancel"}
        </button>
      </div>
      <ol className="mt-4 grid gap-3 sm:grid-cols-4">
        {PHASES.map((phase, i) => {
          const state = phaseState(steps, phase.steps);
          return (
            <li
              key={phase.label}
              className={cn(
                "rounded-xl border bg-surface p-3",
                state === "active" ? "border-accent/50 shadow-sm" : "border-line",
                state === "waiting" && "opacity-60",
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "grid h-6 w-6 place-items-center rounded-full text-xs font-bold",
                  state === "done" && "bg-good/15 text-good-text",
                  state === "problem" && "bg-warning/25 text-warning-text",
                  state === "active" && "bg-accent text-plane",
                  state === "waiting" && "bg-sunken text-ink-3",
                )}
              >
                {state === "done" ? "✓" : state === "problem" ? "!" : i + 1}
              </span>
              <p className="mt-2 text-sm font-medium text-ink">{phase.label}</p>
              <p className="text-xs text-ink-3">
                <span className="sr-only">{state === "done" ? "Done. " : state === "active" ? "In progress. " : state === "problem" ? "Finished with a problem. " : "Waiting. "}</span>
                {phase.detail}
              </p>
            </li>
          );
        })}
      </ol>
      {slow && (
        <p className="mt-4 text-xs leading-relaxed text-ink-3">
          AI analysis with a local model can take a few minutes. You can open other briefs meanwhile; this run keeps going in the background.
        </p>
      )}
      <details className="mt-4">
        <summary className="cursor-pointer text-sm font-medium text-ink-2 hover:text-ink">Show every step</summary>
        <div className="mt-3">
          <StepsList steps={steps} />
        </div>
      </details>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 py-2 text-sm">
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

/** Technical details of one run (for the "Run details" dialog). */
export function RunDetails({ detail, onOpenRun }: { detail: RunDetail; onOpenRun: (runId: string) => void }) {
  const { run, brief } = detail;
  const model = brief ? modelName(brief.review.reviewer) : null;
  const parent = run.parent_run_id;
  return (
    <div className="space-y-6">
      <dl className="divide-y divide-hairline rounded-xl border border-line bg-raised px-4">
        <Row label="Question">{run.question}</Row>
        <Row label="Window">
          {niceRange(run.time_horizon.start_date, run.time_horizon.end_date)}
          {run.time_horizon.is_default ? " (default window)" : ""}
        </Row>
        <Row label="Started">{niceTimestamp(run.created_at)}</Row>
        <Row label="Took">{run.duration_ms !== null && run.duration_ms !== undefined ? elapsed(run.duration_ms) : "still running"}</Row>
        <Row label="Data">{run.data_mode === "fixture" ? "Synthetic demo data" : "Live data from the configured providers"}</Row>
        <Row label="Analysis">{run.llm_mode === "fixture" ? "Scripted demo analysis (no AI model)" : `AI model${model ? ` · ${model}` : ""}`}</Row>
        {run.company && (
          <Row label="Company">
            {run.company.company_name}
            {run.company.exchange ? ` · ${run.company.exchange}` : ""}
            {run.company.cik ? ` · SEC CIK ${run.company.cik}` : ""}
          </Row>
        )}
        <Row label="Started by">{run.origin === "voice" ? "Voice" : run.origin === "rerun" ? "Re-run" : "Text"}</Row>
        {parent && (
          <Row label="Re-run of">
            <button type="button" className="text-accent hover:underline" onClick={() => onOpenRun(parent)}>
              Open the earlier run
            </button>
          </Row>
        )}
      </dl>
      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-3">Data sources</h3>
        <SourceStatusGrid statuses={detail.source_status} />
      </section>
      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-3">Steps</h3>
        <StepsList steps={detail.steps} />
      </section>
      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-3">MCP tool calls ({detail.tool_calls.length})</h3>
        <ToolTrace calls={detail.tool_calls} />
      </section>
    </div>
  );
}
