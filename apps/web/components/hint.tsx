"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Not imported from ./ui, which imports this file.
function cn(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Position = { x: number; y: number; below: boolean };

/** The "explains itself" cue: a small circled i. */
export function InfoIcon({ className = "h-3 w-3" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" className={className}>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M8 7.25v3.75" strokeLinecap="round" />
      <circle cx="8" cy="4.9" r="0.8" fill="currentColor" stroke="none" />
    </svg>
  );
}

/**
 * Explains a badge or label on hover or keyboard focus. The cue that a label can be hovered is a
 * small ⓘ icon at its end (brighter on hover) and the help cursor. The tooltip is rendered on the page body with fixed
 * positioning, so scrolling panes never clip it; Esc or scrolling closes it. Screen readers get
 * the same text as a description.
 *
 * `focusable={false}` for hints inside another control (a button) or repeated many times.
 */
export function Hint({
  text,
  children,
  className,
  focusable = true,
}: {
  text: string;
  children: React.ReactNode;
  className?: string;
  focusable?: boolean;
}) {
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState<Position | null>(null);

  const show = () => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const below = rect.top < 96;
    const half = Math.min(144, window.innerWidth / 2 - 8);
    setPosition({
      x: Math.min(Math.max(rect.left + rect.width / 2, half + 8), window.innerWidth - half - 8),
      y: below ? rect.bottom + 8 : rect.top - 8,
      below,
    });
  };
  const hide = () => setPosition(null);

  useEffect(() => {
    if (!position) return;
    const close = () => setPosition(null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPosition(null);
    };
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [position]);

  return (
    <span
      ref={ref}
      tabIndex={focusable ? 0 : undefined}
      aria-describedby={id}
      onPointerEnter={show}
      onPointerLeave={hide}
      onFocus={show}
      onBlur={hide}
      className={cn(
        "group relative inline-flex cursor-help items-center gap-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
        className,
      )}
    >
      {children}
      <InfoIcon className="h-3 w-3 shrink-0 opacity-45 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 print:hidden" />
      <span id={id} className="sr-only">
        {text}
      </span>
      {position &&
        createPortal(
          <span
            aria-hidden="true"
            style={{ left: position.x, top: position.y }}
            className={cn(
              "pointer-events-none fixed z-[70] w-max max-w-[18rem] -translate-x-1/2 rounded-lg bg-ink px-2.5 py-1.5 text-left text-xs font-normal normal-case leading-snug tracking-normal text-plane no-underline shadow-lg",
              !position.below && "-translate-y-full",
            )}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  );
}
