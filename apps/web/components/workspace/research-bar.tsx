"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { CreateResearchResponse, ResearchRequest } from "@/lib/api/types";

import { ArrowUpIcon, ChevronDownIcon } from "../icons";
import { DictationButton, useDictation } from "./dictation";
import { TickerCombobox } from "./ticker-combobox";
import { useWorkspace } from "./workspace-context";

type HorizonChoice = "default" | "1m" | "6m" | "1y" | "custom";

/** Used when the question box is left empty, so a ticker alone is enough to start. */
const DEFAULT_QUESTION = "Review recent price movement, company fundamentals, news context and key risks.";
/** Starting a run can wait for a cold backend and the queue (D-102); past this the page says so. */
const START_TIMEOUT_MS = 45_000;

/**
 * The research bar is docked at the bottom of the workspace, like a chat composer, on one line:
 * ticker dropdown, question, time window, dictation (speech to text), `voiceButton` (a two-way
 * voice session) and send. A new question can be asked from anywhere, and starting one opens the
 * run in place instead of navigating. `voiceActive` turns dictation off while a voice session runs.
 */
export function ResearchBar({ voiceButton, voiceActive = false }: { voiceButton?: React.ReactNode; voiceActive?: boolean }) {
  const ws = useWorkspace();
  const ids = { symbol: useId(), question: useId(), horizon: useId(), start: useId(), end: useId(), error: useId() };
  const questionRef = useRef<HTMLInputElement>(null);
  const [symbol, setSymbol] = useState(ws.preset?.symbol ?? "");
  const [question, setQuestion] = useState(ws.preset?.question ?? "");
  const [seenPreset, setSeenPreset] = useState(ws.preset?.nonce ?? 0);
  const [horizon, setHorizon] = useState<HorizonChoice>("default");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Dictation fills the question: whatever was typed before it started, then the spoken words.
  const dictationBase = useRef("");
  const onDictated = useCallback((spoken: string) => {
    const base = dictationBase.current;
    const text = base ? `${base} ${spoken}` : spoken.charAt(0).toUpperCase() + spoken.slice(1);
    setQuestion(text.slice(0, 1000));
  }, []);
  const dictation = useDictation(onDictated);
  const toggleDictation = () => {
    if (!dictation.listening) dictationBase.current = question.trim();
    dictation.toggle();
  };
  const stopDictation = dictation.stop;
  useEffect(() => {
    if (voiceActive) stopDictation();
  }, [voiceActive, stopDictation]);

  // Apply a new preset (a quick start) during render; no effect needed for state.
  if (ws.preset && ws.preset.nonce !== seenPreset) {
    setSeenPreset(ws.preset.nonce);
    setSymbol(ws.preset.symbol);
    if (ws.preset.question !== undefined) setQuestion(ws.preset.question);
    setError(null);
  }
  // The ticker follows the run on screen (and clears on the welcome screen), so the bar always
  // shows which company the current research is about; a quick start or typing can change it.
  const [seenRunSymbol, setSeenRunSymbol] = useState(ws.currentSymbol);
  if (ws.currentSymbol !== seenRunSymbol) {
    setSeenRunSymbol(ws.currentSymbol);
    setSymbol(ws.currentSymbol ?? "");
    setError(null);
  }
  // ...and move focus to the question so the user can type or just press Enter.
  const presetNonce = ws.preset?.nonce;
  useEffect(() => {
    if (presetNonce) questionRef.current?.focus();
  }, [presetNonce]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const body: ResearchRequest = {
      symbol: symbol.trim(),
      question: question.trim() || DEFAULT_QUESTION,
      client_request_id: crypto.randomUUID().replace(/-/g, "").slice(0, 32),
    };
    if (horizon === "custom") body.time_horizon = { start_date: start || null, end_date: end || null };
    else if (horizon !== "default") body.time_horizon = { preset: horizon };
    try {
      const created = await api<CreateResearchResponse>("/api/research", {
        method: "POST",
        json: body,
        timeoutMs: START_TIMEOUT_MS,
      });
      ws.refresh();
      ws.openRun(created.run.run_id);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError("UNKNOWN", "The request failed.", 0));
    } finally {
      setSubmitting(false);
    }
  }

  const fieldIssue = error?.fieldIssues.map((f) => f.issue).join(" ");
  const problem = error ? `${error.message}${fieldIssue ? ` ${fieldIssue}` : ""}` : null;
  const symbolProblem = Boolean(error && ["INVALID_SYMBOL", "UNKNOWN_SYMBOL"].includes(error.code));

  return (
    <form onSubmit={submit} noValidate aria-label="New research" className="min-w-0">
      {problem && (
        <p id={ids.error} role="alert" className="mb-1.5 px-3 text-xs text-critical-text">
          {problem}
          {error?.requestId && <span className="text-ink-3"> · request {error.requestId}</span>}
        </p>
      )}
      {(dictation.listening || dictation.note) && (
        <p role="status" className="mb-1.5 flex items-start gap-2 px-3 text-xs text-ink-2">
          {dictation.listening ? (
            <>
              <span aria-hidden="true" className="mt-1 h-2 w-2 shrink-0 animate-pulse rounded-full bg-critical" />
              <span>Listening… speak your question; the words appear in the box. Press ■ to stop. Your browser&apos;s speech service turns speech into text.</span>
            </>
          ) : (
            <>
              <span className="flex-1">{dictation.note}</span>
              <button type="button" onClick={dictation.dismiss} aria-label="Dismiss" className="rounded px-1 leading-none text-ink-3 hover:text-ink">
                ×
              </button>
            </>
          )}
        </p>
      )}
      <div className="rounded-[28px] border border-line bg-raised p-2 shadow-[0_2px_14px_rgba(0,0,0,0.07)] transition focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25">
        {/* One line at normal window widths; in a very narrow window the menu and buttons wrap to a second row. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 sm:flex-nowrap">
          <label htmlFor={ids.symbol} className="sr-only">
            Ticker
          </label>
          <TickerCombobox
            id={ids.symbol}
            value={symbol}
            currentSymbol={ws.currentSymbol}
            onChange={(next) => setSymbol(next)}
            onPicked={() => questionRef.current?.focus()}
            placeholder="Ticker"
            invalid={symbolProblem}
            describedBy={problem ? ids.error : undefined}
          />
          <label htmlFor={ids.question} className="sr-only">
            Research question (optional)
          </label>
          <input
            id={ids.question}
            ref={questionRef}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={1000}
            placeholder={dictation.listening ? "Listening…" : "What do you want to know?"}
            className="min-w-0 flex-1 bg-transparent px-1 py-1.5 text-[15px] text-ink placeholder:text-ink-3 focus-visible:outline-none"
          />
          <div className="flex basis-full items-center gap-1.5 sm:basis-auto">
            <label htmlFor={ids.horizon} className="sr-only">
              Time window
            </label>
            <span className="relative shrink-0">
              <select
                id={ids.horizon}
                value={horizon}
                onChange={(e) => setHorizon(e.target.value as HorizonChoice)}
                title="Time window"
                className="field-sizing-content appearance-none rounded-full border border-line bg-transparent py-1.5 pl-3 pr-7 text-sm text-ink-2 transition hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <option value="default">3 months</option>
                <option value="1m">1 month</option>
                <option value="6m">6 months</option>
                <option value="1y">12 months</option>
                <option value="custom">Custom dates</option>
              </select>
              <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-3">
                <ChevronDownIcon />
              </span>
            </span>
            <div className="ml-auto flex items-center gap-1.5">
              <DictationButton dictation={dictation} onToggle={toggleDictation} disabled={voiceActive} />
              {voiceButton}
              <button
                type="submit"
                aria-label={submitting ? "Starting…" : "Research"}
                title="Research"
                disabled={submitting || !symbol.trim()}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-plane transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-30"
              >
                {submitting ? <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-plane/40 border-t-plane" /> : <ArrowUpIcon />}
              </button>
            </div>
          </div>
        </div>
        {horizon === "custom" && (
          <div className="mt-2 flex flex-wrap items-center gap-2 px-1 pb-0.5 text-sm text-ink-2">
            <label htmlFor={ids.start}>From</label>
            <input id={ids.start} type="date" value={start} onChange={(e) => setStart(e.target.value)} className="rounded-lg border border-line bg-raised px-2 py-1" />
            <label htmlFor={ids.end}>to</label>
            <input id={ids.end} type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="rounded-lg border border-line bg-raised px-2 py-1" />
          </div>
        )}
      </div>
    </form>
  );
}
