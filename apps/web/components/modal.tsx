"use client";

import { useEffect, useId, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { cn } from "./ui";

const noSubscription = () => () => {};

/** False during server rendering and hydration, true afterwards (no effect or state needed). */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
}

/**
 * Render overlays on document.body. A sticky, blurred or transformed ancestor would otherwise
 * become the containing block for `position: fixed` and trap the overlay inside it.
 */
export function Overlay({ children }: { children: React.ReactNode }) {
  const client = useIsClient();
  return client ? createPortal(children, document.body) : null;
}

const SIZES = {
  md: "sm:max-w-lg",
  lg: "sm:max-w-3xl",
  xl: "sm:max-w-5xl",
} as const;

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Dialog for secondary content (lists, settings, details) so the research workspace never
 * navigates away. Esc and the backdrop close it, Tab stays inside, and focus returns to
 * the control that opened it. A write confirmation opened on top handles its own keys.
 */
export function Modal({
  title,
  description,
  onClose,
  size = "lg",
  placement = "center",
  initialFocus,
  children,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  onClose: () => void;
  size?: keyof typeof SIZES;
  /** "top" keeps the dialog's top edge still while its content grows (search results). */
  placement?: "center" | "top";
  /** Focused when the dialog opens, instead of the dialog itself. */
  initialFocus?: React.RefObject<HTMLElement | null>;
  children: React.ReactNode;
}) {
  const titleId = useId();
  const descId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const client = useIsClient();

  // Runs again once the portal exists, so focus moves into the dialog even on first render.
  useEffect(() => {
    if (!client) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (initialFocus?.current ?? dialogRef.current)?.focus();
    return () => opener?.focus();
  }, [client, initialFocus]);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const nodes = [...dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialogRef.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  if (!client) return null;
  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-40 flex items-end justify-center bg-black/45 backdrop-blur-[2px] sm:p-6",
        placement === "top" ? "sm:items-start sm:pt-[12vh]" : "sm:items-center",
      )}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className={cn(
          "flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-surface shadow-2xl outline-none sm:rounded-2xl",
          placement === "top" && "sm:max-h-[80dvh]",
          SIZES[size],
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold tracking-tight text-ink">
              {title}
            </h2>
            {description && (
              <p id={descId} className="mt-0.5 text-sm text-ink-3">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 rounded-lg px-2 py-1 text-lg leading-none text-ink-2 transition hover:bg-sunken hover:text-ink"
          >
            ×
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
