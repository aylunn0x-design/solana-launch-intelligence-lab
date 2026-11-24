"use client";

/**
 * The chart library for this app.
 *
 * Each chart is chosen by the job its data does, not by variety:
 *   TimeSeriesChart  - change over time (liquidity, price) -> line, crosshair
 *   BarSeriesChart   - magnitude by category / per-bucket counts -> columns
 *   HistogramChart   - a distribution -> columns on bin edges
 *   SurvivalChart    - a step function with a CI band -> stepped line + area
 *   CorrelationHeatmap - polarity of a matrix -> diverging blue<->red, grey mid
 *   ScatterChart     - relationship between two variables -> dots
 *
 * Rules enforced here: no dual axes anywhere, 2px lines, >=8px markers with a
 * 2px surface ring, bars capped at 24px with 4px rounded data-ends, hairline
 * solid grid, tooltips on everything, legend at >=2 series, table view provided
 * by the caller via ChartFrame.
 */

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import {
  AXIS,
  ChartEmpty,
  ChartTooltip,
  GRID,
  Legend,
  seriesColor,
} from "./chrome";
import { resolveFormatter, type ValueFormat } from "./formats";
import { humanize } from "@/lib/format";

const MARGIN = { top: 8, right: 16, bottom: 4, left: 4 };

/* -------------------------------------------------------------------------- */
/* Time series                                                                */
/* -------------------------------------------------------------------------- */

export interface TimePoint {
  t: number; // epoch ms
  [key: string]: number | null;
}

export function TimeSeriesChart({
  data,
  series,
  valueFormat = "raw",
  emptyMessage = "No data points to plot.",
  height = 280,
  area = false,
}: {
  data: TimePoint[];
  series: Array<{ key: string; label: string }>;
  valueFormat?: ValueFormat;
  emptyMessage?: string;
  height?: number;
  area?: boolean;
}) {
  if (!data.length) return <ChartEmpty message={emptyMessage} height={height} />;
  const valueFormatter = resolveFormatter(valueFormat);

  const fmtTime = (value: unknown) => {
    const date = new Date(Number(value));
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString("en-GB", {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    });
  };

  const Chart = area ? AreaChart : LineChart;

  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <Chart data={data} margin={MARGIN}>
          <CartesianGrid {...GRID} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={fmtTime}
            stroke={AXIS.stroke}
            tick={AXIS.tick}
            minTickGap={48}
          />
          <YAxis
            stroke={AXIS.stroke}
            tick={AXIS.tick}
            tickFormatter={(v) => valueFormatter(v)}
            width={66}
          />
          <Tooltip
            content={
              <ChartTooltip formatter={valueFormatter} labelFormatter={fmtTime} />
            }
            cursor={{ stroke: "var(--color-border-strong)", strokeWidth: 1 }}
          />
          {series.map((s, i) =>
            area ? (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={seriesColor(i)}
                strokeWidth={2}
                // Area fill is a ~10% wash, never a saturated block.
                fill={seriesColor(i)}
                fillOpacity={0.1}
                dot={false}
                activeDot={{
                  r: 4,
                  strokeWidth: 2,
                  stroke: "var(--color-panel)",
                }}
                connectNulls={false}
              />
            ) : (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={seriesColor(i)}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                dot={false}
                // >=8px marker (r=4) with a 2px surface ring so it stays legible.
                activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--color-panel)" }}
                connectNulls={false}
              />
            ),
          )}
        </Chart>
      </ResponsiveContainer>
      <Legend
        items={series.map((s, i) => ({ label: s.label, color: seriesColor(i) }))}
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Bars                                                                       */
/* -------------------------------------------------------------------------- */

export function BarSeriesChart({
  data,
  xKey,
  series,
  valueFormat = "raw",
  emptyMessage = "No data to plot.",
  height = 280,
  horizontal = false,
  colorByIndex = false,
}: {
  data: Array<Record<string, string | number | null>>;
  xKey: string;
  series: Array<{ key: string; label: string }>;
  valueFormat?: ValueFormat;
  emptyMessage?: string;
  height?: number;
  horizontal?: boolean;
  /** Colour each bar by its own slot (only for a single-series category chart). */
  colorByIndex?: boolean;
}) {
  if (!data.length) return <ChartEmpty message={emptyMessage} height={height} />;
  const valueFormatter = resolveFormatter(valueFormat);

  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={data}
          margin={MARGIN}
          layout={horizontal ? "vertical" : "horizontal"}
          // 2px of surface between adjacent bars, per the spacer rule.
          barGap={2}
          barCategoryGap="20%"
        >
          <CartesianGrid {...GRID} vertical={horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis
                type="number"
                stroke={AXIS.stroke}
                tick={AXIS.tick}
                tickFormatter={(v) => valueFormatter(v)}
              />
              <YAxis
                type="category"
                dataKey={xKey}
                stroke={AXIS.stroke}
                tick={AXIS.tick}
                width={110}
              />
            </>
          ) : (
            <>
              <XAxis
                dataKey={xKey}
                stroke={AXIS.stroke}
                tick={AXIS.tick}
                interval="preserveStartEnd"
              />
              <YAxis
                stroke={AXIS.stroke}
                tick={AXIS.tick}
                tickFormatter={(v) => valueFormatter(v)}
                width={66}
              />
            </>
          )}
          <Tooltip
            content={<ChartTooltip formatter={valueFormatter} />}
            cursor={{ fill: "var(--color-raised)", fillOpacity: 0.5 }}
          />
          {series.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              fill={seriesColor(i)}
              // 24px cap: never let a bar fill its whole band.
              maxBarSize={24}
              // 4px rounded data-end, square at the baseline.
              radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
            >
              {colorByIndex && series.length === 1
                ? data.map((_, index) => (
                    <Cell key={index} fill={seriesColor(index)} />
                  ))
                : null}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
      <Legend items={series.map((s, i) => ({ label: s.label, color: seriesColor(i) }))} />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Histogram                                                                  */
/* -------------------------------------------------------------------------- */

export function HistogramChart({
  binEdges,
  counts,
  logScale = false,
  height = 260,
  emptyMessage = "Not enough data to plot a distribution.",
}: {
  binEdges: number[];
  counts: number[];
  logScale?: boolean;
  height?: number;
  emptyMessage?: string;
}) {
  if (!counts.length || binEdges.length < 2) {
    return <ChartEmpty message={emptyMessage} height={height} />;
  }

  const data = counts.map((n, i) => {
    const lo = binEdges[i] ?? 0;
    const hi = binEdges[i + 1] ?? lo;
    const fmt = (v: number) =>
      logScale
        ? `1e${v.toFixed(1)}`
        : Math.abs(v) >= 1000
          ? `${(v / 1000).toFixed(1)}K`
          : v.toFixed(Math.abs(v) < 10 ? 2 : 0);
    return { bin: fmt(lo), range: `${fmt(lo)} – ${fmt(hi)}`, count: n };
  });

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={MARGIN} barCategoryGap="12%">
        <CartesianGrid {...GRID} />
        <XAxis
          dataKey="bin"
          stroke={AXIS.stroke}
          tick={AXIS.tick}
          interval="preserveStartEnd"
          minTickGap={16}
        />
        <YAxis stroke={AXIS.stroke} tick={AXIS.tick} width={48} allowDecimals={false} />
        <Tooltip
          content={
            <ChartTooltip
              formatter={(v) => `${v} pool${Number(v) === 1 ? "" : "s"}`}
              labelFormatter={(label) =>
                data.find((d) => d.bin === label)?.range ?? String(label)
              }
            />
          }
          cursor={{ fill: "var(--color-raised)", fillOpacity: 0.5 }}
        />
        <Bar
          dataKey="count"
          name="Pools"
          fill={seriesColor(0)}
          maxBarSize={24}
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

/* -------------------------------------------------------------------------- */
/* Survival curve                                                             */
/* -------------------------------------------------------------------------- */

export interface SurvivalCurveInput {
  label: string;
  times: number[];
  survival: number[];
  ci_lower: number[];
  ci_upper: number[];
}

/**
 * Kaplan-Meier step function.
 *
 * `type="stepAfter"` matters statistically: survival is constant between event
 * times and drops at them. A smoothed line would imply a continuous decline
 * that the estimator does not claim.
 *
 * The confidence band is drawn only for a single curve: overlapping translucent
 * bands across several groups are unreadable, so stratified views show the step
 * lines alone and the band is available by viewing one group at a time.
 */
export function SurvivalChart({
  curves,
  height = 320,
  showBand = true,
  emptyMessage = "No survival curve to plot.",
}: {
  curves: SurvivalCurveInput[];
  height?: number;
  showBand?: boolean;
  emptyMessage?: string;
}) {
  if (!curves.length || !curves[0]?.times.length) {
    return <ChartEmpty message={emptyMessage} height={height} />;
  }

  // Merge every curve's time grid so one dataset drives the chart.
  const allTimes = Array.from(
    new Set(curves.flatMap((c) => c.times)),
  ).sort((a, b) => a - b);

  const single = curves.length === 1 && showBand;

  const data = allTimes.map((t) => {
    const row: Record<string, number | null> = { t };
    curves.forEach((curve, index) => {
      // Step function: carry the last value at or before t.
      let value: number | null = null;
      let lo: number | null = null;
      let hi: number | null = null;
      for (let i = 0; i < curve.times.length; i += 1) {
        const ct = curve.times[i];
        if (ct !== undefined && ct <= t) {
          value = curve.survival[i] ?? value;
          lo = curve.ci_lower[i] ?? lo;
          hi = curve.ci_upper[i] ?? hi;
        } else break;
      }
      row[`s${index}`] = value;
      if (single) {
        row.ciLower = lo;
        // Recharts stacks an area pair, so the band is drawn as lower + span.
        row.ciSpan = hi !== null && lo !== null ? Math.max(0, hi - lo) : null;
      }
    });
    return row;
  });

  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={MARGIN}>
          <CartesianGrid {...GRID} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            stroke={AXIS.stroke}
            tick={AXIS.tick}
            tickFormatter={(v) => `${Number(v).toFixed(0)}h`}
          />
          <YAxis
            domain={[0, 1]}
            stroke={AXIS.stroke}
            tick={AXIS.tick}
            tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
            width={46}
          />
          <Tooltip
            content={
              <ChartTooltip
                labelFormatter={(label) => `${Number(label).toFixed(1)} hours after launch`}
                formatter={(value, name) =>
                  name.startsWith("95%")
                    ? "—"
                    : value === null || value === undefined
                      ? "—"
                      : `${(Number(value) * 100).toFixed(1)}% still active`
                }
              />
            }
            cursor={{ stroke: "var(--color-border-strong)", strokeWidth: 1 }}
          />

          {single ? (
            <>
              {/* Invisible lower bound, then the visible span above it. */}
              <Area
                dataKey="ciLower"
                stackId="ci"
                stroke="none"
                fill="transparent"
                isAnimationActive={false}
                name="95% CI lower"
                legendType="none"
              />
              <Area
                dataKey="ciSpan"
                stackId="ci"
                stroke="none"
                fill={seriesColor(0)}
                fillOpacity={0.1}
                isAnimationActive={false}
                name="95% CI"
                legendType="none"
              />
            </>
          ) : null}

          {curves.map((curve, index) => (
            <Area
              key={curve.label}
              type="stepAfter"
              dataKey={`s${index}`}
              name={curve.label}
              stroke={seriesColor(index)}
              strokeWidth={2}
              fill="none"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--color-panel)" }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
          <ReferenceLine y={0.5} stroke="var(--color-border-strong)" strokeWidth={1} />
        </AreaChart>
      </ResponsiveContainer>
      <Legend
        items={curves.map((curve, index) => ({
          label: curve.label,
          color: seriesColor(index),
        }))}
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Correlation heatmap                                                        */
/* -------------------------------------------------------------------------- */

/**
 * Diverging blue <-> red with a neutral grey midpoint.
 *
 * Correlation has polarity, so a diverging scale is the right encoding and a
 * sequential one would hide the sign. The midpoint is grey, never a hue, so
 * "no relationship" reads as nothing. Each cell prints its coefficient, so the
 * value never depends on colour alone.
 */
export function CorrelationHeatmap({
  columns,
  matrix,
  pairwiseN,
}: {
  columns: string[];
  matrix: Array<Array<number | null>>;
  pairwiseN?: number[][];
}) {
  if (!columns.length) {
    return <ChartEmpty message="No variables with enough data to correlate." height={200} />;
  }

  const cellColor = (value: number | null): string => {
    if (value === null) return "var(--color-panel)";
    const magnitude = Math.min(1, Math.abs(value));
    // Opacity carries magnitude; hue carries sign.
    const pole = value >= 0 ? "var(--color-div-pos)" : "var(--color-div-neg)";
    return `color-mix(in oklab, ${pole} ${(magnitude * 85 + 8).toFixed(0)}%, var(--color-div-mid))`;
  };

  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-[11px]">
        <caption className="sr-only">
          Correlation matrix. Each cell is the correlation coefficient between
          the row and column variables.
        </caption>
        <thead>
          <tr>
            <th className="sticky left-0 z-10 bg-surface p-1" />
            {columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="max-w-[90px] p-1 align-bottom text-[10px] font-medium leading-tight text-ink-muted"
              >
                <span className="block break-words">
                  {humanize(column)}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {columns.map((rowName, i) => (
            <tr key={rowName}>
              <th
                scope="row"
                className="sticky left-0 z-10 whitespace-nowrap bg-surface py-1 pr-2 text-right text-[10px] font-medium text-ink-muted"
              >
                {humanize(rowName)}
              </th>
              {columns.map((colName, j) => {
                const value = matrix[i]?.[j] ?? null;
                const n = pairwiseN?.[i]?.[j];
                const isDiagonal = i === j;
                return (
                  <td key={colName} className="p-[1px]">
                    <div
                      title={
                        value === null
                          ? `${rowName} vs ${colName}: not computed (too few paired observations)`
                          : `${rowName} vs ${colName}: ${value.toFixed(3)}${n ? ` (n=${n})` : ""}`
                      }
                      className="flex h-9 w-[76px] items-center justify-center rounded-sm"
                      style={{
                        background: isDiagonal ? "var(--color-raised)" : cellColor(value),
                      }}
                    >
                      <span
                        className={`tnum text-[10px] font-medium ${
                          isDiagonal ? "text-ink-muted" : "text-ink"
                        }`}
                      >
                        {value === null ? "—" : value.toFixed(2)}
                      </span>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-3 flex items-center gap-3 text-[10px] text-ink-muted">
        <span>−1</span>
        <span
          aria-hidden="true"
          className="h-2 w-40 rounded-sm"
          style={{
            background:
              "linear-gradient(to right, var(--color-div-neg), var(--color-div-mid), var(--color-div-pos))",
          }}
        />
        <span>+1</span>
        <span className="ml-2">
          Red = negative, grey = none, blue = positive. Coefficients are printed in
          every cell, so colour is never the only channel.
        </span>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Scatter                                                                    */
/* -------------------------------------------------------------------------- */

export function ScatterPlot({
  points,
  xLabel,
  yLabel,
  xFormat = "raw",
  yFormat = "raw",
  height = 320,
  logX = false,
  emptyMessage = "No paired observations to plot.",
}: {
  points: Array<{ x: number; y: number; label?: string }>;
  xLabel: string;
  yLabel: string;
  xFormat?: ValueFormat;
  yFormat?: ValueFormat;
  height?: number;
  logX?: boolean;
  emptyMessage?: string;
}) {
  if (!points.length) return <ChartEmpty message={emptyMessage} height={height} />;
  const xFormatter = resolveFormatter(xFormat);
  const yFormatter = resolveFormatter(yFormat);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ ...MARGIN, bottom: 12 }}>
        <CartesianGrid {...GRID} vertical />
        <XAxis
          type="number"
          dataKey="x"
          name={xLabel}
          stroke={AXIS.stroke}
          tick={AXIS.tick}
          scale={logX ? "log" : "linear"}
          domain={logX ? ["auto", "auto"] : ["dataMin", "dataMax"]}
          allowDataOverflow={false}
          tickFormatter={(v) => xFormatter(v)}
        />
        <YAxis
          type="number"
          dataKey="y"
          name={yLabel}
          stroke={AXIS.stroke}
          tick={AXIS.tick}
          width={66}
          tickFormatter={(v) => yFormatter(v)}
        />
        <ZAxis range={[36, 36]} />
        <Tooltip
          content={
            <ChartTooltip
              formatter={(value, name) =>
                name === xLabel ? xFormatter(value) : yFormatter(value)
              }
            />
          }
          cursor={{ strokeDasharray: "0", stroke: "var(--color-border-strong)" }}
        />
        <Scatter
          data={points}
          name="Pools"
          fill={seriesColor(0)}
          fillOpacity={0.75}
          // 2px surface ring keeps overlapping dots legible.
          stroke="var(--color-panel)"
          strokeWidth={2}
        />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
