"use client";

import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api/client";
import type { ResearchRun, RunList, RunSearch } from "@/lib/api/types";
import { niceTimestamp, timeAgo } from "@/lib/natural";
import { useApi } from "@/lib/use-api";

import { SearchIcon } from "../icons";
import { Modal } from "../modal";
import { cn } from "../ui";
import { STATUS_DOT } from "./run-status";
import { useWorkspace } from "./workspace-context";

/*
 * Search research (D-96): the search icon beside New research, or ⌘K / Ctrl+K, opens this modal.
 * Suggestions appear as you type: past research whose ticker, company name, question or findings
 * contain every word, best matches first, with the words highlighted and, for a match inside the
 * findings, the sentence it came from. An empty box suggests recent research and the tickers
 * researched so far.
 */

const SEARCH_DELAY_MS = 180;
const MAX_RECENT = 6;
const MAX_TICKERS = 8;

const noSubscription = () => () => {};

/** "⌘K" on a Mac, "Ctrl K" elsewhere (the server renders "Ctrl K"; React swaps it after hydration). */
export function useSearchShortcut(): string {
  return useSyncExternalStore(
    noSubscription,
    () => (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? "⌘K" : "Ctrl K"),
    () => "Ctrl K",
  );
}

/** Lower-cased words of the query, as the backend splits them. */
function termsOf(query: string): string[] {
  return [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))].slice(0, 6);
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The text with every search word marked. */
function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (terms.length === 0) return <>{text}</>;
  const pattern = new RegExp(`(${[...terms].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|")})`, "gi");
  return (
    <>
      {text.split(pattern).map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-sm bg-accent-wash px-0.5 text-ink">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

function ResultRow({
  run,
  snippet,
  terms,
  id,
  active,
  onOpen,
  onHover,
}: {
  run: ResearchRun;
  snippet?: string | null;
  terms: string[];
  id: string;
  active: boolean;
  onOpen: () => void;
  onHover: () => void;
}) {
  const status = STATUS_DOT[run.status];
  return (
    <li
      id={id}
      role="option"
      aria-selected={active}
      onMouseDown={(event) => event.preventDefault()} // keep focus in the search box
      onClick={onOpen}
      onMouseMove={onHover}
      className={cn("cursor-pointer rounded-xl px-3 py-2.5 transition", active ? "bg-accent-wash ring-1 ring-accent/30" : "hover:bg-sunken")}
    >
      <span className="flex items-center gap-2">
        <span className="font-mono text-sm font-bold text-ink">
          <Highlight text={run.symbol} terms={terms} />
        </span>
        {run.company?.company_name && (
          <span className="min-w-0 truncate text-xs text-ink-2">
            <Highlight text={run.company.company_name} terms={terms} />
          </span>
        )}
        <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-ink-3">
          <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", status.dot)} />
          {status.label}
        </span>
        {run.data_mode === "fixture" && <span className="shrink-0 rounded bg-demo-bg px-1 text-[10px] font-semibold text-demo-text">DEMO</span>}
        {run.origin === "voice" && <span className="shrink-0 rounded bg-accent-wash px-1 text-[10px] font-semibold text-accent">Voice</span>}
        <span className="ml-auto shrink-0 text-[11px] text-ink-3" title={niceTimestamp(run.created_at)}>
          {timeAgo(run.created_at)}
        </span>
      </span>
      <span className="mt-0.5 block break-words text-sm leading-snug text-ink">
        <Highlight text={run.question} terms={terms} />
      </span>
      {snippet && (
        <span className="mt-1 flex gap-1.5 text-xs leading-snug text-ink-3">
          <span aria-hidden="true" className="mt-px shrink-0 font-semibold text-ink-3">
            ❝
          </span>
          <span className="break-words">
            <span className="sr-only">In the findings: </span>
            <Highlight text={snippet} terms={terms} />
          </span>
        </span>
      )}
    </li>
  );
}

function SectionTitle({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <div id={id} className="px-3 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
      {children}
    </div>
  );
}

interface Found {
  query: string;
  data: RunSearch | null;
  failed?: boolean;
}

function SearchPanel({ inputRef, onClose }: { inputRef: React.RefObject<HTMLInputElement | null>; onClose: () => void }) {
  const ws = useWorkspace();
  const listId = useId();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Found | null>(null);
  const [active, setActive] = useState(0);
  const trimmed = query.trim();
  const terms = useMemo(() => termsOf(trimmed), [trimmed]);

  // Suggestions for an empty box: recent research, and the tickers researched so far.
  const { data: history } = useApi<RunList>(`/api/research?limit=40&v=${ws.version}`);
  const recent = history?.runs.slice(0, MAX_RECENT) ?? [];
  const tickers = useMemo(() => {
    const seen = new Map<string, string | null>();
    for (const run of history?.runs ?? []) if (!seen.has(run.symbol)) seen.set(run.symbol, run.company?.company_name ?? null);
    return [...seen].slice(0, MAX_TICKERS);
  }, [history]);

  useEffect(() => {
    if (!trimmed) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const data = await api<RunSearch>(`/api/research/search?q=${encodeURIComponent(trimmed)}&limit=10`);
        if (!cancelled) setFound({ query: trimmed, data });
      } catch {
        if (!cancelled) setFound({ query: trimmed, data: null, failed: true });
      }
    }, SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [trimmed]);

  const current = trimmed && found?.query === trimmed ? found : null;
  // While a new search runs, the previous results stay up instead of flickering away.
  const results = trimmed ? ((current ?? found)?.data?.results ?? []) : [];
  const options: { run: ResearchRun; snippet?: string | null }[] = trimmed ? results : recent.map((run) => ({ run }));
  const highlighted = Math.min(active, options.length - 1);

  useEffect(() => {
    if (highlighted >= 0) document.getElementById(`${listId}-${highlighted}`)?.scrollIntoView({ block: "nearest" });
  }, [highlighted, listId]);

  function open(run: ResearchRun) {
    ws.openRun(run.run_id);
    onClose();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && options.length > 0) {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((highlighted + step + options.length) % options.length);
    } else if (event.key === "Enter" && highlighted >= 0) {
      event.preventDefault();
      open(options[highlighted].run);
    }
  }

  let note: React.ReactNode = null;
  if (trimmed && current?.failed) note = "Search is unavailable right now. Try again in a moment.";
  else if (trimmed && !current && options.length === 0) note = "Searching…";
  else if (trimmed && current && options.length === 0)
    note = (
      <>
        No research matches “{trimmed}”. Try a ticker such as AAPL, a company name, or a word from a question or a finding.
        <button
          type="button"
          onClick={() => {
            ws.goHome();
            onClose();
          }}
          className="mt-3 block rounded-lg border border-line px-3 py-1.5 text-sm font-medium text-ink transition hover:border-accent hover:text-accent"
        >
          Start a new research
        </button>
      </>
    );
  else if (!trimmed && history && recent.length === 0) note = "Your research will appear here once you've run one.";

  return (
    <div>
      <div className="sticky -top-5 z-10 -mx-5 -mt-5 border-b border-line bg-surface px-5 py-3">
        <label className="flex items-center gap-2.5 rounded-xl border border-line bg-raised px-3 py-2 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20">
          <SearchIcon className="h-[18px] w-[18px] shrink-0 text-ink-3" />
          <span className="sr-only">Search your research</span>
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded={options.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={highlighted >= 0 ? `${listId}-${highlighted}` : undefined}
            placeholder="Search by ticker, company, question or a word in the findings"
            autoComplete="off"
            spellCheck={false}
            // Inline, because the global :focus-visible outline outranks utility classes; the
            // surrounding box shows focus with its accent border and ring instead.
            style={{ outline: "none" }}
            className="min-w-0 flex-1 bg-transparent text-[15px] text-ink placeholder:text-ink-3 [&::-webkit-search-cancel-button]:hidden"
          />
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setActive(0);
                inputRef.current?.focus();
              }}
              className="shrink-0 rounded-md px-1.5 text-xs font-medium text-ink-3 transition hover:bg-sunken hover:text-ink"
            >
              Clear
            </button>
          )}
        </label>
      </div>

      {!trimmed && tickers.length > 0 && (
        <div className="mb-3">
          <SectionTitle>Tickers you&apos;ve researched</SectionTitle>
          <div className="flex flex-wrap gap-1.5 px-3">
            {tickers.map(([symbol, name]) => (
              <button
                key={symbol}
                type="button"
                onClick={() => {
                  setQuery(symbol);
                  setActive(0);
                  inputRef.current?.focus();
                }}
                title={name ?? undefined}
                className="rounded-full border border-line px-2.5 py-1 text-xs text-ink-2 transition hover:border-accent hover:text-accent"
              >
                <span className="font-mono font-bold">{symbol}</span>
                {name && <span className="ml-1 text-ink-3">{name.replace(/,? (Inc|Corp|Corporation|Ltd|plc)\.?$/i, "")}</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {options.length > 0 && (
        <>
          <SectionTitle id={`${listId}-title`}>
            {trimmed ? `${results.length} ${results.length === 1 ? "match" : "matches"}` : "Recent research"}
          </SectionTitle>
          <ul id={listId} role="listbox" aria-labelledby={`${listId}-title`} className="space-y-0.5">
            {options.map((option, i) => (
              <ResultRow
                key={option.run.run_id}
                id={`${listId}-${i}`}
                run={option.run}
                snippet={option.snippet}
                terms={terms}
                active={i === highlighted}
                onOpen={() => open(option.run)}
                onHover={() => setActive(i)}
              />
            ))}
          </ul>
        </>
      )}

      {note && (
        <div role="status" className="px-3 py-6 text-center text-sm text-ink-3 [&>button]:mx-auto">
          {note}
        </div>
      )}

      <p aria-hidden="true" className="mt-4 flex flex-wrap justify-center gap-x-4 gap-y-1 text-[11px] text-ink-3">
        <span>
          <Kbd>↑</Kbd> <Kbd>↓</Kbd> move
        </span>
        <span>
          <Kbd>Enter</Kbd> open
        </span>
        <span>
          <Kbd>Esc</Kbd> close
        </span>
      </p>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-b-2 border-line bg-raised px-1 font-sans text-[10px] font-semibold text-ink-2">{children}</kbd>;
}

/** The search dialog; the workspace opens it from the history rail, the header or ⌘K / Ctrl+K. */
export function SearchModal({ onClose }: { onClose: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <Modal title="Search research" size="lg" placement="top" initialFocus={inputRef} onClose={onClose}>
      <SearchPanel inputRef={inputRef} onClose={onClose} />
    </Modal>
  );
}
