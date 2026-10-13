// Turns evidence into words for the screen (D-86): every source gets its own name (no two chips
// read "Formula"), and its details are described in plain language with formatted values. No
// internal field names, IDs, hashes or code-style formulas are shown.

import type { EvidenceItem } from "@/lib/api/types";

import { formatCompactUsd, formatInt, formatPct, formatUsd } from "./format";
import { niceDate, niceRange } from "./natural";

type Payload = Record<string, unknown>;

const PROVIDER_NAME: Record<string, string> = {
  alpha_vantage: "Alpha Vantage",
  sec_edgar: "SEC EDGAR",
  yahoo_finance: "Yahoo Finance",
  marketlens: "Calculated by MarketLens (code, not AI)",
};

export function providerName(id: string | null | undefined): string {
  if (!id) return "—";
  return PROVIDER_NAME[id] ?? id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const METRIC_NAME: Record<string, string> = {
  revenue: "Revenue",
  gross_profit: "Gross profit",
  operating_income: "Operating income",
  net_income: "Net income",
  ebitda: "EBITDA",
  reported_eps: "Earnings per share",
  diluted_eps: "Diluted earnings per share",
};

function metricName(key: unknown, fallback?: string | null): string {
  return (typeof key === "string" && METRIC_NAME[key]) || fallback || "Financial figure";
}

function num(value: unknown): number | null {
  const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(n) ? n : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** A value in its unit: "$82.89B", "$3.45 per share", "7.43B shares". */
export function formatAmount(value: unknown, unit: string | null | undefined): string {
  const n = num(value);
  if (n === null) return "—";
  const u = (unit ?? "").toLowerCase();
  if (u.includes("/share")) return `${formatUsd(n)} per share`;
  if (u === "shares") return n >= 1e9 ? `${(n / 1e9).toFixed(2)}B shares` : `${formatInt(n)} shares`;
  if (u.startsWith("usd") || u === "") return Math.abs(n) >= 1e6 ? formatCompactUsd(n) : formatUsd(n);
  return formatInt(n);
}

export function unitName(currency: string | null | undefined, units: string | null | undefined): string {
  const u = (units ?? "").toLowerCase();
  if (u.includes("/share")) return "US dollars per share";
  if (u === "shares") return "Shares";
  if (u.includes("percent")) return "Percent";
  if (currency === "USD" || u.startsWith("usd")) return "US dollars";
  return units || currency || "—";
}

const FLAG_TEXT: Record<string, string> = {
  suspected_prompt_injection: "Contains instruction-like text; it was ignored and kept away from the AI",
  truncated: "Shortened to a short excerpt",
};

export function flagText(flag: string): string {
  return FLAG_TEXT[flag] ?? flag.replace(/_/g, " ");
}

type CalcKind = "price_change" | "daily_drop" | "daily_rise" | "range" | "growth" | "margin" | "other";

function calcKind(p: Payload): CalcKind {
  if ("absolute_change" in p || "requested_start" in p) return "price_change";
  if ("previous_close" in p && "close" in p) return (num(p.percentage_change) ?? 0) < 0 ? "daily_drop" : "daily_rise";
  if ("low_close" in p && "high_close" in p) return "range";
  if ("prior_value" in p && "latest_value" in p) return "growth";
  if ("margin_pct" in p) return "margin";
  return "other";
}

function periodWord(item: EvidenceItem, p: Payload): string {
  const type = str(p.period_type) ?? (/annual/i.test(item.title ?? "") ? "annual" : /quarter/i.test(item.title ?? "") ? "quarterly" : null);
  return type === "annual" ? "year" : type === "quarterly" ? "quarter" : "";
}

/** A short, distinct name for a source ("Price change", "Revenue growth (quarter)", "10-Q filing · Aug 1, 2026"). */
export function sourceName(item: EvidenceItem | undefined): string {
  if (!item) return "Source";
  const p = (item.payload ?? {}) as Payload;
  switch (item.kind) {
    case "calculation": {
      const kind = calcKind(p);
      if (kind === "price_change") return "Price change";
      if (kind === "daily_drop") return "Largest daily drop";
      if (kind === "daily_rise") return "Largest daily rise";
      if (kind === "range") return "Price range";
      if (kind === "margin") return "Operating margin";
      if (kind === "growth") {
        const word = /annual/i.test(item.title ?? "") ? "year" : "quarter";
        return `${metricName(p.metric)} growth (${word})`;
      }
      return (item.title ?? "Calculation").split(/\s\d{4}-/)[0];
    }
    case "financial_metric": {
      const word = periodWord(item, p);
      const name = metricName(p.metric_name, (item.title ?? "").split(" — ")[0]);
      return `${name}${word ? ` (${word})` : ""}${item.period_end ? ` · ${niceDate(item.period_end)}` : ""}`;
    }
    case "filing":
      return `${str(p.form) ?? "SEC"} filing${item.published_date ? ` · ${niceDate(item.published_date)}` : ""}`;
    case "news": {
      const publisher = str(p.publisher) ?? item.source_name;
      return `${publisher ? `${publisher} story` : "News story"}${item.published_date ? ` · ${niceDate(item.published_date)}` : ""}`;
    }
    case "quote":
      return `Latest quote${str(p.trading_date) ? ` · ${niceDate(str(p.trading_date))}` : ""}`;
    case "price_series":
      return "Daily prices";
    default:
      return (item.title ?? "Source").slice(0, 40);
  }
}

export interface Described {
  /** One plain sentence on how the value was produced (calculations only). */
  method?: string;
  rows: [string, string][];
  table?: { head: [string, string, string]; rows: [string, string, string][]; note?: string };
}

/** The source's values in plain words, formatted for reading. */
export function describeSource(item: EvidenceItem): Described {
  const p = (item.payload ?? {}) as Payload;
  switch (item.kind) {
    case "calculation": {
      const kind = calcKind(p);
      if (kind === "price_change")
        return {
          method: "Change = (last close − first close) ÷ first close × 100, using the first and last trading days inside the window.",
          rows: [
            ["First close", `${formatUsd(num(p.start_value))} on ${niceDate(str(p.start_date))}`],
            ["Last close", `${formatUsd(num(p.end_value))} on ${niceDate(str(p.end_date))}`],
            ["Change", `${formatUsd(num(p.absolute_change))} (${formatPct(num(p.percentage_change))})`],
            ["Window asked for", niceRange(str(p.requested_start), str(p.requested_end))],
          ],
        };
      if (kind === "daily_drop" || kind === "daily_rise")
        return {
          method: `The biggest one-day ${kind === "daily_drop" ? "fall" : "rise"} in the window: (close − previous trading day's close) ÷ previous close × 100.`,
          rows: [
            ["Previous close", `${formatUsd(num(p.previous_close))} on ${niceDate(str(p.previous_date))}`],
            ["Close", `${formatUsd(num(p.close))} on ${niceDate(str(p.trading_date))}`],
            ["Move", formatPct(num(p.percentage_change))],
          ],
        };
      if (kind === "range")
        return {
          method: "The lowest and highest closing price over the trading days in the window.",
          rows: [
            ["Lowest close", `${formatUsd(num(p.low_close))} on ${niceDate(str(p.low_date))}`],
            ["Highest close", `${formatUsd(num(p.high_close))} on ${niceDate(str(p.high_date))}`],
          ],
        };
      if (kind === "growth") {
        const unit = p.metric === "reported_eps" || p.metric === "diluted_eps" ? "USD/shares" : "USD";
        return {
          method: "Growth = (latest − same period a year earlier) ÷ year-earlier value × 100.",
          rows: [
            [`Latest (${niceRange(str(p.latest_period_start), str(p.latest_period_end))})`, formatAmount(p.latest_value, unit)],
            [`A year earlier (${niceRange(str(p.prior_period_start), str(p.prior_period_end))})`, formatAmount(p.prior_value, unit)],
            ["Growth", formatPct(num(p.percentage_change))],
          ],
        };
      }
      if (kind === "margin")
        return {
          method: "Operating margin = operating income ÷ revenue × 100, for the same quarter.",
          rows: [
            ["Quarter", niceRange(str(p.period_start), str(p.period_end))],
            ["Operating income", formatAmount(p.operating_income, "USD")],
            ["Revenue", formatAmount(p.revenue, "USD")],
            ["Margin", formatPct(num(p.margin_pct), false)],
          ],
        };
      return { rows: [] };
    }
    case "price_series": {
      const bars = (p.bars as { trading_date: string; close: number }[] | undefined) ?? [];
      const first = bars[0];
      const last = bars[bars.length - 1];
      return {
        rows: [
          ["Trading days", formatInt(num(p.bar_count))],
          ["First close", first ? `${formatUsd(first.close)} on ${niceDate(first.trading_date)}` : "—"],
          ["Last close", last ? `${formatUsd(last.close)} on ${niceDate(last.trading_date)}` : "—"],
          ["Adjusted", p.adjustment === "adjusted" ? "Yes, for splits and dividends" : "No: actual closing prices as traded"],
        ],
      };
    }
    case "quote":
      return {
        rows: [
          ["Price", `${formatUsd(num(p.price))} on ${niceDate(str(p.trading_date))}`],
          ["Previous close", formatUsd(num(p.previous_close))],
          ["Volume", `${formatInt(num(p.volume))} shares`],
        ],
      };
    case "financial_metric": {
      const values = (p.values as Payload[] | undefined) ?? [];
      const unit = str(p.unit) ?? item.units;
      return {
        rows: [],
        table: {
          head: ["Period", "Value", "Reported in"],
          rows: values.map((v) => [
            niceRange(str(v.start), str(v.end ?? v.period_end)) || niceDate(str(v.end ?? v.period_end)),
            formatAmount(v.value, unit),
            [str(v.form), str(v.filed) ? `filed ${niceDate(str(v.filed))}` : null].filter(Boolean).join(", ") || "—",
          ]),
          note: item.provider === "sec_edgar" ? "As reported to the SEC in the company's own filings." : "As normalized by the data provider.",
        },
      };
    }
    case "filing":
      return {
        rows: [
          ["Form", str(p.form) ?? "—"],
          ["Filed", niceDate(str(p.filed_date))],
          ["Report period", str(p.report_date) ? niceDate(str(p.report_date)) : "—"],
        ],
      };
    case "news":
      return { rows: [["Publisher", str(p.publisher) ?? item.source_name ?? "—"]] };
    default:
      return { rows: [] };
  }
}
