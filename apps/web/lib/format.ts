// Display helpers. Internal timestamps are UTC ISO-8601; they are shown in the
// viewer's local time zone with an explicit zone label. Trading and period dates
// are calendar dates and are shown as-is (never shifted by a time zone).

// dateStyle/timeStyle cannot be combined with timeZoneName, so the fields are explicit.
let timestampFormat: Intl.DateTimeFormat | null = null;

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "not reported";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  timestampFormat ??= new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return timestampFormat.format(date);
}

/** Provider timestamps that carry no zone (e.g. news) are shown verbatim and labeled. */
export function formatProviderTime(value: string | null | undefined, tzKnown: boolean | null | undefined): string {
  if (!value) return "not reported";
  if (tzKnown) return formatTimestamp(value);
  return `${value.replace("T", " ").slice(0, 16)} (time zone not specified)`;
}

export function formatDate(value: string | null | undefined): string {
  return value ?? "not reported";
}

export function formatUsd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatCompactUsd(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  return formatUsd(value);
}

export function formatPct(value: number | null | undefined, signed = true): string {
  if (value === null || value === undefined) return "—";
  const text = `${Math.abs(value).toFixed(2)}%`;
  if (!signed) return text;
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : text;
}

export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
