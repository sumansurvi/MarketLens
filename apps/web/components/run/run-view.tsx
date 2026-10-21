"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { CreateResearchResponse, ResearchBrief, RunDetail } from "@/lib/api/types";
import { usePolling } from "@/lib/use-api";

import { Modal } from "../modal";
import { buttonClass, ModeBadge, Notice, Spinner, StatusBadge } from "../ui";
import { useWorkspace } from "../workspace/workspace-context";
import { BriefView } from "./brief-view";
import { ComparePeriods } from "./compare-periods";
import { BriefFrame, Headline, SummaryBar } from "./frame";
import { describeIssues, HeadsUp } from "./issues";
import { RunDetails, RunProgress } from "./progress";
import { PdfButton } from "./share";
import { useEvidenceInspector } from "./use-inspector";

const TERMINAL = new Set(["completed", "partial", "failed", "cancelled"]);
const EMPTY: RunDetail["evidence"] = [];
const EMPTY_CLAIMS: ResearchBrief["claims"] = [];

export function RunView({ runId }: { runId: string }) {
  const ws = useWorkspace();
  const isDone = useCallback((d: RunDetail) => TERMINAL.has(d.run.status) && !d.is_active, []);
  const { data, error, loading, reload } = usePolling<RunDetail>(`/api/research/${runId}`, isDone);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<"compare" | "details" | null>(null);
  const inspector = useEvidenceInspector(data?.evidence ?? EMPTY, data?.brief?.claims ?? EMPTY_CLAIMS);
  const { setInspectorOpen, setCurrentSymbol } = ws;
  useEffect(() => {
    setInspectorOpen(inspector.open);
    return () => setInspectorOpen(false);
  }, [inspector.open, setInspectorOpen]);
  // The research bar shows this run's ticker, so it is clear which company is on screen.
  const runSymbol = data?.run.symbol ?? null;
  useEffect(() => {
    if (runSymbol) setCurrentSymbol(runSymbol);
  }, [runSymbol, setCurrentSymbol]);

  if (loading && !data) {
    return (
      <div className="grid flex-1 place-items-center">
        <Spinner label="Loading research…" />
      </div>
    );
  }
  if (error && !data) {
    return (
      <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
        <Notice tone="critical" title={error.code === "NOT_FOUND" ? "This research run doesn't exist" : "Couldn't load this research"}>
          {error.message}{" "}
          <button type="button" onClick={ws.goHome} className="font-medium text-accent underline">
            Start new research
          </button>
        </Notice>
      </div>
    );
  }
  if (!data) return null;

  const { run, brief } = data;
  const active = !TERMINAL.has(run.status) || data.is_active;
  const issues = active ? [] : describeIssues(run.errors, { withheld: brief?.review.withheld, failed: run.status === "failed" });

  async function cancel() {
    setBusy(true);
    setActionError(null);
    try {
      await api(`/api/research/${run.run_id}/cancel`, { method: "POST" });
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err : null);
    } finally {
      setBusy(false);
    }
  }

  async function runAgain() {
    setBusy(true);
    setActionError(null);
    try {
      // Same symbol, question, focus and window as a new linked run; this run stays unchanged.
      const created = await api<CreateResearchResponse>(`/api/research/${run.run_id}/rerun`, { method: "POST" });
      ws.refresh();
      ws.openRun(created.run.run_id);
    } catch (err) {
      setActionError(err instanceof ApiError ? err : null);
      setBusy(false);
    }
  }

  const bar = (
    <SummaryBar
      symbol={run.symbol}
      company={run.company?.company_name}
      badges={
        <>
          <StatusBadge status={run.status} />
          <ModeBadge isFixture={run.data_mode === "fixture"} />
          {run.origin === "voice" && <span className="rounded-full border border-line px-2 py-0.5 text-[11px] font-medium text-ink-2">started by voice</span>}
        </>
      }
      actions={
        active ? null : (
          <>
            <button type="button" className={buttonClass.secondarySm} onClick={runAgain} disabled={busy}>
              {busy ? "Starting…" : "Re-run"}
            </button>
            {brief && run.company && (
              <button type="button" className={buttonClass.secondarySm} onClick={() => setDialog("compare")}>
                <span>Compare<span className="hidden sm:inline"> periods</span></span>
              </button>
            )}
            <button type="button" className={buttonClass.secondarySm} onClick={() => setDialog("details")}>
              Details
            </button>
            {brief && <PdfButton brief={brief} />}
          </>
        )
      }
      headline={<Headline brief={brief} />}
      question={run.question}
    />
  );

  return (
    <BriefFrame bar={bar} side={inspector.panel}>
      {actionError && <Notice tone="critical">{actionError.message}</Notice>}
      {error && <Notice tone="warning">Connection problem while refreshing: {error.message}</Notice>}
      {run.parent_run_id && (
        <p className="text-xs text-ink-3">
          A re-run of{" "}
          <button type="button" className="font-medium text-accent hover:underline" onClick={() => ws.openRun(run.parent_run_id ?? "")}>
            an earlier run
          </button>{" "}
          with the same settings. The original is unchanged.
        </p>
      )}

      {active && <RunProgress detail={data} onCancel={cancel} cancelling={busy} />}

      {/* A failed run has one retry: Re-run in the header, which the card's text points to. */}
      {!active && (
        <HeadsUp
          issues={issues}
          failed={run.status === "failed"}
          onRetry={run.status === "failed" ? undefined : runAgain}
          retrying={busy}
        />
      )}

      {run.status === "cancelled" && <Notice title="Run cancelled">No report was produced. Use Re-run to start a new run.</Notice>}

      {brief && (
        <BriefView
          brief={brief}
          evidence={inspector.evidence}
          selectedEvidence={inspector.selected}
          onSelectEvidence={inspector.select}
          hiddenGapCodes={issues.map((i) => i.code)}
        />
      )}

      {dialog === "compare" && run.company && (
        <Modal
          title={`Compare two periods · ${run.symbol}`}
          description="Closing-price change for each period, calculated from the first and last trading days inside it."
          onClose={() => setDialog(null)}
        >
          <ComparePeriods bare symbol={run.symbol} start={run.time_horizon.start_date} end={run.time_horizon.end_date} />
        </Modal>
      )}
      {dialog === "details" && (
        <Modal title="Run details" description="How this run was carried out: data sources, steps and every MCP tool call." size="xl" onClose={() => setDialog(null)}>
          <RunDetails
            detail={data}
            onOpenRun={(id) => {
              setDialog(null);
              ws.openRun(id);
            }}
          />
        </Modal>
      )}
    </BriefFrame>
  );
}
