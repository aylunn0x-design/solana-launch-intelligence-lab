/**
 * Renders a statistical result in full.
 *
 * The contract: a statistic is never shown alone. Sample size, missing-data
 * share, method, effect size, confidence interval and p-value appear together,
 * because any one of them without the others invites a wrong reading.
 */

import type { StatResult } from "@/lib/api";
import { Metric, MetricGrid, Panel, SectionNote, WarningList } from "@/components/ui/primitives";
import { decimal, interval, pValue, percent } from "@/lib/format";

export function StatResultPanel({
  result,
  title,
  description,
}: {
  result: StatResult;
  title: string;
  description?: string;
}) {
  const notComputed = result.statistic === null;
  const interpretation =
    typeof result.details.interpretation === "string" ? result.details.interpretation : null;

  return (
    <Panel title={title} description={description}>
      {notComputed ? (
        <p className="mb-4 text-[13px] text-ink-secondary">
          This statistic was not computed. The reasons are listed below — the
          platform declines to report a number it cannot stand behind.
        </p>
      ) : (
        <MetricGrid>
          <Metric
            label={result.statistic_name}
            value={decimal(result.statistic, 4)}
            sub={interpretation ?? undefined}
            hint="The test statistic. For a correlation this is itself the effect size."
          />
          <Metric
            label="Sample size (n)"
            value={result.n.toLocaleString("en-US")}
            hint="Rows with both variables present. Pairwise-complete, not the whole dataset."
          />
          <Metric
            label="Missing"
            value={percent(result.missing_share)}
            hint="Share of the selected rows dropped because a value was absent. A high figure means this is a complete-case analysis on a subsample."
          />
          <Metric
            label="p-value"
            value={pValue(result.p_value)}
            hint="Probability of a statistic at least this extreme if there were no association. It is not the probability that the finding is real, and it says nothing about effect size."
          />
          <Metric
            label="95% CI"
            value={interval(result.ci_lower, result.ci_upper)}
            hint="Range of values compatible with the data. Prefer it over the point estimate, especially at small n."
          />
          {result.effect_size !== null ? (
            <Metric
              label="Effect size"
              value={decimal(result.effect_size, 4)}
              sub={result.effect_size_name ?? undefined}
              hint="How large the difference is, independent of sample size. A tiny effect can still be statistically significant in a large sample."
            />
          ) : null}
        </MetricGrid>
      )}

      <SectionNote>Method: {result.method}</SectionNote>

      {typeof result.details.ci_interpretation === "string" ? (
        <SectionNote>{result.details.ci_interpretation}</SectionNote>
      ) : null}

      {typeof result.details.n_a === "number" && typeof result.details.n_b === "number" ? (
        <div className="mt-3 grid grid-cols-2 gap-4 border-t border-border pt-3 text-xs">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">
              Group A — {String(result.details.group_a)}
            </div>
            <div className="tnum mt-0.5 text-ink">
              n={result.details.n_a} · median={decimal(result.details.median_a as number, 4)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">
              Group B — {String(result.details.group_b)}
            </div>
            <div className="tnum mt-0.5 text-ink">
              n={result.details.n_b} · median={decimal(result.details.median_b as number, 4)}
            </div>
          </div>
        </div>
      ) : null}

      <div className="mt-4">
        <WarningList warnings={result.warnings} />
      </div>
    </Panel>
  );
}
