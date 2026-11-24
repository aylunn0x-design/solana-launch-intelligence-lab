/**
 * Data Status — the data health report.
 *
 * Each check is a real query. A capability that cannot be exercised reports
 * `not_run` rather than `pass`, because a check that never ran has not passed.
 */

import { api, type DataQuality, type Health, type JobList, type RateLimits } from "@/lib/api";
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
import { DataTable } from "@/components/charts/chrome";
import { count, dateTime, decimal, percent, relativeTime } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Data Status" };

export default async function DataStatusPage() {
  let quality: DataQuality;
  let health: Health | null = null;
  let jobs: JobList | null = null;
  let limits: RateLimits | null = null;
  try {
    [quality, health, jobs, limits] = await Promise.all([
      api.dataQuality(),
      api.health().catch(() => null),
      api.jobs({ limit: 25 }).catch(() => null),
      api.rateLimits().catch(() => null),
    ]);
  } catch (error) {
    return (
      <>
        <PageHeader title="Data Status" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  if (quality.dataset_empty) {
    return (
      <>
        <PageHeader title="Data Status" />
        <Panel>
          <EmptyState title="No data to check" description={quality.message} />
        </Panel>
      </>
    );
  }

  const failing = quality.checks.filter((check) => check.status === "fail");

  return (
    <>
      <PageHeader
        title="Data Status"
        description="Ingestion health, completeness, duplicates, invalid rows and source availability. Every figure is a live query."
        actions={<StatusPill status={quality.overall_status} />}
      />

      {failing.length ? (
        <div className="mb-5 space-y-2">
          {failing.map((check) => (
            <Callout key={check.name} severity="critical" title={check.name.replace(/_/g, " ")}>
              {check.message}
            </Callout>
          ))}
        </div>
      ) : null}

      <div className="mb-5">
        <Panel title="Database">
          <MetricGrid>
            <Metric label="Pools" value={count(quality.counts.pools)} />
            <Metric label="Observations" value={count(quality.counts.observations)} />
            <Metric label="Candles" value={count(quality.counts.candles)} />
            <Metric
              label="Engine"
              value={quality.database.engine}
              sub={
                quality.database.foreign_keys_enforced
                  ? "foreign keys enforced"
                  : "foreign keys NOT enforced"
              }
            />
          </MetricGrid>
          {health ? (
            <SectionNote>
              Backend environment: {health.environment} · database reachable:{" "}
              {health.database.reachable ? "yes" : "no"}
              {health.database.error ? ` · ${health.database.error}` : ""}
            </SectionNote>
          ) : null}
        </Panel>
      </div>

      <div className="mb-5">
        <Panel title="Quality checks">
          <ul className="space-y-3">
            {quality.checks.map((check) => (
              <li key={check.name} className="border-b border-border/60 pb-3 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={check.status} />
                  <span className="text-[12px] font-medium text-ink">
                    {check.name.replace(/_/g, " ")}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-ink-secondary">{check.message}</p>
                {check.name === "observation_field_completeness" &&
                Array.isArray(check.detail) ? (
                  <div className="mt-2">
                    <DataTable
                      columns={["Field", "Non-null", "Total", "Completeness"]}
                      rows={(
                        check.detail as Array<{
                          field: string;
                          non_null: number;
                          total: number;
                          completeness: number | null;
                        }>
                      ).map((row) => [
                        row.field,
                        count(row.non_null),
                        count(row.total),
                        percent(row.completeness),
                      ])}
                    />
                  </div>
                ) : check.detail && typeof check.detail === "object" ? (
                  <dl className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1">
                    {Object.entries(check.detail as Record<string, unknown>).map(
                      ([key, value]) => (
                        <div key={key} className="flex items-baseline gap-1.5">
                          <dt className="text-[10px] uppercase tracking-wider text-ink-muted">
                            {key.replace(/_/g, " ")}
                          </dt>
                          <dd className="tnum text-[11px] text-ink-secondary">
                            {typeof value === "number"
                              ? count(value)
                              : String(value)}
                          </dd>
                        </div>
                      ),
                    )}
                  </dl>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Ingestion jobs">
          <MetricGrid>
            <Metric
              label="Last success"
              value={
                quality.jobs.last_successful_at
                  ? relativeTime(quality.jobs.last_successful_at)
                  : "never"
              }
              sub={dateTime(quality.jobs.last_successful_at)}
            />
            <Metric
              label="Rate-limit hits"
              value={count(quality.jobs.total_rate_limit_hits)}
              hint="Total HTTP 429 responses absorbed by the client's backoff across all jobs. Expected to be non-zero: the free tiers throttle aggressively."
            />
          </MetricGrid>
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(quality.jobs.by_status).map(([status, n]) => (
              <Badge
                key={status}
                tone={
                  status === "succeeded"
                    ? "good"
                    : status === "failed"
                      ? "critical"
                      : status === "partial"
                        ? "warning"
                        : "neutral"
                }
              >
                {status}: {n}
              </Badge>
            ))}
          </div>
          {quality.jobs.recent_failures.length ? (
            <div className="mt-4">
              <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-status-serious">
                Recent failures and partial runs
              </h3>
              <DataTable
                columns={["ID", "Type", "Status", "Started", "Error"]}
                rows={quality.jobs.recent_failures.map((job) => [
                  job.id,
                  job.job_type,
                  job.status,
                  dateTime(job.started_at),
                  job.error_message ?? "—",
                ])}
              />
            </div>
          ) : (
            <SectionNote>No failed or partial jobs recorded.</SectionNote>
          )}
        </Panel>

        <Panel title="Data sources">
          <DataTable
            columns={["Source", "Enabled", "API key", "Observations"]}
            rows={quality.sources.map((source) => [
              source.name,
              source.enabled ? "yes" : "no",
              source.requires_api_key ? "required" : "not required",
              count(source.observations_contributed),
            ])}
          />
          <div className="mt-3 space-y-2">
            {quality.sources.map((source) => (
              <p key={source.name} className="text-[11px] leading-relaxed text-ink-muted">
                <span className="font-medium text-ink-secondary">{source.name}:</span>{" "}
                {source.description}
              </p>
            ))}
          </div>
        </Panel>
      </div>

      {limits ? (
        <div className="mb-5">
          <Panel
            title="Rate limits"
            description="What the client is configured to do, and what is actually known about the providers' limits."
          >
            <dl>
              {Object.entries(limits.configured_requests_per_minute).map(([name, value]) => (
                <KeyValue key={name} label={`${name} (configured)`}>
                  <span className="tnum">{decimal(value, 0)} requests / minute</span>
                </KeyValue>
              ))}
              <KeyValue label="Documented provider limits">
                <Badge tone="warning">{limits.documented_limits}</Badge>
              </KeyValue>
            </dl>
            <SectionNote>{limits.explanation}</SectionNote>
            <SectionNote>
              <span className="font-medium text-ink-secondary">On HTTP 429:</span>{" "}
              {limits.behaviour_on_429}
            </SectionNote>
          </Panel>
        </div>
      ) : null}

      {jobs?.items.length ? (
        <Panel title="Job history" description="Most recent runs, newest first.">
          <DataTable
            columns={[
              "ID",
              "Type",
              "Status",
              "Written",
              "Skipped",
              "HTTP",
              "429s",
              "Duration",
              "Started",
            ]}
            rows={jobs.items.map((job) => [
              job.id,
              job.job_type,
              job.status,
              count(job.records_written),
              count(job.records_skipped),
              count(job.http_requests_made),
              count(job.rate_limit_hits),
              job.duration_seconds !== null ? `${decimal(job.duration_seconds, 1)}s` : "—",
              dateTime(job.started_at),
            ])}
          />
        </Panel>
      ) : null}
    </>
  );
}
