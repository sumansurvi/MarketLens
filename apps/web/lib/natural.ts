// Plain-language display helpers. They change how values read on screen only: stored claims,
// evidence and validation results are untouched. Nothing internal (evidence IDs, field names)
// reaches the screen (D-86).

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO_DATE = /\b(\d{4})-(\d{2})-(\d{2})\b/g;
const ISO_RANGE = /\b(\d{4}-\d{2}-\d{2})\s*(?:→|->)\s*(\d{4}-\d{2}-\d{2})\b/g;
const EVIDENCE_ID = /\bev_[a-z_]+_[0-9a-f]{12}\b/g;
// "(evidence_id: ev_calc_…)", "(evidence_ids: ev_a, ev_b)", "[ev_…]" that a model left in a sentence.
const ID_PARENTHETICAL = /\s*[([]\s*(?:evidence[_ ]ids?\s*[:=]\s*)?(?:ev_[a-z_]+_[0-9a-f]{12}[\s,;]*(?:and\s+)?)+[)\]]/gi;
const ID_LABEL = /\s*\(?\bevidence[_ ]ids?\s*[:=]\s*\)?/gi;

function parts(iso: string): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return null;
  return [Number(m[1]), month, Number(m[3])];
}

/** "2026-09-30" → "Sep 30, 2026" (calendar dates are never shifted by a time zone). */
export function niceDate(iso: string | null | undefined, { year = true }: { year?: boolean } = {}): string {
  if (!iso) return "not reported";
  const p = parts(iso);
  if (!p) return iso;
  return `${MONTHS[p[1] - 1]} ${p[2]}${year ? `, ${p[0]}` : ""}`;
}

/** "Jun 30 – Sep 30, 2026", or both years when they differ. */
export function niceRange(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return start || end ? niceDate(start ?? end) : "not reported";
  const a = parts(start);
  const b = parts(end);
  if (!a || !b) return `${start} – ${end}`;
  return a[0] === b[0] ? `${niceDate(start, { year: false })} – ${niceDate(end)}` : `${niceDate(start)} – ${niceDate(end)}`;
}

/** Rewrite ISO dates and date ranges inside sentences so they read naturally. */
export function naturalize(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .replace(ID_PARENTHETICAL, "")
    .replace(EVIDENCE_ID, "")
    .replace(ID_LABEL, "")
    .replace(/\s*[—-]\s*us-gaap:[A-Za-z]+/g, "")
    .replace(/\bus-gaap:[A-Za-z]+/g, "")
    .replace(/\s+([.,;:])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .replace(ISO_RANGE, (_, a: string, b: string) => niceRange(a, b))
    .replace(ISO_DATE, (match: string) => niceDate(match));
}

/** Validation notes mention internal evidence IDs; readers only need to know a source was involved. */
export function withoutIds(text: string): string {
  return naturalize(
    text
      .replace(EVIDENCE_ID, "a source that was not retrieved")
      .replace(/cited evidence/g, "sources")
      .replace(/Numbers not found in the sources: ([^.]+)\./g, "These numbers don't appear in any source: $1."),
  );
}

/** Provider times without a zone (e.g. news) are shown as written, with that fact stated plainly. */
export function niceProviderTime(value: string | null | undefined, tzKnown: boolean | null | undefined): string {
  if (!value) return "not reported";
  if (tzKnown) return niceTimestamp(value);
  const date = niceDate(value.slice(0, 10));
  const time = /T?(\d{2}):(\d{2})/.exec(value.slice(10));
  if (!time) return date;
  const hour = Number(time[1]);
  const clock = `${hour % 12 === 0 ? 12 : hour % 12}:${time[2]} ${hour < 12 ? "AM" : "PM"}`;
  return `${date}, ${clock} (time zone not given)`;
}

let stampFormat: Intl.DateTimeFormat | null = null;

/** A full local timestamp, for tooltips and details. */
export function niceTimestamp(iso: string | null | undefined): string {
  if (!iso) return "not reported";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  stampFormat ??= new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return stampFormat.format(date);
}

/** "just now", "5 min ago", "3 h ago", "yesterday", then a date. Rendered from client-fetched data only. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "not reported";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 172_800) return "yesterday";
  return niceDate(new Date(then).toISOString().slice(0, 10), { year: seconds > 300 * 86_400 });
}

/** "1:05" or "12 s" for an elapsed duration. */
export function elapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return "—";
  const total = Math.round(ms / 1000);
  if (total < 60) return `${total} s`;
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")} min`;
}

export const CATEGORY_LABEL: Record<string, string> = {
  market: "Market prices",
  fundamentals: "Financials",
  filings: "SEC filings",
  news: "News",
};

export function categoryLabel(category: string | null | undefined): string {
  if (!category) return "";
  return CATEGORY_LABEL[category] ?? category.charAt(0).toUpperCase() + category.slice(1);
}

/** "market prices and news" — a readable list of affected categories. */
export function listCategories(categories: string[]): string {
  const names = categories.map((c) => categoryLabel(c).toLowerCase());
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** The model name from the reviewer label, e.g. "evidence_risk_reviewer (qwen2.5:7b) + …" → "qwen2.5:7b". */
export function modelName(reviewer: string | null | undefined): string | null {
  const m = reviewer ? /\(([^)]+)\)/.exec(reviewer) : null;
  return m ? m[1] : null;
}
