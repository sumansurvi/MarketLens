"use client";

import { useId } from "react";

import type { ResearchRun } from "@/lib/api/types";
import { capitalize, categoryLabel, listCategories, naturalize } from "@/lib/natural";

import { Hint } from "../hint";
import { buttonClass, cn } from "../ui";
import { jumpToSection } from "./jump";

type RunError = NonNullable<ResearchRun["errors"]>[number];

export interface Issue {
  code: string;
  tone: "warning" | "critical" | "info";
  icon: string;
  title: string;
  body: string;
  tip?: string;
  categories: string[];
  /** The original code and messages, kept for the tooltip and support. */
  raw: string;
  retryable: boolean;
  jump?: { href: string; label: string };
}

const STEP_LABEL: Record<string, string> = {
  market_specialist: "market analysis",
  news_specialist: "news analysis",
  fundamentals_specialist: "financials analysis",
  supervisor_synthesis: "summary",
  evidence_review: "AI fact-check",
};

function stepList(errors: RunError[]): string {
  const names = [...new Set(errors.map((e) => (e.step_key && STEP_LABEL[e.step_key]) || "").filter(Boolean))];
  if (names.length === 0) return "one step";
  return names.length === 1 ? `the ${names[0]}` : `the ${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

interface Context {
  /** Total statements the fact-check set aside (key and supporting), from the brief's review. */
  withheld?: number;
  /** The run failed without a brief, so its errors say why nothing was written. */
  failed?: boolean;
}

function describe(code: string, errors: RunError[], context: Context): Omit<Issue, "code" | "categories" | "raw" | "retryable"> {
  const categories = [...new Set(errors.map((e) => e.category).filter((c): c is NonNullable<typeof c> => Boolean(c)))];
  const what = categories.length ? capitalize(listCategories(categories)) : "Some data";
  const messages = errors.map((e) => e.message).join(" ");
  switch (code) {
    case "RATE_LIMITED":
      if (/daily|per day/i.test(messages)) {
        return {
          tone: "warning",
          icon: "⏳",
          title: "Daily data allowance used up",
          body: `${what} couldn't be fetched: the free Alpha Vantage plan allows 25 requests a day, and today's are used.`,
          tip: "Data that was already fetched stays cached and keeps working. New requests work again tomorrow.",
        };
      }
      return {
        tone: "warning",
        icon: "⏳",
        title: "The data provider asked us to slow down",
        body: `${what} couldn't be fully fetched because the provider briefly limited requests. This is common on free plans.`,
        tip: "Wait a minute and try again. Anything already fetched is cached, so a retry needs fewer requests.",
      };
    case "PROVIDER_TIMEOUT":
      return { tone: "warning", icon: "⏱", title: "A data source was too slow", body: `${what} didn't respond in time.`, tip: "Try again in a moment." };
    case "CAPABILITY_UNAVAILABLE":
      return {
        tone: "info",
        icon: "🔒",
        title: "Not included in the current data plan",
        body: `${what} isn't available from the configured provider plan, so it's left out instead of guessed.`,
        tip: "Nothing to fix. The brief says exactly what is missing.",
      };
    case "DATA_UNAVAILABLE":
      return {
        tone: "warning",
        icon: "📭",
        title: categories.length ? `Gaps in the ${listCategories(categories)}` : "Some data is missing",
        body: naturalize(messages.replace(/^\w+:\s*/, "")),
        tip: "The brief only uses the dates and items that were actually available.",
      };
    case "PROVIDER_AUTH_FAILED":
      return {
        tone: "critical",
        icon: "🔑",
        title: "The data provider rejected the API key",
        body: `${what} couldn't be fetched with the configured key.`,
        tip: "Check ALPHA_VANTAGE_API_KEY in .env, then restart MarketLens.",
      };
    case "MALFORMED_PROVIDER_RESPONSE":
      return { tone: "warning", icon: "🧩", title: "A data source sent an unreadable response", body: `${what} couldn't be read.`, tip: "Try again later." };
    case "MCP_SERVER_UNAVAILABLE":
      return { tone: "warning", icon: "🔌", title: "A data service was restarting", body: `${what} couldn't be reached at that moment.`, tip: "Try again." };
    case "SCHEMA_VALIDATION_FAILED": {
      const key = errors.reduce((sum, e) => sum + Number(/(\d+)\s+material claim/.exec(e.message)?.[1] ?? 0), 0);
      const total = Math.max(context.withheld ?? 0, key);
      const plural = (n: number) => `${n} statement${n === 1 ? "" : "s"}`;
      const keyNote = key > 0 && total > key ? ` ${key} of them ${key === 1 ? "was" : "were"} key to the brief, so the report is marked partial.` : "";
      return {
        tone: "info",
        icon: "🛡",
        title: total > 0 ? `The fact-checker set aside ${plural(total)}` : "The fact-checker set aside some statements",
        body: `${total === 1 ? "It" : "They"} couldn't be matched to the sources, so ${total === 1 ? "it isn't" : "they aren't"} shown as findings.${keyNote}`,
        tip: "That's the safety net working. A larger AI model usually produces fewer of these.",
        jump: { href: "#withheld", label: "See what was set aside" },
      };
    }
    case "LLM_UNAVAILABLE":
      // Live runs stop at a failed model step (D-104): the reason is the provider's own.
      if (context.failed) {
        return {
          tone: "warning",
          icon: "🤖",
          title: "The AI model couldn't finish, so no brief was written",
          body: naturalize(messages),
          tip: "Press Re-run to try again. The reason comes from the model provider.",
        };
      }
      return {
        tone: "warning",
        icon: "🤖",
        title: "The AI model couldn't finish a step",
        body: `For ${stepList(errors)}, the brief uses a plain summary built directly from the data instead.`,
        tip: "Local models can be slow or miss the required format. Try again, or use a larger model.",
      };
    case "BUDGET_EXCEEDED":
      return {
        tone: "warning",
        icon: "⏱",
        title: "The run ran out of time",
        body: `${capitalize(stepList(errors))} couldn't start before the time budget ran out, so it was summarized from the data instead.`,
        tip: "Slow local models need more time: raise RUN_TIMEOUT_SECONDS in .env.",
      };
    case "STALE_DATA":
      return { tone: "warning", icon: "🕰", title: "Some data is out of date", body: naturalize(messages), tip: "Check the as-of dates on each section." };
    case "CONTRADICTORY_EVIDENCE":
      return { tone: "warning", icon: "⚖", title: "Sources disagree", body: naturalize(messages), tip: "Both sources are cited; the affected statements are marked." };
    case "RESEARCH_NOT_STARTED":
      return {
        tone: "warning",
        icon: "⏳",
        title: "The research service didn't pick the run up",
        body: "The request was accepted, but no worker started it in time, so nothing was researched.",
        tip: "Press Re-run to try again.",
      };
    case "RESEARCH_TIMED_OUT":
      return {
        tone: "warning",
        icon: "⏱",
        title: "The run ran out of time",
        body: "It was stopped before it could finish.",
        tip: "Press Re-run to try again.",
      };
    case "RESEARCH_QUEUE_UNAVAILABLE":
      return {
        tone: "warning",
        icon: "🔌",
        title: "The run couldn't be started",
        body: "The research service didn't accept the request, so nothing was researched.",
        tip: "Press Re-run in a moment.",
      };
    case "RESEARCH_STOPPED":
      return {
        tone: "warning",
        icon: "⚠",
        title: "The research service stopped unexpectedly",
        body: "The run was stopped before it could finish.",
        tip: "Press Re-run to try again.",
      };
    default:
      // Runs that failed before these reasons existed still say what happened, not a vague limit.
      if (context.failed) {
        return {
          tone: "warning",
          icon: "⚠",
          title: "The research stopped before it finished",
          // Older messages end with "Try again.", which the tip below already says.
          body: naturalize(messages).replace(/\s*Try again\.?$/i, ""),
          tip: "Press Re-run to try again.",
        };
      }
      return { tone: "warning", icon: "⚠", title: "Something limited this report", body: naturalize(messages) };
  }
}

/** Group run errors by cause and translate them into plain language. */
export function describeIssues(errors: RunError[] | null | undefined, context: Context = {}): Issue[] {
  const groups = new Map<string, RunError[]>();
  for (const error of errors ?? []) {
    groups.set(error.code, [...(groups.get(error.code) ?? []), error]);
  }
  return [...groups.entries()].map(([code, group]) => ({
    code,
    categories: [...new Set(group.map((e) => e.category).filter((c): c is NonNullable<typeof c> => Boolean(c)))],
    raw: group.map((e) => `${e.code} — ${e.message}`).join("\n"),
    retryable: group.some((e) => e.retryable),
    ...describe(code, group, context),
  }));
}

const ICON_STYLE: Record<Issue["tone"], string> = {
  warning: "bg-warning/20",
  critical: "bg-critical/15",
  info: "bg-accent-wash",
};

export function HeadsUp({
  issues,
  failed,
  onRetry,
  retrying,
}: {
  issues: Issue[];
  failed?: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const titleId = useId();
  if (issues.length === 0) return null;
  const count = issues.length;
  return (
    <section
      aria-labelledby={titleId}
      className={cn(
        "rounded-2xl border p-4 sm:p-5",
        failed ? "border-critical/40 bg-critical/[0.06]" : "border-warning/50 bg-warning/[0.08]",
      )}
      data-testid="heads-up"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className={cn(
              "grid h-7 w-7 place-items-center rounded-full text-sm font-bold",
              failed ? "bg-critical/20 text-critical-text" : "bg-warning/30 text-warning-text",
            )}
          >
            !
          </span>
          <h2 id={titleId} className="font-semibold tracking-tight text-ink">
            {failed ? "This research run couldn't finish" : `Heads up: ${count === 1 ? "one thing" : `${count} things`} affected this report`}
          </h2>
        </div>
        {onRetry && issues.some((i) => i.retryable) && (
          <button type="button" className={buttonClass.secondarySm} onClick={onRetry} disabled={retrying}>
            {retrying ? "Starting…" : "Try again"}
          </button>
        )}
      </div>
      <ul className="mt-4 grid gap-3 md:grid-cols-2">
        {issues.map((issue) => (
          <li key={issue.code} className="flex gap-3 rounded-xl border border-line bg-surface p-4">
            <span aria-hidden="true" className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-xl text-base", ICON_STYLE[issue.tone])}>
              {issue.icon}
            </span>
            <div className="min-w-0 space-y-1">
              <p className="font-medium leading-snug text-ink">{issue.title}</p>
              <p className="text-sm leading-relaxed text-ink-2">{issue.body}</p>
              {issue.categories.length > 0 && (
                <p className="flex flex-wrap gap-1 pt-0.5">
                  {issue.categories.map((c) => (
                    <Hint
                      key={c}
                      text={`Data this problem affected: ${categoryLabel(c).toLowerCase()}.`}
                      className="rounded-full border border-line bg-raised px-2 py-0.5 text-[11px] font-medium text-ink-2"
                    >
                      {categoryLabel(c)}
                    </Hint>
                  ))}
                </p>
              )}
              {issue.tip && (
                <p className="pt-1 text-xs leading-relaxed text-ink-3">
                  <span className="font-semibold text-ink-2">What you can do: </span>
                  {issue.tip}
                </p>
              )}
              {issue.jump && (
                <a
                  href={issue.jump.href}
                  onClick={(e) => jumpToSection(e, issue.jump?.href.slice(1) ?? "")}
                  className="inline-block pt-0.5 text-xs font-medium text-accent hover:underline"
                >
                  {issue.jump.label} ↓
                </a>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
