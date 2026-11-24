"use client";

/**
 * Explorer filter bar.
 *
 * Filters live in the URL so a filtered view is shareable, survives reload and
 * works with browser back/forward. The component writes to the query string and
 * lets the server component re-render; it keeps no duplicate state of its own
 * apart from the debounced search box.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { Search, X } from "lucide-react";

const COHORTS = [
  { value: "all", label: "All pools" },
  { value: "inception", label: "Inception cohort (unbiased)" },
  { value: "retrospective", label: "Retrospective (biased)" },
] as const;

const SORTS = [
  { value: "launch", label: "Launch time" },
  { value: "discovered", label: "Discovery time" },
  { value: "liquidity", label: "Latest liquidity" },
  { value: "symbol", label: "Symbol" },
] as const;

const AGE_PRESETS = [
  { value: "", label: "Any age" },
  { value: "1", label: "< 1 hour" },
  { value: "24", label: "< 24 hours" },
  { value: "168", label: "< 7 days" },
  { value: "720", label: "< 30 days" },
] as const;

const selectClass =
  "rounded border border-border bg-panel px-2 py-1.5 text-xs text-ink transition-colors hover:border-border-strong focus:border-accent";

export function ExplorerFilters({ dexes }: { dexes: Array<{ dex: string; pool_count: number }> }) {
  const router = useRouter();
  const params = useSearchParams();
  const urlQuery = params.get("q") ?? "";
  const [query, setQuery] = useState(urlQuery);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep the box in step when navigation changes the URL (e.g. the back
  // button). Adjusting state during render on a changed input is the
  // documented pattern; an effect would briefly show the stale text.
  const [lastUrlQuery, setLastUrlQuery] = useState(urlQuery);
  if (urlQuery !== lastUrlQuery) {
    setLastUrlQuery(urlQuery);
    setQuery(urlQuery);
  }

  const update = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(changes)) {
        if (value === null || value === "") next.delete(key);
        else next.set(key, value);
      }
      // Any filter change returns to page 1: staying on page 7 of a new result
      // set is almost always wrong.
      if (!("page" in changes)) next.delete("page");
      router.push(`/explorer?${next.toString()}`);
    },
    [params, router],
  );

  const onSearchChange = (value: string) => {
    setQuery(value);
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => update({ q: value || null }), 350);
  };

  const activeCount = ["q", "dex", "cohort", "max_age_hours", "has_history", "min_liquidity_usd"]
    .filter((key) => params.get(key))
    .length;

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <div className="relative min-w-[220px] flex-1">
        <Search
          size={13}
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted"
        />
        <input
          type="search"
          value={query}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search name, symbol, mint or pool address…"
          aria-label="Search tokens"
          className="w-full rounded border border-border bg-panel py-1.5 pl-8 pr-2 text-xs text-ink placeholder:text-ink-muted focus:border-accent"
        />
      </div>

      <label className="sr-only" htmlFor="cohort">Cohort</label>
      <select
        id="cohort"
        value={params.get("cohort") ?? "all"}
        onChange={(event) => update({ cohort: event.target.value })}
        className={selectClass}
      >
        {COHORTS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="dex">Venue</label>
      <select
        id="dex"
        value={params.get("dex") ?? ""}
        onChange={(event) => update({ dex: event.target.value || null })}
        className={selectClass}
      >
        <option value="">All venues</option>
        {dexes.map((item) => (
          <option key={item.dex} value={item.dex}>
            {item.dex} ({item.pool_count})
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="age">Maximum age</label>
      <select
        id="age"
        value={params.get("max_age_hours") ?? ""}
        onChange={(event) => update({ max_age_hours: event.target.value || null })}
        className={selectClass}
      >
        {AGE_PRESETS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <label className="flex items-center gap-1.5 rounded border border-border bg-panel px-2 py-1.5 text-xs text-ink-secondary">
        <input
          type="checkbox"
          checked={params.get("has_history") === "true"}
          onChange={(event) => update({ has_history: event.target.checked ? "true" : null })}
          className="accent-accent"
        />
        Has history
      </label>

      <label className="sr-only" htmlFor="sort">Sort by</label>
      <select
        id="sort"
        value={params.get("sort") ?? "launch"}
        onChange={(event) => update({ sort: event.target.value })}
        className={selectClass}
      >
        {SORTS.map((option) => (
          <option key={option.value} value={option.value}>
            Sort: {option.label}
          </option>
        ))}
      </select>

      <button
        type="button"
        onClick={() => update({ order: params.get("order") === "asc" ? "desc" : "asc" })}
        className="rounded border border-border bg-panel px-2 py-1.5 text-xs text-ink-secondary transition-colors hover:border-border-strong"
        aria-label={`Sort ${params.get("order") === "asc" ? "descending" : "ascending"}`}
      >
        {params.get("order") === "asc" ? "↑ Asc" : "↓ Desc"}
      </button>

      {activeCount > 0 ? (
        <button
          type="button"
          onClick={() => router.push("/explorer")}
          className="inline-flex items-center gap-1 rounded border border-border px-2 py-1.5 text-xs text-ink-muted transition-colors hover:border-border-strong hover:text-ink-secondary"
        >
          <X size={11} aria-hidden="true" />
          Clear {activeCount}
        </button>
      ) : null}
    </div>
  );
}

/** Pagination control that preserves the current filters. */
export function Pagination({
  page,
  totalPages,
  total,
}: {
  page: number;
  totalPages: number;
  total: number;
}) {
  const router = useRouter();
  const params = useSearchParams();

  const goTo = (target: number) => {
    const next = new URLSearchParams(params.toString());
    if (target <= 1) next.delete("page");
    else next.set("page", String(target));
    router.push(`/explorer?${next.toString()}`);
  };

  if (total === 0) return null;

  return (
    <nav
      aria-label="Pagination"
      className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3"
    >
      <p className="tnum text-xs text-ink-muted">
        Page {page} of {Math.max(1, totalPages)} · {total.toLocaleString("en-US")} pools
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => goTo(page - 1)}
          disabled={page <= 1}
          className="rounded border border-border px-2.5 py-1 text-xs text-ink-secondary transition-colors hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => goTo(page + 1)}
          disabled={page >= totalPages}
          className="rounded border border-border px-2.5 py-1 text-xs text-ink-secondary transition-colors hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </nav>
  );
}
