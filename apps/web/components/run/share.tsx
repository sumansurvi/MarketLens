"use client";

import { useEffect, useState } from "react";

import type { ResearchBrief, ResearchClaim } from "@/lib/api/types";
import { naturalize, niceDate, niceRange } from "@/lib/natural";

import { cn } from "../ui";

/*
 * Sharing a brief (D-87, D-91): a PDF button for the whole research at the top right of a run, and a
 * Copy / Share pair under each answer section. Shared text is readable prose (no IDs or raw
 * values). Links open the run in MarketLens, which runs on this computer, so they work where
 * MarketLens is running; the copied text works anywhere.
 */

export function runLink(runId: string): string {
  return typeof window === "undefined" ? `/?run=${runId}` : `${window.location.origin}/?run=${encodeURIComponent(runId)}`;
}

function heading(brief: ResearchBrief): string {
  return `${brief.company.company_name ?? brief.company.symbol} (${brief.company.symbol}) · ${niceRange(brief.time_horizon.start_date, brief.time_horizon.end_date)}`;
}

function bullets(claims: ResearchClaim[]): string {
  return claims.map((c) => `• ${naturalize(c.text)}`).join("\n");
}

export function sectionAsText(brief: ResearchBrief, title: string, claims: ResearchClaim[]): string {
  return `${title} — ${heading(brief)}\n\n${bullets(claims)}\n\nResearch only; not financial advice.`;
}

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Native share sheet where the browser has one; otherwise the text (and link) is copied. */
async function share(title: string, text: string, url: string): Promise<"shared" | "copied" | "failed"> {
  if (typeof navigator.share === "function") {
    try {
      await navigator.share({ title, text, url });
      return "shared";
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return "shared"; // the person closed the sheet
    }
  }
  return (await copy(`${text}\n\n${url}`)) ? "copied" : "failed";
}

function useFlash(): [string | null, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 2500);
    return () => clearTimeout(timer);
  }, [message]);
  return [message, setMessage];
}

function ShareIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d="M12 3v12" />
      <path d="m7 8 5-5 5 5" />
      <path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6" />
    </svg>
  );
}

function CopyIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}

/**
 * "PDF" at the top right of a run (D-91): the brief alone, laid out for paper (light colours, no
 * rails, bar or buttons, folded sections opened), through the browser's Save as PDF. The page title
 * becomes the default file name, e.g. "MarketLens - AAPL research - Oct 12, 2026".
 */
export function PdfButton({ brief }: { brief: ResearchBrief }) {
  const save = () => {
    const previousTitle = document.title;
    const opened: HTMLDetailsElement[] = [];
    document.querySelectorAll<HTMLDetailsElement>("[data-testid=brief-scroll] details:not([open])").forEach((d) => {
      d.open = true;
      opened.push(d);
    });
    document.title = `MarketLens - ${brief.company.symbol} research - ${niceDate(brief.time_horizon.end_date)}`;
    const restore = () => {
      document.title = previousTitle;
      opened.forEach((d) => {
        d.open = false;
      });
      window.removeEventListener("afterprint", restore);
    };
    window.addEventListener("afterprint", restore);
    window.print();
  };
  return (
    <button
      type="button"
      onClick={save}
      title="Save this brief as a PDF (in the window that opens, choose “Save as PDF”)"
      className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-raised px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-sunken"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4">
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
        <path d="M14 3v5h5M12 11v6M9.5 14.5 12 17l2.5-2.5" />
      </svg>
      PDF
    </button>
  );
}

/** "Copy" and "Share" under one answer section. */
export function SectionActions({ brief, title, claims }: { brief: ResearchBrief; title: string; claims: ResearchClaim[] }) {
  const [flash, setFlash] = useFlash();
  if (claims.length === 0) return null;
  const text = sectionAsText(brief, title, claims);
  const button = "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-ink-3 transition hover:bg-sunken hover:text-ink";
  return (
    <div className="flex items-center justify-end gap-1 border-t border-hairline pt-2 print:hidden">
      {flash && (
        <span role="status" className="mr-auto text-xs text-good-text">
          {flash}
        </span>
      )}
      <button type="button" className={button} aria-label={`Copy ${title}`} onClick={async () => setFlash((await copy(text)) ? "Copied" : "Couldn't copy")}>
        <CopyIcon className="h-3.5 w-3.5" />
        Copy
      </button>
      <button
        type="button"
        className={cn(button)}
        aria-label={`Share ${title}`}
        onClick={async () => {
          const result = await share(`${title}: ${brief.company.symbol}`, text, runLink(brief.run_id));
          setFlash(result === "copied" ? "Copied with link, ready to paste" : result === "failed" ? "Couldn't share" : "");
        }}
      >
        <ShareIcon className="h-3.5 w-3.5" />
        Share
      </button>
    </div>
  );
}
