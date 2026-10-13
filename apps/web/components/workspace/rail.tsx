"use client";

import { useCallback } from "react";

import type { RunList, RunStatus } from "@/lib/api/types";
import { niceTimestamp, timeAgo } from "@/lib/natural";
import { usePolling } from "@/lib/use-api";

import { SearchIcon } from "../icons";
import { cn, Notice, Spinner } from "../ui";
import { STATUS_DOT } from "./run-status";
import { useSearchShortcut } from "./search";
import { useWorkspace } from "./workspace-context";

const TERMINAL = new Set<RunStatus>(["completed", "partial", "failed", "cancelled"]);

function Heading({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="mb-1.5 flex items-center justify-between px-2">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-ink-3">{title}</h2>
      {action}
    </div>
  );
}

/** Starts a new research: back to an empty research bar and the welcome screen. */
function NewResearchButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl border border-line bg-raised px-3 py-2.5 text-sm font-medium text-ink shadow-sm transition hover:border-accent hover:text-accent"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-[18px] w-[18px]">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </svg>
      New research
    </button>
  );
}

/** Opens "Search research" (D-96), like ⌘K / Ctrl+K. */
function SearchButton({ onClick }: { onClick: () => void }) {
  const shortcut = useSearchShortcut();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Search research"
      aria-keyshortcuts="Meta+K Control+K"
      title={`Search research (${shortcut})`}
      data-testid="search-research"
      className="grid w-[42px] shrink-0 place-items-center rounded-xl border border-line bg-raised text-ink shadow-sm transition hover:border-accent hover:text-accent"
    >
      <SearchIcon />
    </button>
  );
}

function History() {
  const ws = useWorkspace();
  const isDone = useCallback((d: RunList) => d.runs.every((r) => TERMINAL.has(r.status)), []);
  // Refetched when the workspace changes, and polled while any listed run is still going.
  const { data, error, loading } = usePolling<RunList>(`/api/research?limit=15&v=${ws.version}`, isDone, 3000);
  const activeId = ws.view?.kind === "run" ? ws.view.id : null;
  return (
    <section aria-label="Research history">
      <div className="mb-3 flex gap-2">
        <NewResearchButton onClick={ws.goHome} />
        <SearchButton onClick={ws.openSearch} />
      </div>
      <Heading title="Research history" />
      {loading && !data && <Spinner label="Loading…" />}
      {error && !data && <Notice tone="warning">{error.message}</Notice>}
      {data && data.runs.length === 0 && <p className="px-2 text-xs text-ink-3">Your research runs will appear here.</p>}
      {data && data.runs.length > 0 && (
        <ul className="space-y-0.5">
          {data.runs.map((run) => {
            const status = STATUS_DOT[run.status];
            const active = run.run_id === activeId;
            return (
              <li key={run.run_id}>
                <button
                  type="button"
                  onClick={() => ws.openRun(run.run_id)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left transition",
                    active ? "bg-accent-wash ring-1 ring-accent/30" : "hover:bg-sunken",
                  )}
                >
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-sm font-bold text-ink">{run.symbol}</span>
                    <span className="inline-flex items-center gap-1 text-[11px] text-ink-3">
                      <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", status.dot)} />
                      {status.label}
                    </span>
                    {run.data_mode === "fixture" && <span className="rounded bg-demo-bg px-1 text-[10px] font-semibold text-demo-text">DEMO</span>}
                    {run.origin === "voice" && (
                      <span className="inline-flex items-center gap-0.5 rounded bg-accent-wash px-1 text-[10px] font-semibold text-accent">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true" className="h-2.5 w-2.5">
                          <rect x="9" y="3" width="6" height="11" rx="3" />
                          <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                        </svg>
                        Voice
                      </span>
                    )}
                    <span className="ml-auto text-[11px] text-ink-3" title={niceTimestamp(run.created_at)}>
                      {timeAgo(run.created_at)}
                    </span>
                  </span>
                  <span className="mt-0.5 block break-words text-xs leading-snug text-ink-2">{run.question}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** The workspace side rail: research history (voice lives in the research bar). */
export function Rail() {
  return (
    <div className="px-2 py-4">
      <History />
    </div>
  );
}
