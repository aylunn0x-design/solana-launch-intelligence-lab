/**
 * Experiment detail.
 *
 * Shows everything needed to re-run the experiment: the question, hypothesis,
 * features, target definition, split strategy, parameters, and the dataset
 * version. Limitations are given the same visual weight as the metrics.
 */

import { notFound } from "next/navigation";

import { ApiError, api, type ExperimentDetail } from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import {
  Badge,
  Callout,
  EmptyState,
  KeyValue,
  PageHeader,
  Panel,
  SectionNote,
} from "@/components/ui/primitives";
import { DataTable } from "@/components/charts/chrome";
import { count, dateTime, decimal, interval, pValue, percent } from "@/lib/format";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return { title: `Experiment ${id}` };
}

export default async function ExperimentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let experiment: ExperimentDetail;
  try {
    experiment = await api.experiment(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <>
        <PageHeader title="Experiment" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Experiments", href: "/experiments" },
          { label: experiment.name },
        ]}
      />
      <PageHeader
        title={experiment.name}
        description={experiment.research_question}
        actions={<Badge tone="neutral">{experiment.method}</Badge>}
      />

      <div className="mb-5 grid gap-5 lg:grid-cols-3">
        <Panel title="Design" className="lg:col-span-2">
          <dl>
            <KeyValue label="Research question">{experiment.research_question}</KeyValue>
            <KeyValue label="Hypothesis">
              {experiment.hypothesis ?? <span className="text-ink-muted">not stated</span>}
            </KeyValue>
            <KeyValue
              label="Target definition"
              hint="Exactly what the outcome variable means. Without this a metric cannot be interpreted."
            >
              {experiment.target_definition ?? <span className="text-ink-muted">not stated</span>}
            </KeyValue>
            <KeyValue label="Features used">
              {experiment.features_used?.length ? (
                <span className="tnum">{experiment.features_used.join(", ")}</span>
              ) : (
                <span className="text-ink-muted">none recorded</span>
              )}
            </KeyValue>
            <KeyValue
              label="Split strategy"
              hint="How train and test were separated. A random split on time-ordered data would leak future information."
            >
              {experiment.split_strategy ?? <span className="text-ink-muted">not stated</span>}
            </KeyValue>
            <KeyValue label="Method">{experiment.method}</KeyValue>
            <KeyValue label="Recorded at">{dateTime(experiment.created_at)}</KeyValue>
          </dl>
          {experiment.parameters ? (
            <div className="mt-3 border-t border-border pt-3">
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                Parameters
              </h3>
              <pre className="overflow-x-auto rounded border border-border bg-panel px-3 py-2 text-[11px] text-ink-secondary">
                <code>{JSON.stringify(experiment.parameters, null, 2)}</code>
              </pre>
            </div>
          ) : null}
        </Panel>

        <Panel title="Dataset version">
          {experiment.dataset_version ? (
            <>
              <dl>
                <KeyValue label="Label">{experiment.dataset_version.label}</KeyValue>
                <KeyValue label="Pools">
                  {count(experiment.dataset_version.pool_count)}
                </KeyValue>
                <KeyValue label="Inactivity threshold">
                  {experiment.dataset_version.inactivity_threshold_hours}h
                </KeyValue>
                <KeyValue
                  label="Inception share"
                  hint="Share of pools from the unbiased new-pools cohort."
                >
                  {percent(experiment.dataset_version.inception_cohort_share)}
                </KeyValue>
              </dl>
              {experiment.dataset_version.criteria &&
              typeof experiment.dataset_version.criteria.notes === "string" ? (
                <SectionNote>{experiment.dataset_version.criteria.notes}</SectionNote>
              ) : null}
            </>
          ) : (
            <EmptyState
              title="No dataset version linked"
              description="This experiment cannot be reproduced exactly: the rows it was computed from are not identified."
            />
          )}
        </Panel>
      </div>

      <div className="mb-5">
        <Panel title="Results">
          {experiment.results.length === 0 ? (
            <EmptyState title="No metrics recorded" />
          ) : (
            <DataTable
              columns={["Metric", "Value", "n", "95% CI", "p", "Split", "Note"]}
              rows={experiment.results.map((result) => [
                result.metric_name,
                result.metric_value !== null
                  ? decimal(result.metric_value, 4)
                  : (result.metric_text ?? "—"),
                count(result.sample_size),
                interval(result.ci_lower, result.ci_upper),
                pValue(result.p_value),
                result.split ?? "—",
                result.metric_text ?? "—",
              ])}
            />
          )}
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Limitations">
          {experiment.limitations ? (
            <Callout severity="high" title="Stated by the researcher">
              {experiment.limitations}
            </Callout>
          ) : (
            <Callout severity="medium" title="No limitations recorded">
              Every observational result has limitations. An experiment recorded
              without them is incomplete.
            </Callout>
          )}
          {experiment.notes ? (
            <div className="mt-3">
              <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                Notes
              </h3>
              <p className="text-xs leading-relaxed text-ink-secondary">{experiment.notes}</p>
            </div>
          ) : null}
        </Panel>

        <Panel title="Reproducibility">
          <dl>
            <KeyValue label="Dataset version id">
              {experiment.reproducibility.dataset_version_id ?? "—"}
            </KeyValue>
          </dl>
          <SectionNote>{experiment.reproducibility.note}</SectionNote>
        </Panel>
      </div>
    </>
  );
}
