"use client";

import { useEffect, useState } from "react";

const THRESHOLD_PX = 80;

/**
 * A round ↓ button floating at the bottom centre of a scrolling pane, like ChatGPT's: shown while
 * there is more below, it scrolls smoothly to the end. The parent must be `relative` and contain
 * the scroller referenced by `scroller`.
 */
export function ScrollToBottom({ scroller }: { scroller: React.RefObject<HTMLElement | null> }) {
  const [more, setMore] = useState(false);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () => setMore(el.scrollHeight - el.scrollTop - el.clientHeight > THRESHOLD_PX);
    update();
    el.addEventListener("scroll", update, { passive: true });
    // Content grows while a run is in progress, and images or folds change its height.
    const resize = new ResizeObserver(update);
    resize.observe(el);
    for (const child of Array.from(el.children)) resize.observe(child);
    const mutations = new MutationObserver(update);
    mutations.observe(el, { childList: true, subtree: true });
    return () => {
      el.removeEventListener("scroll", update);
      resize.disconnect();
      mutations.disconnect();
    };
  }, [scroller]);

  if (!more) return null;
  return (
    <button
      type="button"
      onClick={() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" })}
      aria-label="Scroll to the bottom"
      title="Scroll to the bottom"
      data-testid="scroll-to-bottom"
      className="absolute bottom-4 left-1/2 z-20 print:hidden grid h-9 w-9 -translate-x-1/2 place-items-center rounded-full border border-line bg-surface text-ink shadow-lg transition hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-[18px] w-[18px]">
        <path d="M12 5v14" />
        <path d="m19 12-7 7-7-7" />
      </svg>
    </button>
  );
}
