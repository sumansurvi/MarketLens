"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api/client";
import type { ComparePeriodsResponse } from "@/lib/api/types";
import { formatPct, formatUsd } from "@/lib/format";
import { naturalize, niceRange } from "@/lib/natural";

import { buttonClass, Card, Notice } from "../ui";

type Period = { start: string; end: string };

const DAY_MS = 86_400_000;

function midpoint(start: string, end: string): string {
  const a = Date.parse(`${start}T00:00:00Z`);
  const b = Date.parse(`${end}T00:00:00Z`);
  return new Date(a + Math.floor((b - a) / DAY_MS / 2) * DAY_MS).toISOString().slice(0, 10);
}

function PeriodFields({ label, value, onChange }: { label: string; value: Period; onChange: (p: Period) => void }) {
  const id = label.toLowerCase().replace(/\s+/g, "-");
  return (
    <fieldset className="rounded-lg border border-line p-3">
      <legend className="px-1 text-sm font-medium text-ink">{label}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs text-ink-3" htmlFor={`${id}-start`}>
          From
          <input
            id={`${id}-start`}
            type="date"
            required
            value={value.start}
            onChange={(e) => onChange({ ...value, start: e.target.value })}
            className="mt-1 block w-full rounded-md border border-line bg-raised px-2 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="text-xs text-ink-3" htmlFor={`${id}-end`}>
          To
          <input
            id={`${id}-end`}
            type="date"
            required
            value={value.end}
            onChange={(e) => onChange({ ...value, end: e.target.value })}
            className="mt-1 block w-full rounded-md border border-line bg-raised px-2 py-1.5 text-sm text-ink"
          />
        </label>
      </div>
    </fieldset>
  );
}

/** Compare closing-price change across two user-selected periods (deterministic, via MCP tools). */
export function ComparePeriods({ symbol, start, end, bare }: { symbol: string; start: string; end: string; bare?: boolean }) {
  const middle = midpoint(start, end);
  const [a, setA] = useState<Period>({ start, end: middle });
  const [b, setB] = useState<Period>({ start: middle, end });
  const [result, setResult] = useState<ComparePeriodsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { symbol, periods: [a, b].map((p) => ({ start_date: p.start, end_date: p.end })) };
      setResult(await api<ComparePeriodsResponse>("/api/compare", { method: "POST", json: body }));
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError ? err.message : "The comparison could not be computed.");
    } finally {
      setBusy(false);
    }
  }

  const body = (
    <>
      <form onSubmit={submit} className="space-y-3" aria-label={`Compare two periods for ${symbol}`}>
        <div className="grid gap-3 md:grid-cols-2">
          <PeriodFields label="Period A" value={a} onChange={setA} />
          <PeriodFields label="Period B" value={b} onChange={setB} />
        </div>
        <button type="submit" className={buttonClass.secondary} disabled={busy}>
          {busy ? "Comparing…" : "Compare periods"}
        </button>
      </form>
      {error && (
        <div className="mt-3">
          <Notice tone="critical" title="Comparison not available">
            {error}
          </Notice>
        </div>
      )}
      {result && (
        <div className="mt-4 space-y-3" aria-live="polite">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] text-left text-sm">
              <caption className="sr-only">Price change by period for {result.symbol}</caption>
              <thead className="text-xs text-ink-3">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">Period</th>
                  <th className="py-1.5 pr-3 font-medium">Requested</th>
                  <th className="py-1.5 pr-3 font-medium">Trading days used</th>
                  <th className="py-1.5 pr-3 font-medium">Start → end close</th>
                  <th className="py-1.5 font-medium">Change</th>
                </tr>
              </thead>
              <tbody>
                {result.periods.map(({ label, calculation: c }) => (
                  <tr key={label} className="border-t border-hairline align-top">
                    <td className="py-2 pr-3 font-medium">{label}</td>
                    <td className="py-2 pr-3 tabular text-ink-2">
                      {niceRange(c.requested_start, c.requested_end)}
                    </td>
                    <td className="py-2 pr-3 tabular text-ink-2">
                      {c.start_date && c.end_date ? niceRange(c.start_date, c.end_date) : "—"}
                    </td>
                    <td className="py-2 pr-3 tabular text-ink-2">
                      {c.start_value != null && c.end_value != null ? `${formatUsd(c.start_value)} → ${formatUsd(c.end_value)}` : "—"}
                    </td>
                    <td className="py-2 tabular font-semibold">
                      {c.percentage_change != null ? formatPct(c.percentage_change) : <span className="font-normal text-warning-text">{c.unavailable_reason ?? "Not available"}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-sm">
            <span className="font-medium">Difference (B − A): </span>
            {result.difference_percentage_points != null ? (
              <span className="tabular font-semibold">
                {result.difference_percentage_points > 0 ? "+" : result.difference_percentage_points < 0 ? "−" : ""}
                {Math.abs(result.difference_percentage_points).toFixed(2)} percentage points
              </span>
            ) : (
              <span className="text-ink-2">not computed</span>
            )}
            <span className="block text-xs text-ink-3">{result.difference_note}</span>
          </p>
          {result.periods.some((p) => (p.calculation.warnings ?? []).length > 0) && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-2">
              {result.periods.flatMap((p) => (p.calculation.warnings ?? []).map((w) => <li key={`${p.label}-${w}`}>Period {p.label}: {naturalize(w)}</li>))}
            </ul>
          )}
          <p className="text-xs text-ink-3">
            Formula: (end close − start close) / start close × 100, unadjusted daily closes.
            {result.is_fixture ? " Synthetic demo data — illustrative only." : ""} This is a calculation over the selected dates, not an
            explanation of why the price moved.
          </p>
        </div>
      )}
    </>
  );
  if (bare) return body;
  return (
    <Card
      title="Compare two periods"
      description="Closing-price change for each period, computed deterministically from the first and last trading days inside it."
    >
      {body}
    </Card>
  );
}
