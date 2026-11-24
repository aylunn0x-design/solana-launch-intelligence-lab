/**
 * Serializable value-format descriptors for charts.
 *
 * Server components cannot pass functions to client components across the RSC
 * boundary, so charts take a *name* for how to format a value and resolve it to
 * a function on the client. This keeps the formatting rules in one place and
 * makes the chart props serializable.
 */

import { count, decimal, duration, percent, usd } from "@/lib/format";

export type ValueFormat =
  | "usd"
  | "count"
  | "decimal"
  | "duration"
  | "percent"
  | "percentPoints"
  | "raw";

const FORMATTERS: Record<ValueFormat, (value: unknown) => string> = {
  usd: (value) => usd(toNumber(value)),
  count: (value) => count(toNumber(value)),
  decimal: (value) => decimal(toNumber(value), 3),
  duration: (value) => duration(toNumber(value)),
  // A 0-1 proportion rendered as a percentage.
  percent: (value) => percent(toNumber(value)),
  // A value already expressed in percentage points.
  percentPoints: (value) => percent(toNumber(value), { alreadyPercent: true }),
  raw: (value) => (value === null || value === undefined ? "—" : String(value)),
};

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function resolveFormatter(format: ValueFormat = "raw") {
  return FORMATTERS[format] ?? FORMATTERS.raw;
}

/** Pick a sensible format from a feature's name. */
export function formatForVariable(name: string): ValueFormat {
  if (name.endsWith("_usd")) return "usd";
  if (name.endsWith("_hours")) return "duration";
  if (name.endsWith("_pct")) return "percentPoints";
  if (
    name.endsWith("_count") ||
    name.startsWith("active_") ||
    name.endsWith("_txns") ||
    name.endsWith("_traders")
  ) {
    return "count";
  }
  return "decimal";
}
