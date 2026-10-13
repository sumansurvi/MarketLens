"use client";

import { useRef } from "react";

import { ScrollToBottom } from "../scroll-to-bottom";
import { useStatus } from "../status-provider";
import { Notice } from "../ui";
import { QUICK_START_NOTE, QUICK_STARTS, QuickStart } from "./suggestions";

const STEPS = [
  { icon: "📥", title: "Gather", text: "Prices, SEC filings and dated news, fetched through three MCP servers." },
  { icon: "🧠", title: "Analyze", text: "Market, news and financials specialists draft findings; a supervisor writes the brief." },
  { icon: "🛡", title: "Fact-check", text: "Every number and date is checked against its source. Anything unsupported is set aside." },
];

/** What the main pane shows before a run is opened. */
export function Welcome() {
  const { health } = useStatus();
  const demo = health?.data_mode === "fixtures";
  const scroller = useRef<HTMLDivElement>(null);
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-10 sm:py-14">
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">Evidence-grounded equity research</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">Research a US-listed company, with every claim checked.</h1>
          <p className="mt-3 max-w-2xl text-base leading-relaxed text-ink-2">
            Type a ticker in the bar below and ask what you want to know. Press the microphone to dictate your question{health?.voice_enabled ? ", or the sound-wave button to talk with Lens, the voice research assistant" : ""}.
            MarketLens gathers the evidence, has specialist agents analyze it, and shows you what it found, what it couldn&apos;t find, and where
            every number came from.
          </p>
          {health?.warnings?.map((warning) => (
            <div key={warning} className="mt-4">
              <Notice tone="warning" title="Exposure warning">
                {warning}
              </Notice>
            </div>
          ))}
          {demo && (
            <div className="mt-5">
              <Notice title="Demo mode">
                Synthetic data for AAPL, MSFT and NVDA, dated through Sep 30, 2026. It is labeled everywhere and is never current market data.
              </Notice>
            </div>
          )}

          {/* On wide screens these live in the right-hand rail instead. */}
          <div className="xl:hidden">
            <h2 className="mt-10 text-sm font-semibold text-ink">Try one of these</h2>
            <ul className="mt-3 grid gap-3 sm:grid-cols-3">
              {QUICK_STARTS.map((item) => (
                <li key={item.title}>
                  <QuickStart item={item} />
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-ink-3">{QUICK_START_NOTE}</p>
          </div>

          <h2 className="mt-10 text-sm font-semibold text-ink">How it works</h2>
          <ol className="mt-3 grid gap-3 sm:grid-cols-3">
            {STEPS.map((step, i) => (
              <li key={step.title} className="rounded-2xl border border-line bg-surface p-4">
                <span aria-hidden="true" className="text-xl">
                  {step.icon}
                </span>
                <p className="mt-2 font-medium text-ink">
                  {i + 1}. {step.title}
                </p>
                <p className="mt-1 text-sm leading-relaxed text-ink-2">{step.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <ScrollToBottom scroller={scroller} />
    </div>
  );
}
