"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import type { EvidenceItem, ResearchClaim } from "@/lib/api/types";

import { EvidenceInspector } from "./evidence-inspector";

/** Evidence selection + inspector panel, shared by live runs and saved snapshots. On wide screens the
 * panel docks beside the brief (both stay readable); on smaller screens it slides over it. */
export function useEvidenceInspector(evidenceItems: EvidenceItem[], claims: ResearchClaim[]) {
  const [selected, setSelected] = useState<string | null>(null);
  const lastTrigger = useRef<HTMLElement | null>(null);
  const evidence = useMemo(() => new Map(evidenceItems.map((e) => [e.evidence_id, e])), [evidenceItems]);

  const select = useCallback((id: string, trigger: HTMLElement) => {
    lastTrigger.current = trigger;
    setSelected(id);
  }, []);
  const close = useCallback(() => {
    setSelected(null);
    lastTrigger.current?.focus();
  }, []);

  const item = selected ? evidence.get(selected) : undefined;
  const panel = item ? (
    <>
      <div className="fixed inset-0 z-30 bg-black/30 xl:hidden print:hidden" aria-hidden="true" onClick={close} />
      <aside
        aria-label="Evidence inspector"
        className="print:!hidden fixed inset-y-0 right-0 z-30 w-full max-w-md overflow-hidden border-l border-line bg-surface shadow-2xl xl:static xl:z-auto xl:w-[26rem] xl:max-w-none xl:shrink-0 xl:shadow-none"
      >
        <EvidenceInspector item={item} evidence={evidence} claims={claims} onSelect={select} onClose={close} />
      </aside>
    </>
  ) : null;

  return { evidence, selected, select, panel, open: Boolean(item) };
}
