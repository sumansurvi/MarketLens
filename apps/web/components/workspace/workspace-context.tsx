"use client";

import { createContext, useContext } from "react";

/** What the main pane shows: a research run, or the welcome screen. */
export type View = { kind: "run"; id: string } | null;

export interface Preset {
  symbol: string;
  question?: string;
  nonce: number;
}

export interface WorkspaceApi {
  view: View;
  openRun: (runId: string) => void;
  goHome: () => void;
  /** Open "Search research" (D-96). */
  openSearch: () => void;
  /** Fill the research bar (e.g. from a quick start) without starting anything. */
  prefill: (symbol: string, question?: string) => void;
  preset: Preset | null;
  /** Bumped when runs change, so the research history refetches. */
  version: number;
  refresh: () => void;
  /** A run reports its evidence inspector open, so the suggestions rail makes room for it. */
  setInspectorOpen: (open: boolean) => void;
  /** The ticker of the run on screen (null on the welcome screen); the research bar shows it. */
  currentSymbol: string | null;
  setCurrentSymbol: (symbol: string | null) => void;
}

const WorkspaceContext = createContext<WorkspaceApi | null>(null);

export const WorkspaceProvider = WorkspaceContext.Provider;

export function useWorkspace(): WorkspaceApi {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside <Workspace>.");
  return value;
}
