/**
 * Survival analysis.
 *
 * Kaplan-Meier, Cox proportional hazards, and the threshold-sensitivity table
 * that answers RQ6. Censoring is displayed prominently: a survival curve
 * computed from a mostly-censored sample is weakly identified past the observed
 * range, and the page says so rather than letting the tail look authoritative.
 */

import { Suspense } from "react";

import {
  api,
  type Cox,
  type Sensitivity,
  type Survival,
} from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
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
  StatusPill,
  WarningList,
} from "@/components/ui/primitives";
import { ControlBar, ParamSelect } from "@/components/research/controls";
import { BarSeriesChart, SurvivalChart } from "@/components/charts";
import { ChartFrame, DataTable } from "@/components/charts/chrome";
import { count, decimal, duration, humanize, pValue, percent } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Survival analysis" };

interface SearchParams {
  group_column?: string;
  covariate?: string;
}

export default async function SurvivalPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const groupColumn = sp.group_column || undefined;
  const covariate = sp.covariate || "initial_liquidity_usd";

  let survival: Survival;
  let cox: Cox | null = null;
  let sensitivity: Sensitivity | null = null;
  try {
    survival = await api.survival(groupColumn ? { group_column: groupColumn } : undefined);
    [cox, sensitivity] = await Promise.all([
      api.cox({ covariates: [covariate] }).catch(() => null),
      api.sensitivity().catch(() => null),
    ]);
  } catch (error) {
    return (
      <>
        <PageHeader title="Survival analysis" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  const sensitivityRows = (sensitivity?.rows ?? []).filter((row) => row.available);
  const thresholdVaries =
    new Set(sensitivityRows.map((row) => row.event_rate)).size > 1;

  return (
    <>
      <Breadcrumbs
        items={[{ label: "Research Lab", href: "/research" }, { label: "Survival" }]}
      />
      <PageHeader
        title="Survival analysis"
        description="Time from launch until measurable trading activity stops. Pools still active when observation ended are right-censored: they contribute information up to that point and nothing after."
        actions={
          survival.dataset_label ? (
            <Badge tone="neutral">{survival.dataset_label}</Badge>
          ) : null
        }
      />

      <Suspense fallback={<Skeleton className="mb-5 h-16 w-full" />}>
        <ControlBar>
          <ParamSelect
            paramKey="group_column"
            label="Stratify curve by"
            options={[
              { value: "", label: "(pooled, with CI band)" },
              { value: "dex", label: "Venue (DEX)" },
              { value: "discovery_method", label: "Discovery method" },
              { value: "launch_dow", label: "Launch day of week" },
            ]}
          />
          <ParamSelect
            paramKey="covariate"
            label="Cox covariate"
            defaultValue="initial_liquidity_usd"
            options={[
              { value: "initial_liquidity_usd", label: "Initial liquidity (USD)" },
              { value: "initial_fdv_usd", label: "Initial FDV (USD)" },
              { value: "first_hour_volume_usd", label: "First-hour volume (USD)" },
              { value: "launch_hour_utc", label: "Launch hour (UTC)" },
            ]}
          />
        </ControlBar>
      </Suspense>

      {survival.available ? (
        <>
          <div className="mb-5">
            <Panel title="Sample and censoring">
              <MetricGrid>
                <Metric
                  label="Pools"
                  value={count(survival.n ?? null)}
                  hint="Pools with a usable duration and event status."
                />
                <Metric
                  label="Events observed"
                  value={count(survival.events_observed ?? null)}
                  hint="Pools seen to go inactive. Only events carry information about when failure happens."
                />
                <Metric
                  label="Right-censored"
                  value={count(survival.censored ?? null)}
                  hint="Still active when observation ended. Not failures — their eventual fate is unknown."
                />
                <Metric
                  label="Censored share"
                  value={percent(survival.censored_share)}
                  tone={
                    (survival.censored_share ?? 0) > 0.7 ? "down" : "neutral"
                  }
                  hint="Above roughly 70% the curve's tail is extrapolation rather than measurement."
                />
              </MetricGrid>
              <SectionNote>
                Event definition: {survival.event_definition} Time is measured in{" "}
                {survival.time_unit}.
              </SectionNote>
            </Panel>
          </div>

          <div className="mb-5">
            <Panel
              title="Kaplan-Meier survival curve"
              description={survival.method}
            >
              <ChartFrame
                title={
                  groupColumn
                    ? `Probability of remaining active, by ${humanize(groupColumn)}`
                    : "Probability of remaining active after launch"
                }
                subtitle={
                  groupColumn
                    ? "Step lines per group. The confidence band is shown only for the pooled curve, because overlapping bands are unreadable."
                    : "Step function with a 95% confidence band. Survival is constant between events and drops at them."
                }
                yLabel="Share still active"
                xLabel="Hours since launch"
                height={340}
                tableView={
                  <DataTable
                    columns={["Group", "n", "Events", "Median survival"]}
                    rows={(survival.curves ?? []).map((curve) => [
                      curve.label,
                      count(curve.n),
                      count(curve.events),
                      curve.median_survival_hours === null
                        ? "not reached"
                        : duration(curve.median_survival_hours),
                    ])}
                  />
                }
                footnote="A step down occurs at each observed inactivity event. Censored pools leave the at-risk set without causing a step."
              >
                <SurvivalChart
                  curves={survival.curves ?? []}
                  showBand={!groupColumn}
                  height={340}
                />
              </ChartFrame>

              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                {(survival.curves ?? []).map((curve) => (
                  <div key={curve.label} className="rounded border border-border bg-panel px-3 py-2.5">
                    <div className="text-[11px] font-semibold text-ink">{curve.label}</div>
                    <div className="tnum mt-1 text-xs text-ink-secondary">
                      n={curve.n} · events={curve.events} · median=
                      {curve.median_survival_hours === null
                        ? "not reached"
                        : duration(curve.median_survival_hours)}
                    </div>
                    {curve.median_note ? (
                      <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                        {curve.median_note}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>

              {survival.logrank ? (
                <div className="mt-4">
                  {survival.logrank.error ? (
                    <Callout severity="medium" title="Log-rank test">
                      {survival.logrank.error}
                    </Callout>
                  ) : (
                    <Callout severity="info" title="Log-rank test between groups">
                      {survival.logrank.test}: statistic{" "}
                      {decimal(survival.logrank.statistic ?? null, 3)}, p ={" "}
                      {pValue(survival.logrank.p_value ?? null)}.{" "}
                      {survival.logrank.interpretation}
                    </Callout>
                  )}
                </div>
              ) : null}

              <div className="mt-4">
                <WarningList warnings={survival.warnings} />
              </div>
            </Panel>
          </div>
        </>
      ) : (
        <div className="mb-5">
          <Panel title="Kaplan-Meier survival curve">
            <EmptyState
              title="Survival curve cannot be estimated"
              description={
                <div className="space-y-2 text-left">
                  <p>{survival.message}</p>
                  <p className="text-ink-muted">
                    This is the correct behaviour rather than a bug: with no observed
                    events there is nothing for the estimator to measure, and drawing
                    a flat line at 100% would imply that no pool ever goes inactive.
                  </p>
                </div>
              }
            />
            <div className="mt-4">
              <WarningList warnings={survival.warnings} />
            </div>
          </Panel>
        </div>
      )}

      <div className="mb-5">
        <Panel
          title="Cox proportional hazards"
          description="Effect of a launch-time covariate on the hazard of going inactive. Only features known at launch are permitted here."
        >
          {cox?.available ? (
            <>
              <MetricGrid>
                <Metric label="Pools" value={count(cox.n ?? null)} />
                <Metric label="Events" value={count(cox.events_observed ?? null)} />
                <Metric
                  label="Events per covariate"
                  value={decimal(cox.events_per_covariate, 1)}
                  hint="Rule of thumb: at least 10. Below that the coefficients are unstable."
                />
                <Metric
                  label="Concordance"
                  value={decimal(cox.concordance_index, 3)}
                  hint="Share of comparable pool pairs ranked in the right order. 0.5 is coin-flip. A goodness-of-fit measure, not out-of-sample accuracy."
                />
              </MetricGrid>

              <div className="mt-4">
                <DataTable
                  columns={["Covariate", "Hazard ratio", "95% CI", "p", "Reading"]}
                  rows={(cox.coefficients ?? []).map((coefficient) => [
                    humanize(coefficient.covariate),
                    decimal(coefficient.hazard_ratio, 3),
                    `[${decimal(coefficient.ci_lower_hr, 3)}, ${decimal(coefficient.ci_upper_hr, 3)}]`,
                    pValue(coefficient.p_value),
                    coefficient.interpretation,
                  ])}
                />
              </div>

              {cox.log_transformed?.length ? (
                <SectionNote>
                  Log-transformed before fitting (strong right skew):{" "}
                  {cox.log_transformed.map(humanize).join(", ")}. Hazard ratios are
                  therefore per unit of log1p(value).
                </SectionNote>
              ) : null}

              {cox.proportional_hazards_test ? (
                <div className="mt-4">
                  <Callout
                    severity={
                      cox.proportional_hazards_test.passed === true ? "info" : "medium"
                    }
                    title="Proportional-hazards assumption"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusPill
                        status={
                          cox.proportional_hazards_test.passed === true ? "pass" : "warn"
                        }
                      />
                      <span>
                        {Array.isArray(cox.proportional_hazards_test.note)
                          ? cox.proportional_hazards_test.note.join(" ")
                          : String(
                              cox.proportional_hazards_test.note ??
                                cox.proportional_hazards_test.error ??
                                "",
                            )}
                      </span>
                    </div>
                  </Callout>
                </div>
              ) : null}

              {cox.concordance_note ? <SectionNote>{cox.concordance_note}</SectionNote> : null}

              {cox.warnings?.length ? (
                <div className="mt-4">
                  <WarningList warnings={cox.warnings} />
                </div>
              ) : null}
            </>
          ) : (
            <EmptyState
              title="Cox model not fitted"
              description={
                <div className="space-y-2 text-left">
                  <p>{cox?.message ?? "The model could not be estimated."}</p>
                  {cox?.error === "leakage_blocked" ? (
                    <p className="text-ink-muted">
                      The requested covariate is not known at launch, so using it to
                      predict lifetime would leak outcome information. Legal inputs:{" "}
                      {(cox.legal_model_inputs ?? []).map(humanize).join(", ")}.
                    </p>
                  ) : null}
                </div>
              }
            />
          )}
        </Panel>
      </div>

      <Panel
        title="Threshold sensitivity (RQ6)"
        description="How much the measured outcome depends on the definition of inactivity."
      >
        {sensitivityRows.length > 1 ? (
          <>
            <ChartFrame
              title="Event rate by inactivity threshold"
              subtitle="Each bar is the same pools scored under a different definition of 'inactive'."
              yLabel="Share of pools with an observed event"
              xLabel="Inactivity threshold (hours)"
              height={240}
              tableView={
                <DataTable
                  columns={["Threshold (h)", "Pools", "Events", "Event rate", "Median duration"]}
                  rows={sensitivityRows.map((row) => [
                    row.threshold_hours,
                    count(row.n),
                    count(row.events_observed ?? null),
                    percent(row.event_rate),
                    duration(row.median_duration_observed),
                  ])}
                />
              }
            >
              <BarSeriesChart
                data={sensitivityRows.map((row) => ({
                  threshold: `${row.threshold_hours}h`,
                  rate: row.event_rate ?? null,
                }))}
                xKey="threshold"
                series={[{ key: "rate", label: "Event rate" }]}
                valueFormat="percent"
                height={240}
              />
            </ChartFrame>

            <div className="mt-4">
              <Callout
                severity={thresholdVaries ? "high" : "info"}
                title={
                  thresholdVaries
                    ? "The measured outcome moves with the definition"
                    : "Stable across thresholds"
                }
              >
                {thresholdVaries ? (
                  <>
                    The share of pools counted as “dead” changes with the inactivity
                    threshold alone, on the same underlying pools. Any statement about
                    how often launches fail is therefore a statement about the chosen
                    threshold as much as about the market. Quote the threshold with
                    the result, always.
                  </>
                ) : (
                  <>
                    The event rate is unchanged across the compared thresholds, so the
                    conclusion is not an artefact of this particular choice.
                  </>
                )}
              </Callout>
            </div>

            {sensitivity?.versions_compared?.length ? (
              <div className="mt-4">
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                  Dataset versions compared
                </h3>
                <dl>
                  {sensitivity.versions_compared.map((version) => (
                    <KeyValue key={version.id} label={version.label}>
                      threshold {version.inactivity_threshold_hours}h ·{" "}
                      {count(version.pool_count)} pools
                    </KeyValue>
                  ))}
                </dl>
              </div>
            ) : null}
          </>
        ) : (
          <EmptyState
            title="Not enough dataset versions to compare"
            description={
              <div className="space-y-2 text-left">
                <p>
                  {sensitivity?.message ??
                    "A sensitivity analysis needs dataset versions built at different inactivity thresholds."}
                </p>
                <pre className="overflow-x-auto rounded border border-border bg-panel px-3 py-2 text-[11px] text-ink-secondary">
                  <code>{`python -m app.cli build-dataset --label t12 --inactivity-hours 12
python -m app.cli build-dataset --label t48 --inactivity-hours 48`}</code>
                </pre>
              </div>
            }
          />
        )}
        {sensitivity?.warnings?.length ? (
          <div className="mt-4">
            <WarningList warnings={sensitivity.warnings} />
          </div>
        ) : null}
      </Panel>
    </>
  );
}
