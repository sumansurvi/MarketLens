// Small presentational primitives shared across screens.
import type { ClaimType, RunStatus } from "@/lib/api/types";

import { Hint } from "./hint";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({
  title,
  description,
  actions,
  children,
  className,
  id,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cn("scroll-mt-28 rounded-2xl border border-line bg-surface p-5 shadow-[0_1px_2px_rgba(0,0,0,0.04)]", className)}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-base font-semibold tracking-tight text-ink">{title}</h2>}
            {description && <p className="mt-0.5 text-sm text-ink-3">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

const STATUS_STYLE: Record<RunStatus, { label: string; icon: string; className: string }> = {
  queued: { label: "Queued", icon: "○", className: "text-ink-2 border-line bg-sunken" },
  running: { label: "Running", icon: "◔", className: "text-accent border-accent/30 bg-accent-wash" },
  completed: { label: "Completed", icon: "✓", className: "text-good-text border-good/40 bg-good/10" },
  partial: { label: "Partial", icon: "◐", className: "text-warning-text border-warning/60 bg-warning/15" },
  failed: { label: "Failed", icon: "✕", className: "text-critical-text border-critical/40 bg-critical/10" },
  cancelled: { label: "Cancelled", icon: "–", className: "text-ink-2 border-line bg-sunken" },
};

/** What each run status means, shown on hover. */
export const STATUS_HINT: Record<RunStatus, string> = {
  queued: "Waiting to start.",
  running: "Research in progress. The brief appears when every step has finished.",
  completed: "All the evidence the brief needs was found, and every statement passed the fact-check.",
  partial: "Usable but incomplete: some data wasn't available, or some statements failed the fact-check. The Heads up card lists what.",
  failed: "No trustworthy brief could be produced. The Heads up card says why.",
  cancelled: "Stopped before it finished, so there is no brief.",
};

/** `explain` adds the hover definition; it is used inside the brief (the response) only (D-87). */
export function StatusBadge({ status, explain = false }: { status: RunStatus; explain?: boolean }) {
  const style = STATUS_STYLE[status];
  const className = cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold", style.className);
  const content = (
    <>
      <span aria-hidden="true">{style.icon}</span>
      {style.label}
    </>
  );
  return explain ? (
    <Hint text={STATUS_HINT[status]} className={className}>
      {content}
    </Hint>
  ) : (
    <span className={className}>{content}</span>
  );
}

const CLAIM_STYLE: Record<ClaimType, { label: string; dot: string; hint: string }> = {
  source_fact: {
    label: "Source fact",
    dot: "bg-[#2a78d6]",
    hint: "Taken directly from a source: a filing, a price record or a news story.",
  },
  calculation: {
    label: "Calculation",
    dot: "bg-[#4a3aa7] dark:bg-[#9085e9]",
    hint: "Computed by code from the source data, not by the AI.",
  },
  interpretation: {
    label: "Interpretation",
    dot: "bg-[#eda100]",
    hint: "Analysis drawn from the evidence. Not a fact, and not advice.",
  },
  scenario: {
    label: "Scenario",
    dot: "bg-[#e87ba4]",
    hint: "An “if…, then…” possibility with stated assumptions. Not a forecast.",
  },
};

/** Claim type is carried by text; the dot is a secondary identity cue, never the only one. */
export function ClaimTypeBadge({ type }: { type: ClaimType }) {
  const style = CLAIM_STYLE[type];
  return (
    <Hint
      text={style.hint}
      focusable={false}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-line bg-raised px-1.5 py-0.5 text-[11px] font-medium text-ink-2"
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} aria-hidden="true" />
      {style.label}
    </Hint>
  );
}

export const MODE_HINT = {
  live: "Real data: prices from Alpha Vantage; filings and financial results from SEC EDGAR.",
  demo: "Synthetic sample data for demonstrations. Never current market data.",
};

export function ModeBadge({ isFixture, explain = false }: { isFixture: boolean; explain?: boolean }) {
  const className = isFixture
    ? "inline-flex items-center rounded-md border border-demo-border bg-demo-bg px-2 py-0.5 text-[11px] font-bold tracking-wide text-demo-text"
    : "inline-flex items-center rounded-md border border-good/40 bg-good/10 px-2 py-0.5 text-[11px] font-bold tracking-wide text-good-text";
  const label = isFixture ? "DEMO FIXTURES" : "LIVE DATA";
  return explain ? (
    <Hint text={isFixture ? MODE_HINT.demo : MODE_HINT.live} className={className}>
      {label}
    </Hint>
  ) : (
    <span className={className}>{label}</span>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warning" | "critical";
  title?: string;
  children: React.ReactNode;
}) {
  const styles = {
    info: { box: "border-line bg-sunken text-ink-2", icon: "ℹ" },
    warning: { box: "border-warning/60 bg-warning/10 text-ink", icon: "!" },
    critical: { box: "border-critical/40 bg-critical/10 text-ink", icon: "✕" },
  }[tone];
  return (
    <div role={tone === "critical" ? "alert" : "status"} className={cn("flex gap-3 rounded-lg border px-4 py-3 text-sm", styles.box)}>
      <span aria-hidden="true" className="mt-0.5 font-bold">
        {styles.icon}
      </span>
      <div>
        {title && <p className="font-semibold">{title}</p>}
        <div>{children}</div>
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-baseline px-4 py-6 text-center">
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {children && <div className="mt-1 text-sm text-ink-3">{children}</div>}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-3" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-hairline border-t-accent" aria-hidden="true" />
      {label}
    </span>
  );
}

export function ButtonLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} className="text-sm font-medium text-accent underline-offset-2 hover:underline">
      {children}
    </a>
  );
}

export const buttonClass = {
  primary:
    "inline-flex items-center justify-center gap-2 rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-plane transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
  secondary:
    "inline-flex items-center justify-center gap-2 rounded-lg border border-line bg-raised px-3.5 py-2 text-sm font-medium text-ink transition hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-50",
  ghost:
    "inline-flex items-center justify-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-accent transition hover:bg-accent-wash disabled:opacity-50",
  /** Compact variants for the frozen run bar. */
  primarySm:
    "inline-flex items-center justify-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-sm font-semibold text-plane transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
  secondarySm:
    "inline-flex items-center justify-center gap-1.5 rounded-lg border border-line bg-raised px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-50",
};
