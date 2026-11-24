/**
 * Formatting helpers.
 *
 * The rule throughout: **null means "no data" and renders as an em dash, never
 * as 0.** A zero and a missing observation mean different things in this
 * project, and the UI must not blur them.
 */

const EM_DASH = "—";

/** Compact USD. Token liquidity spans $0 to $10M+, so SI suffixes are required. */
export function usd(value: number | null | undefined, opts?: { precise?: boolean }): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  if (opts?.precise) {
    return `$${value.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }
  const abs = Math.abs(value);
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(value / 1e3).toFixed(1)}K`;
  if (abs >= 1) return `$${value.toFixed(2)}`;
  if (abs === 0) return "$0";
  // Sub-dollar token prices need significant digits, not 2 decimal places.
  return `$${value.toPrecision(3)}`;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return value.toLocaleString("en-US");
}

export function decimal(value: number | null | undefined, places = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return value.toFixed(places);
}

export function percent(
  value: number | null | undefined,
  opts?: { alreadyPercent?: boolean; places?: number },
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  const scaled = opts?.alreadyPercent ? value : value * 100;
  return `${scaled.toFixed(opts?.places ?? 1)}%`;
}

/** Signed percentage for directional values, e.g. 24h price change. */
export function signedPercent(value: number | null | undefined, places = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(places)}%`;
}

/** Hours as a human duration: 0.5h, 18h, 3.2d. */
export function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return EM_DASH;
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

/** Shorten a base58 address for display. Full value belongs in a title/copy. */
export function shortAddress(address: string | null | undefined, chars = 4): string {
  if (!address) return EM_DASH;
  if (address.length <= chars * 2 + 3) return address;
  return `${address.slice(0, chars)}…${address.slice(-chars)}`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return EM_DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EM_DASH;
  return date.toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
}

export function dateOnly(iso: string | null | undefined): string {
  if (!iso) return EM_DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EM_DASH;
  return date.toLocaleDateString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    timeZone: "UTC",
  });
}

/** "3 minutes ago" / "in 2 hours". Used for ingestion freshness. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return EM_DASH;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return EM_DASH;
  const seconds = (Date.now() - then) / 1000;
  const abs = Math.abs(seconds);
  const suffix = seconds >= 0 ? "ago" : "from now";
  if (abs < 60) return `${Math.round(abs)}s ${suffix}`;
  if (abs < 3600) return `${Math.round(abs / 60)}m ${suffix}`;
  if (abs < 86400) return `${Math.round(abs / 3600)}h ${suffix}`;
  return `${Math.round(abs / 86400)}d ${suffix}`;
}

/** Format a p-value the way papers do: never "0.000". */
export function pValue(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  if (value < 0.0001) return "< 0.0001";
  return value.toFixed(4);
}

/** A confidence interval as a bracketed pair, or a clear "not computed". */
export function interval(
  lower: number | null | undefined,
  upper: number | null | undefined,
  places = 3,
): string {
  if (
    lower === null ||
    lower === undefined ||
    upper === null ||
    upper === undefined ||
    !Number.isFinite(lower) ||
    !Number.isFinite(upper)
  ) {
    return "not computed";
  }
  return `[${lower.toFixed(places)}, ${upper.toFixed(places)}]`;
}

/** Human label for a snake_case variable name. */
export function humanize(name: string): string {
  return name
    .replace(/_usd$/, " (USD)")
    .replace(/_pct$/, " (%)")
    .replace(/_hours$/, " (hours)")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/Usd/g, "USD")
    .replace(/Cv\b/g, "CV")
    .replace(/Fdv/g, "FDV")
    .replace(/Dex/g, "DEX");
}

export { EM_DASH };
