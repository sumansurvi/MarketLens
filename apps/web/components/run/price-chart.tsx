"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { formatInt, formatPct, formatUsd } from "@/lib/format";
import { niceDate } from "@/lib/natural";

export interface ChartBar {
  trading_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
}

export interface ChartMarker {
  date: string;
  pct: number;
  kind: "decline" | "gain";
}

const HEIGHT = 230;
const MARGIN = { top: 22, right: 70, bottom: 30, left: 56 };
const MIN_LABEL_GAP = 52;

function niceStep(range: number, targetTicks: number): number {
  const raw = range / Math.max(1, targetTicks);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const residual = raw / magnitude;
  const nice = residual >= 5 ? 10 : residual >= 2 ? 5 : residual >= 1 ? 2 : 1;
  return nice * magnitude;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Single-series daily-close line chart. One series → no legend (the title names it).
 * Extreme-day markers are labeled with glyph + text, so color is never the only cue.
 * The crosshair snaps to trading days; arrow keys move it for keyboard users.
 */
export function PriceChart({
  symbol,
  bars,
  markers,
  subtitle,
}: {
  symbol: string;
  bars: ChartBar[];
  markers: ChartMarker[];
  subtitle: string;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [active, setActive] = useState<number | null>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.max(300, Math.floor(entries[0].contentRect.width));
      setWidth(next);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const geometry = useMemo(() => {
    const closes = bars.map((b) => b.close);
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const pad = (max - min) * 0.08 || 1;
    const step = niceStep(max - min + pad * 2, 4);
    const lo = Math.floor((min - pad) / step) * step;
    const hi = Math.ceil((max + pad) / step) * step;
    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;
    const x = (i: number) => MARGIN.left + (bars.length === 1 ? innerW / 2 : (i / (bars.length - 1)) * innerW);
    const y = (v: number) => MARGIN.top + innerH - ((v - lo) / (hi - lo)) * innerH;
    const ticks: number[] = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(6)));
    // One label per month start; a label closer than MIN_LABEL_GAP to the next one is
    // dropped (the later month wins) and the first kept label carries the year.
    const candidates: { index: number; month: string; year: string }[] = [];
    bars.forEach((bar, i) => {
      const [yy, mm] = bar.trading_date.split("-");
      if (i === 0 || bars[i - 1].trading_date.split("-")[1] !== mm) candidates.push({ index: i, month: mm, year: yy });
    });
    const kept = candidates.filter((c, k) => k === candidates.length - 1 || x(candidates[k + 1].index) - x(c.index) >= MIN_LABEL_GAP);
    const monthStarts = kept.map((c, k) => ({
      index: c.index,
      label: MONTHS[Number(c.month) - 1] + (k === 0 || c.month === "01" ? ` ${c.year}` : ""),
    }));
    const path = bars.map((b, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(b.close).toFixed(1)}`).join("");
    return { x, y, ticks, monthStarts, path, innerW, innerH };
  }, [bars, width]);

  if (bars.length === 0) return null;
  const last = bars[bars.length - 1];
  const markerPoints = markers
    .map((m) => ({ ...m, index: bars.findIndex((b) => b.trading_date === m.date) }))
    .filter((m) => m.index >= 0);

  function indexFromPointer(clientX: number, target: Element): number {
    const rect = target.getBoundingClientRect();
    const px = ((clientX - rect.left) / rect.width) * width;
    const ratio = (px - MARGIN.left) / geometry.innerW;
    return Math.max(0, Math.min(bars.length - 1, Math.round(ratio * (bars.length - 1))));
  }

  function onKeyDown(event: React.KeyboardEvent) {
    const current = active ?? bars.length - 1;
    const moves: Record<string, number> = { ArrowLeft: current - 1, ArrowRight: current + 1, Home: 0, End: bars.length - 1 };
    if (event.key in moves) {
      event.preventDefault();
      setActive(Math.max(0, Math.min(bars.length - 1, moves[event.key])));
    } else if (event.key === "Escape") {
      setActive(null);
    }
  }

  const hovered = active !== null ? bars[active] : null;
  const tooltipLeft = active !== null ? (geometry.x(active) / width) * 100 : 0;

  return (
    <figure className="m-0">
      <figcaption className="mb-2">
        <p id={titleId} className="text-sm font-semibold text-ink">
          Daily close (USD), {symbol}
        </p>
        <p id={descId} className="text-xs text-ink-3">
          {subtitle}
        </p>
      </figcaption>
      <div ref={wrapRef} className="relative w-full">
        <svg
          viewBox={`0 0 ${width} ${HEIGHT}`}
          width="100%"
          height={HEIGHT}
          role="img"
          aria-labelledby={`${titleId} ${descId}`}
          tabIndex={0}
          onKeyDown={onKeyDown}
          onPointerMove={(e) => setActive(indexFromPointer(e.clientX, e.currentTarget))}
          onPointerLeave={() => setActive(null)}
          onBlur={() => setActive(null)}
          className="block touch-none select-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]"
        >
          {geometry.ticks.map((t) => (
            <g key={t}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={geometry.y(t)} y2={geometry.y(t)} stroke="var(--hairline)" strokeWidth={1} />
              <text x={MARGIN.left - 8} y={geometry.y(t)} dy="0.32em" textAnchor="end" className="tabular" fontSize={11} fill="var(--ink-3)">
                {formatUsd(t, t % 1 === 0 ? 0 : 2)}
              </text>
            </g>
          ))}
          <line
            x1={MARGIN.left}
            x2={width - MARGIN.right}
            y1={MARGIN.top + geometry.innerH}
            y2={MARGIN.top + geometry.innerH}
            stroke="var(--baseline)"
            strokeWidth={1}
          />
          {geometry.monthStarts.map((m) => (
            <text key={m.index} x={geometry.x(m.index)} y={HEIGHT - 8} textAnchor="middle" fontSize={11} fill="var(--ink-3)">
              {m.label}
            </text>
          ))}
          <path d={geometry.path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {markerPoints.map((m) => {
            const cx = geometry.x(m.index);
            const cy = geometry.y(bars[m.index].close);
            const above = m.kind === "gain";
            return (
              <g key={`${m.kind}-${m.date}`}>
                <circle cx={cx} cy={cy} r={6} fill="var(--surface)" />
                <circle cx={cx} cy={cy} r={4.5} fill={m.kind === "decline" ? "var(--series-decline)" : "var(--series-1)"} />
                <text
                  x={cx}
                  y={above ? cy - 12 : cy + 20}
                  textAnchor="middle"
                  fontSize={11}
                  fontWeight={600}
                  fill="var(--ink-2)"
                  paintOrder="stroke"
                  stroke="var(--surface)"
                  strokeWidth={3}
                >
                  {m.kind === "decline" ? "▼" : "▲"} {formatPct(m.pct, false)} · {niceDate(m.date, { year: false })}
                </text>
              </g>
            );
          })}
          <circle cx={geometry.x(bars.length - 1)} cy={geometry.y(last.close)} r={4} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} />
          <text x={geometry.x(bars.length - 1) + 8} y={geometry.y(last.close)} dy="0.32em" fontSize={11} fontWeight={600} fill="var(--ink)">
            {formatUsd(last.close)}
          </text>
          {active !== null && (
            <g pointerEvents="none">
              <line
                x1={geometry.x(active)}
                x2={geometry.x(active)}
                y1={MARGIN.top}
                y2={MARGIN.top + geometry.innerH}
                stroke="var(--ink-3)"
                strokeWidth={1}
              />
              <circle cx={geometry.x(active)} cy={geometry.y(bars[active].close)} r={4.5} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hovered && (
          <div
            className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs shadow-sm"
            style={{ left: `${Math.min(88, Math.max(12, tooltipLeft))}%` }}
            role="status"
          >
            <p className="text-sm font-semibold text-ink">{formatUsd(hovered.close)}</p>
            <p className="flex items-center gap-1.5 text-ink-3">
              <span className="inline-block h-0.5 w-3 rounded bg-[var(--series-1)]" aria-hidden="true" />
              Close · {hovered.trading_date}
            </p>
          </div>
        )}
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-ink-2 hover:text-ink">Show data table ({bars.length} trading days)</summary>
        <div className="mt-2 max-h-64 overflow-auto rounded-md border border-line">
          <table className="w-full text-left tabular">
            <caption className="sr-only">Daily prices for {symbol}</caption>
            <thead className="sticky top-0 bg-sunken text-ink-2">
              <tr>
                <th className="px-2 py-1 font-medium">Trading date</th>
                <th className="px-2 py-1 text-right font-medium">Open</th>
                <th className="px-2 py-1 text-right font-medium">High</th>
                <th className="px-2 py-1 text-right font-medium">Low</th>
                <th className="px-2 py-1 text-right font-medium">Close</th>
                <th className="px-2 py-1 text-right font-medium">Volume</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((b) => (
                <tr key={b.trading_date} className="border-t border-hairline">
                  <td className="px-2 py-1">{b.trading_date}</td>
                  <td className="px-2 py-1 text-right">{formatUsd(b.open)}</td>
                  <td className="px-2 py-1 text-right">{formatUsd(b.high)}</td>
                  <td className="px-2 py-1 text-right">{formatUsd(b.low)}</td>
                  <td className="px-2 py-1 text-right">{formatUsd(b.close)}</td>
                  <td className="px-2 py-1 text-right">{formatInt(b.volume)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
