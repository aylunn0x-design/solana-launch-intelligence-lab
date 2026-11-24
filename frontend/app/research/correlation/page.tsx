/**
 * Correlation analysis.
 *
 * Shows a pairwise matrix with FDR-adjusted p-values and a focused two-variable
 * result with a scatter plot. The adjusted p-values are shown next to the raw
 * ones because reading raw p-values off a 21-test matrix is a textbook
 * data-snooping error.
 */

import { Suspense } from "react";

import {
  api,
  type CorrelationMatrix,
  type ResearchSummary,
  type StatResult,
} from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import {
  Badge,
  EmptyState,
  PageHeader,
  Panel,
  SectionNote,
  Skeleton,
  WarningList,
} from "@/components/ui/primitives";
import { ControlBar, ParamSelect, VariableSelect } from "@/components/research/controls";
import { StatResultPanel } from "@/components/research/stat-result";
import { CorrelationHeatmap } from "@/components/charts";
import { DataTable } from "@/components/charts/chrome";
import { count, decimal, humanize, pValue } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Correlation" };

interface SearchParams {
  x?: string;
  y?: string;
  method?: string;
}

export default async function CorrelationPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const method = sp.method === "pearson" ? "pearson" : "spearman";

  let summary: ResearchSummary;
  let matrix: CorrelationMatrix;
  try {
    [summary, matrix] = await Promise.all([
      api.researchSummary(),
      api.correlationMatrix({ method }),
    ]);
  } catch (error) {
    return (
      <>
        <PageHeader title="Correlation analysis" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  const variables = summary.variables;
  const x =
    sp.x && variables.some((v) => v.name === sp.x)
      ? sp.x
      : (variables.find((v) => v.name === "initial_liquidity_usd")?.name ??
        variables[0]?.name);
  const y =
    sp.y && variables.some((v) => v.name === sp.y)
      ? sp.y
      : (variables.find((v) => v.name === "duration_hours")?.name ??
        variables[1]?.name ??
        variables[0]?.name);

  let focused: StatResult | null = null;
  if (x && y && x !== y) {
    focused = await api.correlation(x, y, { method }).catch(() => null);
  }

  const significant = matrix.pairs.filter((pair) => pair.significant_after_fdr);

  return (
    <>
      <Breadcrumbs
        items={[{ label: "Research Lab", href: "/research" }, { label: "Correlation" }]}
      />
      <PageHeader
        title="Correlation analysis"
        description="Pairwise association between dataset variables. Spearman by default: it measures monotonic association on ranks and is robust to the extreme outliers these distributions contain."
        actions={
          <Badge tone="neutral">
            {summary.dataset.label} · n={summary.dataset.n_pools}
          </Badge>
        }
      />

      <Suspense fallback={<Skeleton className="mb-5 h-16 w-full" />}>
        <ControlBar>
          <VariableSelect
            paramKey="x"
            label="Variable X"
            variables={variables.map((v) => ({
              name: v.name,
              label: humanize(v.name),
              availability: v.availability,
              nonNull: v.non_null_count,
            }))}
            defaultValue={x}
          />
          <VariableSelect
            paramKey="y"
            label="Variable Y"
            variables={variables.map((v) => ({
              name: v.name,
              label: humanize(v.name),
              availability: v.availability,
              nonNull: v.non_null_count,
            }))}
            defaultValue={y}
          />
          <ParamSelect
            paramKey="method"
            label="Method"
            defaultValue="spearman"
            options={[
              { value: "spearman", label: "Spearman (rank, robust)" },
              { value: "pearson", label: "Pearson (linear)" },
            ]}
          />
        </ControlBar>
      </Suspense>

      {focused ? (
        <div className="mb-5">
          <StatResultPanel
            result={focused}
            title={`${humanize(x ?? "")} vs ${humanize(y ?? "")}`}
            description="A single pair, reported in full."
          />
        </div>
      ) : x === y ? (
        <div className="mb-5">
          <Panel>
            <EmptyState
              title="Pick two different variables"
              description="A variable correlates perfectly with itself; choose a different Y."
            />
          </Panel>
        </div>
      ) : null}

      <div className="mb-5">
        <Panel
          title="Correlation matrix"
          description={`${matrix.method}. ${matrix.pairs.length} pairwise tests, p-values adjusted for the false discovery rate.`}
        >
          <CorrelationHeatmap
            columns={matrix.columns}
            matrix={matrix.matrix}
            pairwiseN={matrix.pairwise_n}
          />
          <div className="mt-4">
            <WarningList warnings={matrix.warnings} />
          </div>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title="Strongest associations"
          description="Ranked by absolute coefficient. Ranking by strength is exploratory — it is not a hypothesis test."
        >
          {matrix.pairs.length ? (
            <DataTable
              columns={["X", "Y", "coef", "n", "p", "p (FDR)", "Strength"]}
              rows={matrix.pairs
                .slice(0, 14)
                .map((pair) => [
                  humanize(pair.x),
                  humanize(pair.y),
                  decimal(pair.coefficient, 3),
                  count(pair.n),
                  pValue(pair.p_value),
                  pValue(pair.p_value_adjusted),
                  pair.interpretation,
                ])}
            />
          ) : (
            <EmptyState
              title="No pairs could be computed"
              description="Each pair needs at least 10 rows with both variables present."
            />
          )}
        </Panel>

        <Panel
          title="Surviving multiple-comparison correction"
          description="Pairs still significant at FDR < 0.05."
        >
          {significant.length ? (
            <DataTable
              columns={["X", "Y", "coef", "p (FDR)"]}
              rows={significant.map((pair) => [
                humanize(pair.x),
                humanize(pair.y),
                decimal(pair.coefficient, 3),
                pValue(pair.p_value_adjusted),
              ])}
            />
          ) : (
            <EmptyState
              title="None survive correction"
              description="No pair remains significant once the number of tests is accounted for. That is a result, not a gap: it means the apparent associations in this sample are consistent with chance."
            />
          )}
          <SectionNote>
            A surviving pair is still an association, not a cause. Both variables
            may be driven by a third — for example, pools with more liquidity also
            tend to be observed for longer, which inflates several of these
            relationships at once.
          </SectionNote>
        </Panel>
      </div>
    </>
  );
}
