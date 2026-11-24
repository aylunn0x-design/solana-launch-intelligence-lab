"use client";

/**
 * Shared chart chrome: the frame, the tooltip, the legend, the table view.
 *
 * These exist so every chart in the app obeys the same rules:
 *   - a title that says what is plotted, and axis labels with units
 *   - a tooltip on every chart that has marks (hover + keyboard focus)
 *   - a legend whenever there are two or more series
 *   - an accessible table view of the same numbers, toggleable
 *   - an explicit empty state instead of blank axes
 *
 * Mark specs (2px lines, >=8px markers, 24px bar cap, 2px surface gaps,
 * recessive hairline grid) are applied by the chart components themselves.
 */

import { useId, useState, type ReactNode } from "react";
import { Table2 } from "lucide-react";

/** Categorical series colours, in fixed validated order. Never cycled. */
export const SERIES = [
  "var(--color-series-1)",
  "var(--color-series-2)",
  "var(--color-series-3)",
  "var(--color-series-4)",
  "var(--color-series-5)",
  "var(--color-series-6)",
  "var(--color-series-7)",
  "var(--color-series-8)",
] as const;

/** Beyond this, series fold into "Other" rather than inventing hues. */
export const MAX_SERIES = SERIES.length;
/** Scatter/all-pairs charts are capped at 3: only those validate all-pairs. */
export const MAX_SERIES_ALL_PAIRS = 3;

export const AXIS = {
  stroke: "var(--color-border)",
  tick: { fill: "var(--color-ink-muted)", fontSize: 11 },
};

export const GRID = {
  stroke: "var(--color-border)",
  strokeDasharray: "0", // solid hairline, never dashed
  vertical: false,
};

export function seriesColor(index: number): string {
  return SERIES[index % SERIES.length] ?? SERIES[0];
}

/* -------------------------------------------------------------------------- */

export function ChartFrame({
  title,
  subtitle,
  yLabel,
  xLabel,
  children,
  tableView,
  footnote,
  height = 280,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  yLabel?: string;
  xLabel?: string;
  children: ReactNode;
  /** The same numbers as a table: the non-visual access path. */
  tableView?: ReactNode;
  footnote?: ReactNode;
  height?: number;
  actions?: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();

  return (
    <figure className="m-0">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <figcaption className="text-[13px] font-semibold text-ink">{title}</figcaption>
          {subtitle ? (
            <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{subtitle}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {actions}
          {tableView ? (
            <button
              type="button"
              onClick={() => setShowTable((v) => !v)}
              aria-expanded={showTable}
              aria-controls={tableId}
              className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px] text-ink-muted transition-colors hover:border-border-strong hover:text-ink-secondary"
            >
              <Table2 size={11} aria-hidden="true" />
              {showTable ? "Hide data" : "View data"}
            </button>
          ) : null}
        </div>
      </div>

      {yLabel ? (
        <div className="mb-1 text-[10px] uppercase tracking-wider text-ink-muted">
          {yLabel}
        </div>
      ) : null}

      {/* overflow-hidden here (not on the recharts wrapper) keeps long axis
          labels from widening the page, without interfering with
          ResponsiveContainer's measurement of its own element. */}
      <div style={{ height }} className="w-full min-w-0 overflow-hidden">
        {children}
      </div>

      {xLabel ? (
        <div className="mt-1 text-center text-[10px] uppercase tracking-wider text-ink-muted">
          {xLabel}
        </div>
      ) : null}

      {footnote ? (
        <p className="mt-2.5 text-xs leading-relaxed text-ink-muted">{footnote}</p>
      ) : null}

      {tableView && showTable ? (
        <div id={tableId} className="mt-3 max-h-72 overflow-auto rounded border border-border">
          {tableView}
        </div>
      ) : null}
    </figure>
  );
}

/** The one tooltip shape used by every chart. */
export function ChartTooltip({
  active,
  payload,
  label,
  formatter,
  labelFormatter,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: unknown; color?: string; dataKey?: string }>;
  label?: unknown;
  formatter?: (value: unknown, name: string) => string;
  labelFormatter?: (label: unknown) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="pointer-events-none rounded-md border border-border-strong bg-raised px-2.5 py-2 shadow-xl">
      {label !== undefined && label !== null ? (
        <div className="mb-1 text-[11px] font-medium text-ink-secondary">
          {labelFormatter ? labelFormatter(label) : String(label)}
        </div>
      ) : null}
      <div className="space-y-0.5">
        {payload.map((entry, i) => {
          const name = entry.name ?? entry.dataKey ?? "value";
          return (
            <div key={i} className="flex items-center gap-2 text-[11px]">
              <span
                aria-hidden="true"
                className="h-2 w-2 shrink-0 rounded-sm"
                style={{ background: entry.color }}
              />
              <span className="text-ink-muted">{name}</span>
              <span className="tnum ml-auto pl-2 font-medium text-ink">
                {formatter ? formatter(entry.value, String(name)) : String(entry.value)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Legend: required for two or more series. Identity never by colour alone. */
export function Legend({
  items,
}: {
  items: Array<{ label: string; color: string; note?: string }>;
}) {
  if (items.length < 2) return null;
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5 text-[11px] text-ink-secondary">
          <span
            aria-hidden="true"
            className="h-2 w-2 shrink-0 rounded-sm"
            style={{ background: item.color }}
          />
          <span>{item.label}</span>
          {item.note ? <span className="text-ink-muted">({item.note})</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function ChartEmpty({
  message,
  height = 280,
}: {
  message: string;
  height?: number;
}) {
  return (
    <div
      style={{ height }}
      className="flex items-center justify-center rounded border border-dashed border-border px-6 text-center"
    >
      <p className="max-w-md text-xs leading-relaxed text-ink-muted">{message}</p>
    </div>
  );
}

/** Minimal table used as the accessible data view under charts. */
export function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Array<Array<ReactNode>>;
}) {
  // The table owns its horizontal scrolling: these tables are used directly
  // inside panels as well as under charts, and a wide one would otherwise
  // widen the whole page on a narrow screen.
  return (
    <div className="w-full overflow-x-auto">
    <table className="w-full border-collapse text-left text-[11px]">
      <thead className="sticky top-0 bg-surface">
        <tr>
          {columns.map((column) => (
            <th
              key={column}
              scope="col"
              className="border-b border-border px-2.5 py-1.5 font-semibold uppercase tracking-wider text-ink-muted"
            >
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className="odd:bg-panel/40">
            {row.map((cell, j) => (
              <td key={j} className="tnum border-b border-border/50 px-2.5 py-1 text-ink-secondary">
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}
