/**
 * Research Lab — exploratory data analysis.
 *
 * Variable selection is in the URL, so any distribution view can be linked to.
 * The dataset's cohort warnings are shown at the top of the page and repeated on
 * individual results: on this platform the caveats travel with the number.
 */

import Link from "next/link";
import { Suspense } from "react";

import { api, type Describe, type Histogram, type ResearchSummary } from "@/lib/api";
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
  Skeleton,
  WarningList,
} from "@/components/ui/primitives";
import {
  ControlBar,
  ParamSelect,
  ParamToggle,
  VariableSelect,
} from "@/components/research/controls";
import { StatResultPanel } from "@/components/research/stat-result";
import { HistogramChart } from "@/components/charts";
import { ChartFrame, DataTable } from "@/components/charts/chrome";
import { count, decimal, duration, humanize, percent, usd } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Research Lab" };

interface SearchParams {
  var?: string;
  log?: string;
  bins?: string;
  group_by?: string;
  compare?: string;
}

export default async function ResearchPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;

  let summary: ResearchSummary;
  try {
    summary = await api.researchSummary();
  } catch (error) {
    return (
      <>
        <PageHeader title="Research Lab" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  const variables = summary.variables;
  const selected =
    sp.var && variables.some((v) => v.name === sp.var)
      ? sp.var
      : (variables.find((v) => v.name === "initial_liquidity_usd")?.name ??
        variables[0]?.name);

  const logScale = sp.log === "true";
  const bins = Math.min(60, Math.max(8, Number.parseInt(sp.bins ?? "24", 10) || 24));

  let describe: Describe | null = null;
  let histogram: Histogram | null = null;
  let compareResult = null;

  if (selected) {
    const [d, h] = await Promise.all([
      api.describe(selected).catch(() => null),
      api.histogram(selected, { bins, log_scale: logScale }).catch(() => null),
    ]);
    describe = d;
    histogram = h;
  }

  // Optional cohort comparison: a numeric variable split by a grouping column.
  const groupBy = sp.group_by;
  if (selected && groupBy) {
    compareResult = await api
      .compareGroups({ value_column: selected, group_column: groupBy })
      .catch(() => null);
  }

  if (variables.length === 0) {
    return (
      <>
        <PageHeader title="Research Lab" />
        <Panel>
          <EmptyState
            title="No analysable variables in this dataset"
            description="The dataset version contains no pool with at least two non-null values for any feature. Ingest more data and rebuild the dataset."
          />
        </Panel>
      </>
    );
  }

  const variableOptions = variables.map((variable) => ({
    name: variable.name,
    label: humanize(variable.name),
    availability: variable.availability,
    nonNull: variable.non_null_count,
  }));

  // Formatting for the metric text below. This stays on the server: it is only
  // used to build strings, never passed to a client component.
  const fmt = (value: number | null | undefined): string => {
    if (!selected) return String(value ?? "—");
    if (selected.endsWith("_usd")) return usd(value);
    if (selected.endsWith("_hours")) return duration(value);
    if (selected.endsWith("_pct")) return percent(value, { alreadyPercent: true });
    return decimal(value, 3);
  };

  return (
    <>
      <PageHeader
        title="Research Lab"
        description="Exploratory analysis over an immutable dataset version. Distributions, descriptive statistics and cohort comparisons."
        actions={
          <Badge tone="neutral" title="The dataset version these results are computed from">
            {summary.dataset.label} · n={summary.dataset.n_pools}
          </Badge>
        }
      />

      <div className="mb-5">
        <WarningList
          warnings={summary.cohort_warnings}
          title="These caveats apply to every result on this page"
        />
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-3">
        <Panel title="Dataset" className="lg:col-span-1">
          <dl>
            <KeyValue label="Version label">{summary.dataset.label}</KeyValue>
            <KeyValue label="Pools included">{count(summary.dataset.n_pools)}</KeyValue>
            <KeyValue
              label="Inactivity threshold"
              hint="Hours of zero volume after which a pool counts as inactive for survival analysis."
            >
              {summary.dataset.inactivity_threshold_hours}h
            </KeyValue>
            <KeyValue label="Analysable variables">{count(variables.length)}</KeyValue>
          </dl>
          {summary.dataset.criteria &&
          typeof summary.dataset.criteria.notes === "string" ? (
            <SectionNote>{summary.dataset.criteria.notes}</SectionNote>
          ) : null}
        </Panel>

        <Panel
          title="Leakage boundary"
          description="Which variables may be used as model inputs."
          className="lg:col-span-2"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-status-good">
                Legal model inputs (known at launch)
              </h3>
              <ul className="space-y-0.5 text-xs text-ink-secondary">
                {summary.feature_availability.legal_model_inputs.map((name) => (
                  <li key={name}>{humanize(name)}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-status-serious">
                Outcomes (never inputs)
              </h3>
              <ul className="space-y-0.5 text-xs text-ink-secondary">
                {summary.feature_availability.outcomes.map((name) => (
                  <li key={name}>{humanize(name)}</li>
                ))}
              </ul>
            </div>
          </div>
          <SectionNote>{summary.feature_availability.explanation}</SectionNote>
        </Panel>
      </div>

      <Suspense fallback={<Skeleton className="mb-5 h-16 w-full" />}>
        <ControlBar>
          <VariableSelect
            paramKey="var"
            label="Variable"
            variables={variableOptions}
            defaultValue={selected}
          />
          <ParamSelect
            paramKey="bins"
            label="Bins"
            defaultValue="24"
            options={[12, 16, 24, 32, 48].map((n) => ({
              value: String(n),
              label: String(n),
            }))}
          />
          <ParamSelect
            paramKey="group_by"
            label="Compare groups by"
            options={[
              { value: "", label: "(no comparison)" },
              { value: "dex", label: "Venue (DEX)" },
              { value: "discovery_method", label: "Discovery method" },
              { value: "event_observed", label: "Became inactive" },
              { value: "survived_24h", label: "Survived 24h" },
            ]}
          />
          <ParamToggle
            paramKey="log"
            label="Log scale"
            hint="Bin log10(value). Liquidity and volume span several orders of magnitude, so a linear histogram collapses almost everything into the first bin."
          />
        </ControlBar>
      </Suspense>

      {selected && describe ? (
        <div className="mb-5 grid gap-5 lg:grid-cols-5">
          <Panel
            title={`Distribution — ${humanize(selected)}`}
            className="lg:col-span-3"
          >
            {histogram && histogram.available && histogram.bin_edges && histogram.counts ? (
              <ChartFrame
                title={`${humanize(selected)} across ${count(histogram.n)} pools`}
                subtitle={
                  logScale
                    ? "Binned on a log10 scale; axis labels are powers of ten."
                    : "Linear bins."
                }
                yLabel="Pools"
                xLabel={logScale ? `log10(${humanize(selected)})` : humanize(selected)}
                height={260}
                tableView={
                  <DataTable
                    columns={["Bin start", "Bin end", "Pools"]}
                    rows={histogram.counts.map((n, i) => [
                      decimal(histogram.bin_edges?.[i] ?? null, 3),
                      decimal(histogram.bin_edges?.[i + 1] ?? null, 3),
                      count(n),
                    ])}
                  />
                }
                footnote={
                  histogram.excluded_nonpositive
                    ? `${histogram.excluded_nonpositive} non-positive value(s) excluded for the log scale.`
                    : undefined
                }
              >
                <HistogramChart
                  binEdges={histogram.bin_edges}
                  counts={histogram.counts}
                  logScale={logScale}
                  height={260}
                />
              </ChartFrame>
            ) : (
              <EmptyState
                title="Cannot plot this distribution"
                description={
                  histogram?.message ??
                  "Not enough non-null values for this variable in the current dataset."
                }
              />
            )}
          </Panel>

          <Panel title="Descriptive statistics" className="lg:col-span-2">
            {describe.available ? (
              <>
                <MetricGrid>
                  <Metric
                    label="Median"
                    value={fmt(describe.median)}
                    hint="The middle value. Preferred over the mean for these variables, which are heavily right-skewed."
                  />
                  <Metric label="Mean" value={fmt(describe.mean)} />
                  <Metric label="n" value={count(describe.n)} />
                  <Metric
                    label="Missing"
                    value={percent(describe.missing_share)}
                    hint="Share of pools in the dataset with no value for this variable."
                  />
                </MetricGrid>
                <dl className="mt-4 border-t border-border pt-2">
                  <KeyValue label="Minimum">{fmt(describe.min)}</KeyValue>
                  <KeyValue label="25th percentile">{fmt(describe.q1)}</KeyValue>
                  <KeyValue label="75th percentile">{fmt(describe.q3)}</KeyValue>
                  <KeyValue label="95th percentile">{fmt(describe.p95)}</KeyValue>
                  <KeyValue label="Maximum">{fmt(describe.max)}</KeyValue>
                  <KeyValue
                    label="Skewness"
                    hint="0 is symmetric. Above 2 means a long right tail: a few pools dominate, and the mean is a poor summary."
                  >
                    {decimal(describe.skewness, 2)}
                  </KeyValue>
                  <KeyValue label="Zero values">{count(describe.zero_count ?? null)}</KeyValue>
                </dl>
                {describe.notes?.length ? (
                  <div className="mt-3">
                    <Callout severity="info" title="Reading this distribution">
                      <ul className="space-y-1">
                        {describe.notes.map((note) => (
                          <li key={note}>{note}</li>
                        ))}
                      </ul>
                    </Callout>
                  </div>
                ) : null}
              </>
            ) : (
              <EmptyState title="No data" description={describe.message} />
            )}
          </Panel>
        </div>
      ) : null}

      {compareResult ? (
        <div className="mb-5">
          <StatResultPanel
            result={compareResult}
            title={`Cohort comparison — ${humanize(selected ?? "")} by ${humanize(groupBy ?? "")}`}
            description="Two groups compared with a rank-based test that makes no normality assumption."
          />
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title="Breakdown by venue"
          description="Median outcomes per DEX. Descriptive only."
        >
          {summary.cohort_breakdown_by_dex.groups.length ? (
            <DataTable
              columns={["Venue", "Pools", "Median duration", "Median liquidity", "Event rate"]}
              rows={summary.cohort_breakdown_by_dex.groups.map((group) => [
                group.group,
                count(group.n),
                duration(group.median_duration_hours),
                usd(group.median_initial_liquidity_usd),
                percent(group.event_rate),
              ])}
            />
          ) : (
            <EmptyState title="No venue breakdown available" />
          )}
          <SectionNote>
            “Event rate” is the share of pools observed to go inactive. A low rate
            can mean pools are surviving or simply that they have not been watched
            long enough — check the censoring share on the Survival page.
          </SectionNote>
        </Panel>

        <Panel
          title="Breakdown by discovery method"
          description="The bias-relevant split."
        >
          {summary.cohort_breakdown_by_discovery.groups.length ? (
            <DataTable
              columns={["Discovery", "Pools", "Median duration", "Median liquidity", "Event rate"]}
              rows={summary.cohort_breakdown_by_discovery.groups.map((group) => [
                group.group.replace(/_/g, " "),
                count(group.n),
                duration(group.median_duration_hours),
                usd(group.median_initial_liquidity_usd),
                percent(group.event_rate),
              ])}
            />
          ) : (
            <EmptyState title="No discovery breakdown available" />
          )}
          <SectionNote>
            Differences between these rows are partly an artefact of how the pools
            were found, not only of the pools themselves. See the{" "}
            <Link href="/methodology" className="text-accent hover:opacity-80">
              methodology
            </Link>{" "}
            for why.
          </SectionNote>
        </Panel>
      </div>

      <div className="mt-5 flex flex-wrap gap-3">
        <Link
          href="/research/correlation"
          className="rounded border border-border px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-border-strong hover:text-ink"
        >
          Correlation analysis →
        </Link>
        <Link
          href="/research/survival"
          className="rounded border border-border px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-border-strong hover:text-ink"
        >
          Survival analysis →
        </Link>
      </div>
    </>
  );
}
