"use client";

import { useRef } from "react";

import type { ResearchBrief } from "@/lib/api/types";
import { naturalize, niceDate } from "@/lib/natural";

import { ScrollToBottom } from "../scroll-to-bottom";
import { cn } from "../ui";

/**
 * Main-pane layout for a brief: a frozen summary bar, the scrolling brief with a floating
 * scroll-to-bottom button, and the evidence inspector docked beside it. Only the brief scrolls;
 * the bar and the research bar stay put.
 */
export function BriefFrame({ bar, children, side }: { bar: React.ReactNode; children: React.ReactNode; side?: React.ReactNode }) {
  const scroller = useRef<HTMLDivElement>(null);
  return (
    <div className="flex min-h-0 flex-1 print:block">
      <div className="relative flex min-w-0 flex-1 flex-col print:block">
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto print:overflow-visible" data-testid="brief-scroll">
          <p className="hidden px-6 pb-2 text-[10px] text-ink-3 print:block">MarketLens research brief · research only, not financial advice</p>
          <div data-sticky-bar className="sticky top-0 z-20 border-b border-line bg-plane/85 backdrop-blur-md print:static print:bg-transparent print:backdrop-blur-none">
            {bar}
          </div>
          <div className="mx-auto max-w-4xl space-y-6 px-4 pb-16 pt-6 sm:px-6 print:max-w-none print:pb-0">{children}</div>
        </div>
        <ScrollToBottom scroller={scroller} />
      </div>
      {side}
    </div>
  );
}

/** The price and price change from the brief's key numbers, if they exist. */
function headline(brief: ResearchBrief | null | undefined) {
  const metrics = brief?.key_metrics ?? [];
  const price = metrics.find((m) => /last close|latest provider quote/i.test(m.label));
  const change = metrics.find((m) => /price change/i.test(m.label));
  return { price, change };
}

export function Headline({ brief }: { brief: ResearchBrief | null | undefined }) {
  const { price, change } = headline(brief);
  if (!price && !change) return null;
  const up = change?.display_value.startsWith("+");
  const down = change ? /^[−-]/.test(change.display_value) : false;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2.5">
      {price && (
        <span className="text-lg font-semibold tracking-tight tabular text-ink" title={`${price.label}, ${naturalize(price.as_of)}`}>
          {price.display_value}
        </span>
      )}
      {change && (
        <span className={cn("text-sm font-semibold tabular", up && "text-good-text", down && "text-critical-text")}>
          <span aria-hidden="true">{up ? "▲ " : down ? "▼ " : ""}</span>
          {change.display_value}
          <span className="ml-1.5 font-normal text-ink-3">{naturalize(change.as_of)}</span>
        </span>
      )}
      {price && !change && <span className="text-xs text-ink-3">as of {niceDate(price.as_of)}</span>}
    </span>
  );
}

/** Frozen bar at the top of a brief: identity, status, key price, the full question and actions. */
export function SummaryBar({
  symbol,
  company,
  badges,
  actions,
  headline,
  question,
}: {
  symbol: string;
  company?: string | null;
  badges?: React.ReactNode;
  actions?: React.ReactNode;
  headline?: React.ReactNode;
  question?: string | null;
}) {
  return (
    <div className="mx-auto max-w-4xl px-4 py-3 sm:px-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="flex min-w-0 items-baseline gap-2">
          <span className="font-mono text-xl font-bold tracking-tight text-ink">{symbol}</span>
          {company && <span className="truncate text-sm font-medium text-ink-2">{company}</span>}
        </h1>
        <span className="flex flex-wrap items-center gap-1.5">{badges}</span>
        <div className="ml-auto flex flex-wrap items-center gap-2 print:hidden">{actions}</div>
      </div>
      <div className="mt-1.5 flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1">
        {headline}
        {question && (
          <p className="min-w-[16rem] flex-1 break-words text-sm leading-snug text-ink-3">“{question}”</p>
        )}
      </div>
    </div>
  );
}
