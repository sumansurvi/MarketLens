"use client";

import { useEffect, useId, useMemo, useState } from "react";

import { api } from "@/lib/api/client";
import type { RunList, SymbolMatch, SymbolSearch } from "@/lib/api/types";
import { useApi } from "@/lib/use-api";

import { ChevronDownIcon } from "../icons";
import { cn } from "../ui";
import { useWorkspace } from "./workspace-context";

const SEARCH_DELAY_MS = 150;
const MAX_RECENT = 6;
const TOP_LIMIT = 12;

interface Props {
  id: string;
  value: string;
  onChange: (symbol: string) => void;
  /** Called after a suggestion is picked (the bar moves focus to the question). */
  onPicked: () => void;
  placeholder: string;
  invalid: boolean;
  describedBy?: string;
  /** The ticker of the research on screen, listed first and shown as selected. */
  currentSymbol: string | null;
}

type Group = { key: string; title: string; options: SymbolMatch[] };
type Results = { query: string; matches: SymbolMatch[]; isFixture: boolean; failed?: boolean };

/**
 * The research bar's ticker field: a combobox whose list always has values. Opening it (click,
 * focus or ↓) lists the ticker of the research on screen, recently researched tickers and the
 * largest covered companies (GET /api/symbols with no query); typing a ticker or company name
 * filters it. The chosen ticker is marked ✓. Picking only fills the field: research still resolves
 * exactly the ticker submitted. Empty, the field is highlighted so it is clear a ticker is needed.
 * Opens upward, since the bar is at the bottom.
 */
export function TickerCombobox({ id, value, onChange, onPicked, placeholder, invalid, describedBy, currentSymbol }: Props) {
  const ws = useWorkspace();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  /** What the user typed since opening; null shows the full list. */
  const [filter, setFilter] = useState<string | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const query = filter?.trim() ?? "";
  const selected = value.trim().toUpperCase();

  const { data: history } = useApi<RunList>(`/api/research?limit=30&v=${ws.version}`);
  const { data: top } = useApi<SymbolSearch>(`/api/symbols?limit=${TOP_LIMIT}`);

  const groups = useMemo<Group[]>(() => {
    const names = new Map<string, SymbolMatch>();
    for (const m of top?.matches ?? []) names.set(m.symbol, m);
    const recent: SymbolMatch[] = [];
    for (const run of history?.runs ?? []) {
      if (recent.some((r) => r.symbol === run.symbol)) continue;
      recent.push(
        names.get(run.symbol) ?? { symbol: run.symbol, company_name: run.company?.company_name ?? "", exchange: run.company?.exchange ?? null },
      );
    }
    const find = (symbol: string): SymbolMatch =>
      recent.find((r) => r.symbol === symbol) ?? names.get(symbol) ?? { symbol, company_name: "", exchange: null };
    const used = new Set<string>();
    const take = (list: SymbolMatch[]) =>
      list.filter((m) => {
        if (used.has(m.symbol)) return false;
        used.add(m.symbol);
        return true;
      });
    const out: Group[] = [];
    if (currentSymbol) out.push({ key: "current", title: "In this research", options: take([find(currentSymbol)]) });
    out.push({ key: "recent", title: "Recently researched", options: take(recent).slice(0, MAX_RECENT) });
    out.push({ key: "top", title: top?.is_fixture ? "In the demo data" : "Largest companies", options: take(top?.matches ?? []) });
    return out.filter((g) => g.options.length > 0);
  }, [history, top, currentSymbol]);

  useEffect(() => {
    if (!open || !query) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const found = await api<SymbolSearch>(`/api/symbols?q=${encodeURIComponent(query)}&limit=10`);
        if (!cancelled) setResults({ query, matches: found.matches, isFixture: found.is_fixture });
      } catch {
        if (!cancelled) setResults({ query, matches: [], isFixture: false, failed: true });
      }
    }, SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, query]);

  const current = query && results?.query === query ? results : null;
  // While a new search runs, the previous matches stay up instead of flickering away.
  const shown: Group[] = query ? [{ key: "matches", title: "Matching tickers", options: (current ?? results)?.matches ?? [] }] : groups;
  const options = shown.flatMap((g) => g.options);
  const highlighted = active < options.length ? active : -1;

  useEffect(() => {
    if (open && highlighted >= 0) document.getElementById(`${listId}-${highlighted}`)?.scrollIntoView({ block: "nearest" });
  }, [open, highlighted, listId]);

  function openList() {
    setOpen(true);
    setFilter(null);
    setActive(-1);
  }

  function pick(option: SymbolMatch) {
    onChange(option.symbol);
    setOpen(false);
    setActive(-1);
    setFilter(null);
    onPicked();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openList();
        return;
      }
      if (options.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((highlighted + step + options.length) % options.length);
    } else if (event.key === "Enter" && open && highlighted >= 0) {
      event.preventDefault();
      pick(options[highlighted]);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  let note: string | null = null;
  if (query && current && current.matches.length === 0) {
    note = current.failed
      ? "Suggestions are unavailable right now. You can still type a ticker."
      : current.isFixture
        ? "No match in the demo data (AAPL, MSFT, NVDA)."
        : `No NYSE or Nasdaq ticker or company starts with “${query}”.`;
  } else if (query && !current && options.length === 0) {
    note = "Searching…";
  } else if (!query && options.length === 0) {
    note = "Loading tickers…";
  }

  let index = -1;
  const empty = !value.trim();
  return (
    <div className="relative shrink-0">
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && highlighted >= 0 ? `${listId}-${highlighted}` : undefined}
        aria-invalid={invalid}
        aria-describedby={describedBy}
        value={value}
        onChange={(e) => {
          onChange(e.target.value.toUpperCase());
          setFilter(e.target.value);
          setActive(-1);
          setOpen(true);
        }}
        onFocus={(e) => {
          const input = e.currentTarget;
          requestAnimationFrame(() => input.select()); // typing replaces the current ticker (after mouseup)
          openList();
        }}
        onClick={() => {
          if (!open) openList();
        }}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        maxLength={16}
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
        title={empty ? "Choose a ticker: pick one from the list, or type a ticker or company name" : `Ticker: ${selected}`}
        className={cn(
          "w-[5.5rem] rounded-full py-1.5 pl-2.5 pr-6 font-mono text-sm font-semibold uppercase tracking-wide text-ink placeholder:font-sans placeholder:normal-case placeholder:tracking-normal focus-visible:outline-none",
          empty ? "bg-accent-wash ring-1 ring-accent/50 placeholder:font-semibold placeholder:text-accent" : "bg-sunken",
          invalid && "ring-1 ring-critical",
        )}
      />
      <span aria-hidden="true" className={cn("pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2", empty ? "text-accent" : "text-ink-3")}>
        <ChevronDownIcon />
      </span>
      {open && (
        <div className="absolute bottom-full left-0 z-40 mb-2 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-1 shadow-xl">
          <div id={listId} role="listbox" aria-label="Tickers" className="max-h-80 overflow-y-auto">
            {shown.map((group) =>
              group.options.length === 0 ? null : (
                <div key={group.key} role="group" aria-labelledby={`${listId}-${group.key}`}>
                  <div id={`${listId}-${group.key}`} role="presentation" className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
                    {group.title}
                  </div>
                  {group.options.map((option) => {
                    index += 1;
                    const i = index;
                    const isSelected = option.symbol === selected;
                    return (
                      <div
                        key={option.symbol}
                        id={`${listId}-${i}`}
                        role="option"
                        aria-selected={isSelected}
                        onMouseDown={(e) => {
                          e.preventDefault(); // keep focus in the field until the pick is applied
                          pick(option);
                        }}
                        onMouseEnter={() => setActive(i)}
                        className={cn(
                          "flex cursor-pointer items-baseline gap-3 rounded-lg px-2.5 py-2",
                          i === highlighted && "bg-accent-wash",
                          isSelected && i !== highlighted && "bg-sunken",
                        )}
                      >
                        <span className={cn("w-16 shrink-0 font-mono text-sm font-bold", isSelected ? "text-accent" : "text-ink")}>{option.symbol}</span>
                        <span className="min-w-0 flex-1 text-sm leading-snug text-ink-2">{option.company_name || "—"}</span>
                        {option.exchange && <span className="shrink-0 text-[11px] text-ink-3">{option.exchange}</span>}
                        <span aria-hidden="true" className={cn("w-4 shrink-0 text-sm font-bold text-accent", !isSelected && "invisible")}>
                          ✓
                        </span>
                      </div>
                    );
                  })}
                </div>
              ),
            )}
          </div>
          {note && (
            <p role="status" className="px-2.5 py-2 text-sm text-ink-3">
              {note}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
