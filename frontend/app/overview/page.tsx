/**
 * Overview.
 *
 * Every figure comes from GET /api/overview, which computes it with SQL
 * aggregates over stored rows. There is no client-side fallback: if the API is
 * unreachable the page renders an error state naming the reason, and if the
 * database is empty it renders an empty state explaining how to populate it.
 */

import Link from "next/link";
import { AlertTriangle, Database, Terminal } from "lucide-react";

import { api, type Overview } from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import {
  Badge,
  Callout,
  EmptyState,
  KeyValue,
  Metric,
  MetricGrid,
  PageHeader,
  Panel,
  SectionNote,
  StatusPill,
} from "@/components/ui/primitives";
import { BarSeriesChart } from "@/components/charts";
import { ChartFrame, DataTable } from "@/components/charts/chrome";
import { count, dateTime, percent, relativeTime, usd } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Overview" };

export default async function OverviewPage() {
  let data: Overview;
  try {
    data = await api.overview();
  } catch (error) {
    return (
      <>
        <PageHeader title="Overview" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  if (data.dataset_empty) {
    return (
      <>
        <PageHeader
          title="Overview"
          description="Dataset size, coverage, freshness and data-quality warnings."
        />
        <Panel>
          <EmptyState
            icon={<Database size={22} strokeWidth={1.5} />}
            title="No data has been ingested yet"
            description={
              <div className="space-y-3 text-left">
                <p>{data.message}</p>
                <p className="text-ink-muted">
                  The platform deliberately shows nothing rather than sample
                  numbers. Run the ingestion pipeline to populate it:
                </p>
                <pre className="overflow-x-auto rounded border border-border bg-panel px-3 py-2.5 text-[11px] leading-relaxed text-ink-secondary">
                  <code>{`cd backend
python -m app.cli init-db
python -m app.cli ingest --feed new --pages 10
python -m app.cli backfill --timeframe hour --limit-pools 50
python -m app.cli build-dataset --label v1`}</code>
                </pre>
              </div>
            }
          />
        </Panel>
      </>
    );
  }

  const cohortRows = Object.entries(data.cohort_composition.counts).map(
    ([method, n]) => ({
      method: method.replace(/_/g, " "),
      pools: n,
    }),
  );

  const highWarnings = data.data_quality_warnings.filter(
    (w) => w.severity === "high",
  );

  return (
    <>
      <PageHeader
        title="Overview"
        description="Dataset size, coverage, ingestion freshness and data-quality warnings. Every figure is computed from stored rows."
        actions={
          <Badge tone="neutral" title="When this page was generated">
            {relativeTime(data.generated_at)}
          </Badge>
        }
      />

      {highWarnings.length ? (
        <div className="mb-5 space-y-2">
          {highWarnings.map((warning) => (
            <Callout key={warning.code} severity="high" title={warning.code.replace(/_/g, " ")}>
              {warning.message}
            </Callout>
          ))}
        </div>
      ) : null}

      <div className="mb-5">
        <Panel title="Dataset size">
          <MetricGrid>
            <Metric
              label="Pools (launches)"
              value={count(data.counts.pools)}
              hint="One pool is one launch: the creation of an AMM market for a token. A token can have several pools, each with its own lifecycle."
            />
            <Metric
              label="Distinct tokens"
              value={count(data.counts.tokens)}
              hint="Unique mint addresses observed."
            />
            <Metric
              label="Market observations"
              value={count(data.counts.market_observations)}
              hint="Point-in-time snapshots of a pool's market state. Repeated polling of the same pool builds a longitudinal panel."
            />
            <Metric
              label="OHLCV candles"
              value={count(data.counts.ohlcv_candles)}
              hint="Historical open/high/low/close/volume bars. This is the retrospective price and volume history."
            />
          </MetricGrid>
        </Panel>
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-3">
        <Panel title="Launch date range" className="lg:col-span-1">
          <dl>
            <KeyValue label="Earliest launch">
              {dateTime(data.launch_date_range.earliest)}
            </KeyValue>
            <KeyValue label="Latest launch">
              {dateTime(data.launch_date_range.latest)}
            </KeyValue>
            <KeyValue
              label="Dataset versions"
              hint="Immutable analytical snapshots. An experiment references one so its inputs can be recovered."
            >
              {count(data.counts.dataset_versions)}
            </KeyValue>
            <KeyValue label="Experiments recorded">
              {count(data.counts.experiments)}
            </KeyValue>
          </dl>
        </Panel>

        <Panel
          title="Data coverage"
          description="How much of the dataset is actually usable for analysis."
          className="lg:col-span-2"
        >
          <MetricGrid>
            <Metric
              label="Pools with history"
              value={percent(data.coverage.pools_with_history_share)}
              sub={`${count(data.coverage.pools_with_history)} of ${count(data.counts.pools)}`}
              hint="Share of pools that have at least one OHLCV candle. Pools without history cannot enter survival analysis."
            />
            <Metric
              label="With launch timestamp"
              value={percent(data.coverage.pools_with_launch_timestamp_share)}
              sub={`${count(data.coverage.pools_with_launch_timestamp)} of ${count(data.counts.pools)}`}
              hint="A pool without a creation timestamp cannot be placed on the launch clock and is excluded from cohort analysis."
            />
            <Metric
              label="Unique-trader coverage"
              value={percent(data.coverage.unique_trader_coverage_share)}
              sub={`${count(data.coverage.observations_with_unique_traders)} observations`}
              hint="Share of observations carrying unique participant counts. DexScreener does not report them, so cross-source rows lack this field."
            />
            <Metric
              label="Inception cohort"
              value={percent(data.cohort_composition.inception_cohort_share)}
              sub={`${count(data.cohort_composition.inception_cohort_pools)} pools`}
              hint="Share of pools discovered from the new-pools feed. Only this cohort is free of survivorship bias."
            />
          </MetricGrid>
          <SectionNote>{data.cohort_composition.note}</SectionNote>
        </Panel>
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Cohort composition" description="How pools entered the dataset.">
          <ChartFrame
            title="Pools by discovery method"
            subtitle="Discovery method determines a pool's bias properties."
            yLabel="Pools"
            height={240}
            tableView={
              <DataTable
                columns={["Discovery method", "Pools"]}
                rows={cohortRows.map((row) => [row.method, count(row.pools)])}
              />
            }
            footnote="Only new_pools_feed is an inception cohort: every launch is captured regardless of what happens next. Ranked 'top'/'trending' listings include a pool only because it is currently notable."
          >
            <BarSeriesChart
              data={cohortRows}
              xKey="method"
              series={[{ key: "pools", label: "Pools" }]}
              valueFormat="count"
              height={240}
              colorByIndex
            />
          </ChartFrame>
        </Panel>

        <Panel title="Ingestion status">
          <dl className="mb-4">
            <KeyValue label="Last successful ingestion">
              {data.ingestion.last_successful_at ? (
                <span>
                  {relativeTime(data.ingestion.last_successful_at)}{" "}
                  <span className="text-ink-muted">
                    ({dateTime(data.ingestion.last_successful_at)})
                  </span>
                </span>
              ) : (
                <span className="text-ink-muted">never</span>
              )}
            </KeyValue>
            <KeyValue label="Last job type">
              {data.ingestion.last_successful_job_type ?? "—"}
            </KeyValue>
            <KeyValue label="Total jobs">{count(data.ingestion.total_jobs)}</KeyValue>
            <KeyValue label="Failed jobs">
              {data.ingestion.failed_jobs > 0 ? (
                <span className="inline-flex items-center gap-2">
                  <StatusPill status="failed" />
                  {count(data.ingestion.failed_jobs)}
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <StatusPill status="pass" />0
                </span>
              )}
            </KeyValue>
          </dl>
          <Link
            href="/data-status"
            className="inline-flex items-center gap-1.5 text-xs text-accent transition-opacity hover:opacity-80"
          >
            <Terminal size={12} aria-hidden="true" />
            Full data health report
          </Link>
        </Panel>
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel
          title="Liquidity distribution"
          description="Across all stored observations."
        >
          <MetricGrid>
            <Metric label="Minimum" value={usd(data.distributions.liquidity_usd.min)} />
            <Metric label="Mean" value={usd(data.distributions.liquidity_usd.mean)} />
            <Metric label="Maximum" value={usd(data.distributions.liquidity_usd.max)} />
            <Metric
              label="Observations"
              value={count(data.distributions.liquidity_usd.non_null_observations)}
            />
          </MetricGrid>
          <SectionNote>{data.distributions.liquidity_usd.note}</SectionNote>
          <div className="mt-3">
            <Link
              href="/research"
              className="text-xs text-accent transition-opacity hover:opacity-80"
            >
              Medians, quantiles and distributions in the Research Lab →
            </Link>
          </div>
        </Panel>

        <Panel title="Activity distribution" description="24-hour transaction counts.">
          <MetricGrid>
            <Metric
              label="Mean txns / 24h"
              value={count(
                data.distributions.txns_h24.mean !== null
                  ? Math.round(data.distributions.txns_h24.mean)
                  : null,
              )}
            />
            <Metric label="Max txns / 24h" value={count(data.distributions.txns_h24.max)} />
            <Metric
              label="Observations"
              value={count(data.distributions.txns_h24.non_null_observations)}
            />
          </MetricGrid>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title="Dataset versions"
          description="Immutable snapshots that experiments are computed against."
        >
          {data.dataset_versions.length === 0 ? (
            <EmptyState
              title="No dataset versions built"
              description={
                <>
                  Features have not been computed yet. Build one with{" "}
                  <code className="rounded bg-panel px-1 py-0.5 text-[11px]">
                    python -m app.cli build-dataset --label v1
                  </code>
                  .
                </>
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border text-[10px] uppercase tracking-wider text-ink-muted">
                    <th scope="col" className="py-2 pr-3 font-semibold">Label</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Pools</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Candles</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Threshold</th>
                    <th scope="col" className="py-2 font-semibold">Inception</th>
                  </tr>
                </thead>
                <tbody>
                  {data.dataset_versions.map((version) => (
                    <tr key={version.id} className="border-b border-border/50">
                      <td className="py-2 pr-3 font-medium text-ink">{version.label}</td>
                      <td className="tnum py-2 pr-3 text-ink-secondary">
                        {count(version.pool_count)}
                      </td>
                      <td className="tnum py-2 pr-3 text-ink-secondary">
                        {count(version.candle_count)}
                      </td>
                      <td className="tnum py-2 pr-3 text-ink-secondary">
                        {version.inactivity_threshold_hours}h
                      </td>
                      <td className="tnum py-2 text-ink-secondary">
                        {percent(version.inception_cohort_share)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="All data-quality warnings">
          {data.data_quality_warnings.length === 0 ? (
            <EmptyState title="No warnings" description="All checks are clear." />
          ) : (
            <ul className="space-y-2.5">
              {data.data_quality_warnings.map((warning) => (
                <li key={warning.code} className="flex gap-2.5">
                  <AlertTriangle
                    size={13}
                    aria-hidden="true"
                    className={`mt-0.5 shrink-0 ${
                      warning.severity === "high"
                        ? "text-status-serious"
                        : warning.severity === "medium"
                          ? "text-status-warning"
                          : "text-ink-muted"
                    }`}
                  />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-secondary">
                        {warning.code.replace(/_/g, " ")}
                      </span>
                      <Badge
                        tone={
                          warning.severity === "high"
                            ? "critical"
                            : warning.severity === "medium"
                              ? "warning"
                              : "neutral"
                        }
                      >
                        {warning.severity}
                      </Badge>
                    </div>
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
                      {warning.message}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {data.recent_experiments.length ? (
        <div className="mt-5">
          <Panel title="Recently recorded experiments">
            <ul className="divide-y divide-border/60">
              {data.recent_experiments.map((experiment) => (
                <li key={experiment.id} className="flex items-center justify-between gap-3 py-2">
                  <Link
                    href={`/experiments/${experiment.id}`}
                    className="min-w-0 truncate text-[13px] text-ink transition-colors hover:text-accent"
                  >
                    {experiment.name}
                  </Link>
                  <div className="flex shrink-0 items-center gap-3">
                    <Badge tone="neutral">{experiment.method}</Badge>
                    <span className="text-[11px] text-ink-muted">
                      {relativeTime(experiment.created_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      ) : null}
    </>
  );
}
