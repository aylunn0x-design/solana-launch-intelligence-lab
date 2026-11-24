/**
 * Experiment tracking.
 *
 * An experiment records the question, hypothesis, method, dataset version and
 * limitations alongside its metrics. The dataset version is the important part:
 * without it a recorded metric cannot be reproduced, so the API refuses to
 * create an experiment pointing at a version that does not exist.
 */

import Link from "next/link";

import { api, type DatasetList, type ExperimentList } from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import {
  Badge,
  Callout,
  EmptyState,
  PageHeader,
  Panel,
  SectionNote,
} from "@/components/ui/primitives";
import { DataTable } from "@/components/charts/chrome";
import { count, dateTime, percent, relativeTime } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Experiments" };

export default async function ExperimentsPage() {
  let experiments: ExperimentList;
  let datasets: DatasetList;
  try {
    [experiments, datasets] = await Promise.all([api.experiments(), api.datasets()]);
  } catch (error) {
    return (
      <>
        <PageHeader title="Experiments" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Experiments"
        description="Recorded research runs. Each is tied to an immutable dataset version so its numbers can be regenerated."
        actions={
          <Badge tone="neutral">{count(experiments.pagination.total)} recorded</Badge>
        }
      />

      <div className="mb-5">
        <Panel title="Recorded experiments">
          {experiments.items.length === 0 ? (
            <EmptyState
              title="No experiments recorded yet"
              description={
                <div className="space-y-3 text-left">
                  <p>
                    An experiment is a deliberate record of a research run: the
                    question, the hypothesis, the method, the dataset version, the
                    metrics and the limitations. Nothing is auto-recorded, because an
                    experiment is a claim the researcher chooses to stand behind.
                  </p>
                  <p className="text-ink-muted">Record one via the API:</p>
                  <pre className="overflow-x-auto rounded border border-border bg-panel px-3 py-2.5 text-[11px] leading-relaxed text-ink-secondary">
                    <code>{`curl -X POST http://127.0.0.1:8000/api/experiments \\
  -H 'Content-Type: application/json' \\
  -d '{
    "name": "Initial liquidity vs activity duration",
    "research_question": "Is initial liquidity associated with longer measurable activity?",
    "hypothesis": "Higher initial liquidity is associated with longer activity.",
    "dataset_version_id": 1,
    "method": "spearman",
    "features_used": ["initial_liquidity_usd"],
    "target_definition": "duration_hours",
    "limitations": "Observational; survivorship-biased sample; heavy censoring.",
    "results": [{"metric_name": "rho", "metric_value": 0.33, "sample_size": 44, "p_value": 0.026}]
  }'`}</code>
                  </pre>
                </div>
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-border text-[10px] uppercase tracking-wider text-ink-muted">
                    <th scope="col" className="px-3 py-2.5 font-semibold">Experiment</th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">Method</th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">Dataset</th>
                    <th scope="col" className="px-3 py-2.5 text-right font-semibold">Metrics</th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">Recorded</th>
                  </tr>
                </thead>
                <tbody>
                  {experiments.items.map((experiment) => (
                    <tr
                      key={experiment.id}
                      className="border-b border-border/50 transition-colors hover:bg-raised/40"
                    >
                      <td className="px-3 py-2.5">
                        <Link
                          href={`/experiments/${experiment.id}`}
                          className="font-medium text-ink transition-colors hover:text-accent"
                        >
                          {experiment.name}
                        </Link>
                        <p className="mt-0.5 max-w-xl truncate text-[11px] text-ink-muted">
                          {experiment.research_question}
                        </p>
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge tone="neutral">{experiment.method}</Badge>
                      </td>
                      <td className="tnum px-3 py-2.5 text-ink-secondary">
                        {experiment.dataset_version_id ?? "—"}
                      </td>
                      <td className="tnum px-3 py-2.5 text-right text-ink-secondary">
                        {count(experiment.result_count)}
                      </td>
                      <td
                        className="px-3 py-2.5 text-ink-muted"
                        title={dateTime(experiment.created_at)}
                      >
                        {relativeTime(experiment.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title="Dataset versions"
        description="The immutable snapshots experiments are computed against."
      >
        {datasets.items.length === 0 ? (
          <EmptyState
            title="No dataset versions"
            description="Build one before recording experiments, so results stay reproducible."
          />
        ) : (
          <DataTable
            columns={[
              "ID",
              "Label",
              "Pools",
              "Candles",
              "Threshold",
              "Inception share",
              "Built",
            ]}
            rows={datasets.items.map((version) => [
              version.id,
              version.label,
              count(version.pool_count),
              count(version.candle_count),
              `${version.inactivity_threshold_hours}h`,
              percent(version.inception_cohort_share),
              dateTime(version.built_at),
            ])}
          />
        )}
        <SectionNote>{datasets.note}</SectionNote>
      </Panel>

      <div className="mt-5">
        <Callout severity="info" title="Why versions are immutable">
          Rebuilding a dataset under the same label is refused. If features were
          recomputed in place, every experiment that referenced the label would
          silently change meaning, and the recorded numbers would no longer match
          what the label now contains.
        </Callout>
      </div>
    </>
  );
}
