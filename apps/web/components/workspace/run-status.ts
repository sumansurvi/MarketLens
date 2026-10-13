import type { RunStatus } from "@/lib/api/types";

/** Status dot and word for a run in lists (research history, search). */
export const STATUS_DOT: Record<RunStatus, { dot: string; label: string }> = {
  queued: { dot: "bg-ink-3", label: "Queued" },
  running: { dot: "bg-accent animate-pulse", label: "Running" },
  completed: { dot: "bg-good", label: "Complete" },
  partial: { dot: "bg-warning", label: "Partial" },
  failed: { dot: "bg-critical", label: "Failed" },
  cancelled: { dot: "bg-ink-3", label: "Cancelled" },
};
