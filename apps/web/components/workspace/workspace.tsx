"use client";

import { useEffect, useState } from "react";

import { SearchIcon } from "../icons";
import { Modal } from "../modal";
import { RunView } from "../run/run-view";
import { useStatus } from "../status-provider";
import { useVoiceControl, VoiceButton, VoiceTray } from "../voice/voice-panel";
import { Rail } from "./rail";
import { ResearchBar } from "./research-bar";
import { GuideButton } from "./guide";
import { SearchModal } from "./search";
import { SuggestionsRail } from "./suggestions";
import { Welcome } from "./welcome";
import { type Preset, type View, type WorkspaceApi, WorkspaceProvider } from "./workspace-context";

// Canonical copy (PROJECT_STATUS §2.5); mirrors marketlens_core.policy.RESEARCH_ONLY_NOTICE.
const RESEARCH_ONLY_DETAIL =
  "MarketLens does not place trades, connect to brokerage accounts, or provide personalized buy/sell recommendations. Content is informational, may be incomplete or delayed, and is not financial advice.";

export interface WorkspaceInitial {
  run?: string | null;
  symbol?: string | null;
}

function urlFor(view: View): string {
  return view ? `/?run=${encodeURIComponent(view.id)}` : "/";
}

function ModeBanner() {
  const { health, error, loading } = useStatus();
  if (loading) return null;
  if (error || !health) {
    return (
      <div className="border-b border-critical/40 bg-critical/10 px-4 py-1.5 text-center text-sm text-ink" role="alert">
        <strong>Backend unavailable.</strong> {error?.message ?? "Health check failed."}
      </div>
    );
  }
  if (health.data_mode === "fixtures") {
    return (
      <div className="border-b border-demo-border bg-demo-bg px-4 py-1 text-center text-xs text-demo-text" role="status" data-testid="demo-banner">
        <strong className="tracking-wide">DEMO FIXTURES</strong> — synthetic, illustrative data<span className="hidden sm:inline">. Not current market data</span>.
      </div>
    );
  }
  return (
    <div className="border-b border-good/40 bg-good/10 px-4 py-1 text-center text-xs font-medium text-good-text" role="status">
      LIVE DATA<span className="hidden sm:inline"> — values come from configured providers; check the as-of date on every section</span>
      <span className="sm:hidden"> — check each section&apos;s as-of date</span>
    </div>
  );
}

/** Docked under the main pane like a chat composer: the voice tray, the research bar and the research-only note. */
function Composer() {
  const voice = useVoiceControl();
  return (
    <div className="shrink-0 bg-plane px-3 pb-2 pt-2 sm:px-4 print:hidden">
      <div className="mx-auto w-full max-w-4xl">
        <VoiceTray voice={voice} />
        <ResearchBar voiceButton={<VoiceButton voice={voice} />} voiceActive={voice.live || voice.checking} />
        <p className="mt-1.5 text-center text-[11px] leading-snug text-ink-3" data-testid="research-only-footer">
          <strong className="text-ink-2">Research only.</strong> <span className="sr-only sm:not-sr-only">{RESEARCH_ONLY_DETAIL}</span>
          <span aria-hidden="true" className="sm:hidden">
            No trades, no brokerage access, no personal buy/sell advice. Not financial advice.
          </span>
        </p>
      </div>
    </div>
  );
}

/**
 * The whole app is this one screen: research history on the left, the run in the middle with the
 * research bar docked at the bottom, and quick starts on the right (wide screens; the evidence
 * inspector takes that place while it is open). Only the brief scrolls. The URL is kept in sync
 * (?run=) without navigating, so refresh and sharing still work.
 */
export function Workspace({ initial }: { initial: WorkspaceInitial }) {
  const [view, setView] = useState<View>(initial.run ? { kind: "run", id: initial.run } : null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [preset, setPreset] = useState<Preset | null>(initial.symbol ? { symbol: initial.symbol, nonce: 1 } : null);
  const [version, setVersion] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [currentSymbol, setCurrentSymbol] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);

  function show(next: View) {
    setView(next);
    setMenuOpen(false);
    if (!next) setCurrentSymbol(null); // the welcome screen starts with an empty ticker
    window.history.replaceState(null, "", urlFor(next));
  }

  function openSearch() {
    setMenuOpen(false); // from the History dialog in narrower windows: one dialog at a time
    setSearchOpen(true);
  }

  // ⌘K (Mac) or Ctrl+K anywhere opens the search, like most chat apps.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setMenuOpen(false);
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const workspace: WorkspaceApi = {
    view,
    openRun: (id) => show({ kind: "run", id }),
    goHome: () => show(null),
    openSearch,
    prefill: (symbol, question) => setPreset((p) => ({ symbol, question, nonce: (p?.nonce ?? 0) + 1 })),
    preset,
    version,
    refresh: () => setVersion((v) => v + 1),
    setInspectorOpen,
    currentSymbol,
    setCurrentSymbol,
  };

  return (
    <WorkspaceProvider value={workspace}>
      {/* overflow-clip, not -hidden: a clipped frame cannot be scrolled by anchor jumps or focus.
          relative: absolutely positioned pieces (screen-reader text) stay inside it instead of
          stretching the page, which would let the whole app scroll up (D-86). */}
      <div className="relative flex h-dvh flex-col overflow-clip bg-plane print:block print:h-auto print:overflow-visible print:bg-white">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-raised focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <ModeBanner />
        <header className="border-b border-line bg-surface print:hidden">
          <div className="flex items-center gap-4 px-4 py-2">
            <button type="button" onClick={workspace.goHome} className="flex shrink-0 items-center gap-2" aria-label="MarketLens home">
              <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-xs font-black text-plane">
                ML
              </span>
              <span className="text-base font-semibold tracking-tight text-ink">MarketLens</span>
            </button>
            <nav aria-label="Workspace" className="ml-auto flex items-center gap-0.5 xl:hidden">
              {/* In wider windows the search sits beside New research in the history rail (D-96). */}
              <button
                type="button"
                onClick={openSearch}
                aria-label="Search research"
                className="grid h-8 w-8 place-items-center rounded-lg text-ink-2 transition hover:bg-sunken hover:text-ink lg:hidden"
              >
                <SearchIcon />
              </button>
              <button
                type="button"
                className="rounded-lg px-2 py-1.5 text-sm font-medium text-ink-2 transition hover:bg-sunken hover:text-ink sm:px-3 lg:hidden"
                onClick={() => setMenuOpen(true)}
              >
                History
              </button>
              {/* On wide screens the guide sits at the bottom right instead (D-92). */}
              <GuideButton variant="header" />
            </nav>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 print:block">
          <aside aria-label="Your research" className="hidden w-72 shrink-0 overflow-y-auto border-r border-line bg-surface lg:block print:!hidden">
            <Rail />
          </aside>
          <main id="main" className="flex min-w-0 flex-1 flex-col print:block">
            {view === null && <Welcome />}
            {view?.kind === "run" && <RunView key={view.id} runId={view.id} />}
            <Composer />
          </main>
          {!inspectorOpen && (
            <aside aria-label="Suggestions" className="hidden w-72 shrink-0 flex-col overflow-y-auto border-l border-line bg-surface xl:flex print:!hidden">
              <SuggestionsRail />
              <div className="mt-auto p-3">
                <GuideButton variant="pill" />
              </div>
            </aside>
          )}
        </div>

        {menuOpen && (
          <Modal title="Research history" size="md" onClose={() => setMenuOpen(false)}>
            <Rail />
          </Modal>
        )}
        {searchOpen && <SearchModal onClose={() => setSearchOpen(false)} />}
      </div>
    </WorkspaceProvider>
  );
}
