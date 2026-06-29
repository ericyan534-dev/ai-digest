/** Small pure formatting helpers (dates, hostnames). No external deps. */

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function parseDate(value: string): Date | null {
  // Accept "YYYY-MM-DD" (treat as UTC midnight) or full ISO datetimes.
  const isoDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const d = new Date(isoDateOnly ? `${value}T00:00:00Z` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Sunday, Jun 21, 2026" — used for the masthead dateline. */
export function formatDateline(value: string): string {
  const d = parseDate(value);
  if (!d) return value;
  const weekday = WEEKDAYS[d.getUTCDay()];
  return `${weekday}, ${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

/** "Jun 21, 2026" — compact date for cards/lists. */
export function formatShortDate(value: string): string {
  const d = parseDate(value);
  if (!d) return value;
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

/** ISO date in the server-ish local sense: YYYY-MM-DD for "today". */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Hostname for a link, e.g. "arxiv.org". Falls back to the raw string. */
export function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
