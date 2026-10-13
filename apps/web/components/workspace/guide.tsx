"use client";

import { useRef, useState } from "react";

import { Modal } from "../modal";
import { cn } from "../ui";

/*
 * "Explore MarketLens" (D-92): a quick guide to every feature, opened from a quiet pill at the bottom
 * right (wide screens) or the header (narrower screens). Plain words, grouped by what the user wants
 * to do, with the on-screen names of buttons and keys so each step can be tried right away.
 */

/** A button or label as it appears on screen. */
function Ui({ children }: { children: React.ReactNode }) {
  return <span className="whitespace-nowrap rounded-md border border-line bg-raised px-1.5 py-px text-[0.8em] font-medium text-ink">{children}</span>;
}

/** A keyboard key. */
function Key({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-b-2 border-line bg-raised px-1.5 py-px font-sans text-[0.8em] font-semibold text-ink">{children}</kbd>;
}

type Topic = { id: string; icon: string; title: string; intro: string; steps: React.ReactNode[] };

const TOPICS: Topic[] = [
  {
    id: "start",
    icon: "🔎",
    title: "Start a research",
    intro: "Ask about one US-listed company at a time (NYSE or Nasdaq).",
    steps: [
      <>
        Pick a company in the <Ui>Ticker ▾</Ui> field at the bottom: click it to see the company on screen, your recent tickers and the largest
        companies, or type a ticker or a name like “apple”.
      </>,
      <>
        Add a question if you like (it&apos;s optional), choose a time window such as <Ui>3 months</Ui>, then press <Ui>↑</Ui> or <Key>Enter</Key>.
      </>,
      <>
        Not sure what to ask? Click a card under <Ui>Try one of these</Ui>. It fills in the bar; nothing starts until you press <Ui>↑</Ui>.
      </>,
      <>
        <Ui>New research</Ui> (top left) clears the bar and starts over.
      </>,
    ],
  },
  {
    id: "voice",
    icon: "🎙",
    title: "Speak instead of typing",
    intro: "Two ways to use your voice, both in the research bar.",
    steps: [
      <>
        <b>Dictate:</b> press the microphone 🎙 and speak your question; the words appear in the box. Press ■ to stop. Nothing is spoken back.
      </>,
      <>
        <b>Talk to Lens:</b> press the sound-wave button 〰 to open Lens, the voice research assistant. Say a company, confirm the ticker, and Lens
        starts the research.
      </>,
      <>While Lens researches, its screen shows the progress. When the answer is ready, the screen shrinks to a small bar and Lens reads a short summary.</>,
      <>
        <Ui>✕</Ui> ends the voice session. If you can&apos;t hear Lens, press <Ui>Turn on the assistant&apos;s voice</Ui> when it appears.
      </>,
    ],
  },
  {
    id: "wait",
    icon: "⏳",
    title: "While it works",
    intro: "A research takes a few minutes with live data and AI analysis.",
    steps: [
      <>A progress card shows four steps: gathering data, specialist analysis, writing the brief and fact-checking, with a timer.</>,
      <>
        You can open other research meanwhile; the run keeps going and its status updates in the history. <Ui>Cancel</Ui> stops it.
      </>,
    ],
  },
  {
    id: "read",
    icon: "📄",
    title: "Read the brief",
    intro: "Every brief is built from dated sources and fact-checked before you see it.",
    steps: [
      <>
        <b>Heads up</b> (top) explains anything that limited the report, in plain words, with what you can do.
      </>,
      <>
        The header shows the period, the dates each kind of data describes, and a <b>fact-check meter</b>: how many statements and numbers matched
        their sources.
      </>,
      <>Key numbers, the price chart (hover for daily closes), financial results, SEC filings and dated news follow.</>,
      <>
        Findings are colour-coded: <span className="text-good-text">positive ▲</span>, <span className="text-critical-text">negative ▼</span>,{" "}
        <span className="text-warning-text">risks</span>, interpretation and scenarios. The <b>Summary</b> is at the end.
      </>,
      <>
        Anything with a small ⓘ explains itself when you hover over it, for example <Ui>◐ Partial</Ui> or <Ui>Calculation</Ui>.
      </>,
      <>
        A floating <Ui>↓</Ui> jumps to the end of a long brief.
      </>,
    ],
  },
  {
    id: "verify",
    icon: "✅",
    title: "Check where a number came from",
    intro: "Nothing in a brief is taken on trust.",
    steps: [
      <>
        Every statement has small source chips, such as <Ui>Price change</Ui> or <Ui>10-Q filing · Jul 31</Ui>. Click one to open the source panel.
      </>,
      <>The panel shows what the source says in plain words, how a calculation was done, and a link to the original document.</>,
      <>
        <b>What this brief couldn&apos;t establish</b> lists the gaps, and <b>Set aside by the fact-checker</b> shows statements that failed the
        checks, with the reason.
      </>,
    ],
  },
  {
    id: "more",
    icon: "🧰",
    title: "Do more with a research",
    intro: "Buttons at the top right of every finished research.",
    steps: [
      <>
        <Ui>Re-run</Ui> repeats the research with fresh data; the original stays as it was.
      </>,
      <>
        <Ui>Compare periods</Ui> compares the price change of two date ranges.
      </>,
      <>
        <Ui>Details</Ui> lists every step and every data request.
      </>,
      <>
        <Ui>PDF</Ui> saves the brief as a PDF (choose “Save as PDF” in the window that opens).
      </>,
      <>
        Under each findings section, <Ui>Copy</Ui> copies it as text and <Ui>Share</Ui> sends it with a link.
      </>,
    ],
  },
  {
    id: "history",
    icon: "🗂",
    title: "Find past research",
    intro: "Everything you research is kept in your MarketLens history.",
    steps: [
      <>
        The <b>Research history</b> on the left lists every run with its status; a <Ui>Voice</Ui> tag marks research Lens started.
      </>,
      <>
        Press the search icon <Ui>⌕</Ui> beside <Ui>New research</Ui> (or <Key>⌘</Key> <Key>K</Key> / <Key>Ctrl</Key> <Key>K</Key>) and type a ticker, a
        company name, or any word from a question or a finding. Suggestions appear as you type, with the matching words highlighted.
      </>,
      <>The address bar always points at the open research, so refreshing or bookmarking brings it back.</>,
    ],
  },
  {
    id: "trust",
    icon: "🛡",
    title: "What MarketLens will and won't do",
    intro: "A research assistant, not an advisor.",
    steps: [
      <>It never places trades, connects to a brokerage, or tells you to buy or sell.</>,
      <>Only the latest data is used. Missing or outdated data is left out and said so, never guessed.</>,
      <>
        The green <Ui>LIVE DATA</Ui> bar means real data; a yellow <Ui>DEMO FIXTURES</Ui> label means synthetic sample data.
      </>,
    ],
  },
  {
    id: "tips",
    icon: "⌨",
    title: "Handy tips",
    intro: "Small things that make it quicker.",
    steps: [
      <>
        <Key>Enter</Key> starts research, <Key>↑</Key> <Key>↓</Key> move through the ticker list, <Key>Esc</Key> closes lists and dialogs, and{" "}
        <Key>⌘</Key> <Key>K</Key> (<Key>Ctrl</Key> <Key>K</Key>) searches your research.
      </>,
      <>In a smaller window, search, the history and this guide move to buttons at the top right; widen the window to see the suggestions panel.</>,
      <>MarketLens follows your device&apos;s light or dark mode.</>,
    ],
  },
];

function GuideContent() {
  const [active, setActive] = useState(TOPICS[0].id);
  const container = useRef<HTMLDivElement>(null);
  const go = (id: string) => {
    setActive(id);
    container.current?.querySelector(`#guide-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <div ref={container}>
      <nav aria-label="Guide topics" className="sticky -top-5 z-10 -mx-5 -mt-5 mb-4 flex flex-wrap gap-1.5 border-b border-line bg-surface/95 px-5 py-3 backdrop-blur">
        {TOPICS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => go(t.id)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs font-medium transition",
              active === t.id ? "border-accent bg-accent-wash text-accent" : "border-line text-ink-2 hover:bg-sunken hover:text-ink",
            )}
          >
            <span aria-hidden="true">{t.icon}</span> {t.title}
          </button>
        ))}
      </nav>
      <div className="grid gap-3 sm:grid-cols-2">
        {TOPICS.map((t) => (
          <section key={t.id} id={`guide-${t.id}`} className="scroll-mt-16 rounded-2xl border border-line bg-raised p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <span aria-hidden="true" className="grid h-7 w-7 place-items-center rounded-lg bg-sunken text-base">
                {t.icon}
              </span>
              {t.title}
            </h3>
            <p className="mt-1.5 text-xs text-ink-3">{t.intro}</p>
            <ol className="mt-2.5 space-y-2 text-sm leading-relaxed text-ink-2">
              {t.steps.map((step, i) => (
                <li key={i} className="flex gap-2">
                  <span aria-hidden="true" className="mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-accent-wash text-[10px] font-semibold text-accent">
                    {i + 1}
                  </span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>
      <p className="mt-4 text-center text-xs text-ink-3">Research only. MarketLens is informational and is not financial advice.</p>
    </div>
  );
}

/** Opens the guide. `variant="pill"` for the bottom-right corner, `"header"` for the header on narrower screens. */
export function GuideButton({ variant }: { variant: "pill" | "header" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {variant === "pill" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="group flex w-full items-center gap-2.5 rounded-xl border border-line bg-surface px-3 py-2.5 text-left text-sm text-ink-2 shadow-sm transition hover:border-accent hover:text-ink"
        >
          <span aria-hidden="true" className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-accent-wash text-accent">
            ✦
          </span>
          <span className="min-w-0">
            <span className="block font-medium text-ink">Explore MarketLens</span>
            <span className="block text-xs text-ink-3">A quick guide to everything you can do</span>
          </span>
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-lg px-2 py-1.5 text-sm font-medium text-ink-2 transition hover:bg-sunken hover:text-ink sm:px-3"
        >
          <span aria-hidden="true">✦ </span>Explore
        </button>
      )}
      {open && (
        <Modal title="Explore MarketLens" description="A quick guide to everything you can do. Pick a topic, or scroll through." size="xl" onClose={() => setOpen(false)}>
          <GuideContent />
        </Modal>
      )}
    </>
  );
}
