"use client";

import { cn } from "../ui";
import { useWorkspace } from "./workspace-context";

export const QUICK_STARTS = [
  {
    symbol: "AAPL",
    title: "Explain recent movement",
    question:
      "Research AAPL over the last 3 months; explain notable movement; review recent news and latest available filings; list risks and unknowns.",
  },
  {
    symbol: "NVDA",
    title: "What changed recently?",
    question: "Give me a research briefing on Nvidia and explain what changed recently.",
  },
  {
    symbol: "MSFT",
    title: "Fundamentals and gaps",
    question: "Review Microsoft's latest reported fundamentals and filings, and state clearly what evidence is missing.",
  },
];

export const QUICK_START_NOTE = "Fills in the research bar; nothing runs until you press ↑ or Enter.";

/** One quick-start card. Picking it fills the research bar without starting anything. */
export function QuickStart({ item, className }: { item: (typeof QUICK_STARTS)[number]; className?: string }) {
  const ws = useWorkspace();
  return (
    <button
      type="button"
      onClick={() => ws.prefill(item.symbol, item.question)}
      className={cn(
        "flex h-full w-full flex-col items-start rounded-2xl border border-line bg-surface p-3.5 text-left transition hover:border-accent hover:shadow-md",
        className,
      )}
    >
      <span className="rounded-full bg-sunken px-2 py-0.5 font-mono text-xs font-bold text-ink-2">{item.symbol}</span>
      <span className="mt-2 block text-sm font-medium text-ink">{item.title}</span>
      <span className="mt-1 block text-xs leading-relaxed text-ink-3">{item.question}</span>
    </button>
  );
}

/** The right-hand rail, mirroring the research history on the left (wide screens only). */
export function SuggestionsRail() {
  return (
    <section aria-label="Try one of these" className="px-2 py-4">
      <h2 className="mb-1.5 px-2 text-[11px] font-semibold uppercase tracking-wider text-ink-3">Try one of these</h2>
      <ul className="space-y-2 px-1">
        {QUICK_STARTS.map((item) => (
          <li key={item.title}>
            <QuickStart item={item} />
          </li>
        ))}
      </ul>
      <p className="mt-2 px-2 text-xs text-ink-3">{QUICK_START_NOTE}</p>
    </section>
  );
}
