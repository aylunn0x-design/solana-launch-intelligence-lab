/**
 * Token / pool detail.
 *
 * Shows what is actually stored for one pool: metadata, provenance, the
 * liquidity-and-activity panel built by repeated polling, and the OHLCV history
 * fetched from the provider. Missing data is stated as missing — there is no
 * interpolation and no carry-forward of stale values into a chart.
 *
 * Note on the two charts: price and volume are plotted separately rather than on
 * one pair of axes. A dual-axis chart would let the reader infer a relationship
 * from whatever scaling the axes happened to use.
 */

import Link from "next/link";
import { ExternalLink } from "lucide-react";

import { api, type TokenDetail, type TokenHistory } from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import {
  Address,
  Badge,
  Callout,
  EmptyState,
  KeyValue,
  Metric,
  MetricGrid,
  PageHeader,
  Panel,
  SectionNote,
} from "@/components/ui/primitives";
import { BarSeriesChart, TimeSeriesChart } from "@/components/charts";
import { ChartFrame, DataTable } from "@/components/charts/chrome";
import {
  count,
  dateTime,
  duration,
  percent,
  signedPercent,
  usd,
} from "@/lib/format";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ mint: string }>;
}) {
  const { mint } = await params;
  return { title: `${mint.slice(0, 8)}… · Token detail` };
}

export default async function TokenDetailPage({
  params,
}: {
  params: Promise<{ mint: string }>;
}) {
  const { mint } = await params;

  let detail: TokenDetail;
  let history: TokenHistory | null = null;
  try {
    detail = await api.tokenDetail(mint);
    history = await api.tokenHistory(mint, { timeframe: "hour", limit: 2000 });
  } catch (error) {
    return (
      <>
        <Breadcrumbs
          items={[{ label: "Explorer", href: "/explorer" }, { label: mint.slice(0, 10) }]}
        />
        <PageHeader title="Token detail" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  const { token, pool } = detail;

  // Price series from candle closes; liquidity from the observation panel.
  const priceSeries = (history?.ohlcv ?? [])
    .filter((candle) => candle.close !== null)
    .map((candle) => ({ t: new Date(candle.ts).getTime(), close: candle.close }));

  const volumeSeries = (history?.ohlcv ?? []).map((candle) => ({
    bucket: new Date(candle.ts).toLocaleString("en-GB", {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      timeZone: "UTC",
    }),
    volume: candle.volume_usd,
  }));

  const liquiditySeries = (history?.liquidity_activity_series ?? [])
    .filter((row) => row.liquidity_usd !== null)
    .map((row) => ({
      t: new Date(row.observed_at).getTime(),
      liquidity: row.liquidity_usd,
    }));

  const activeCandles = (history?.ohlcv ?? []).filter(
    (candle) => (candle.volume_usd ?? 0) > 0,
  ).length;

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Explorer", href: "/explorer" },
          { label: token.symbol ?? mint.slice(0, 10) },
        ]}
      />

      <PageHeader
        title={token.symbol ? `${token.symbol}` : "Unnamed token"}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{token.name ?? "No name reported by the source"}</span>
            <span className="text-ink-muted">·</span>
            <span>{pool.pool_name ?? "unnamed pool"}</span>
          </span>
        }
        actions={
          <>
            {pool.survivorship_biased ? (
              <Badge tone="warning" title="Discovered via a ranked listing">
                survivorship-biased
              </Badge>
            ) : (
              <Badge tone="good" title="Captured from the new-pools feed at launch">
                inception cohort
              </Badge>
            )}
            <a
              href={`https://dexscreener.com/solana/${pool.pool_address}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-ink-muted transition-colors hover:border-border-strong hover:text-ink-secondary"
            >
              DexScreener
              <ExternalLink size={10} aria-hidden="true" />
            </a>
          </>
        }
      />

      {detail.missing_data_warnings.length ? (
        <div className="mb-5">
          <Callout severity="medium" title="Missing data for this pool">
            <ul className="space-y-1.5">
              {detail.missing_data_warnings.map((warning) => (
                <li key={warning} className="flex gap-2">
                  <span aria-hidden="true" className="text-ink-muted">•</span>
                  <span>{warning}</span>
                </li>
              ))}
            </ul>
          </Callout>
        </div>
      ) : null}

      <div className="mb-5">
        <Panel title="Latest observation">
          {detail.latest ? (
            <>
              <MetricGrid>
                <Metric label="Liquidity" value={usd(detail.latest.liquidity_usd)} />
                <Metric label="Price" value={usd(detail.latest.price_usd)} />
                <Metric
                  label="24h change"
                  value={signedPercent(detail.latest.price_change_pct_h24)}
                  tone={
                    detail.latest.price_change_pct_h24 === null
                      ? "neutral"
                      : detail.latest.price_change_pct_h24 >= 0
                        ? "up"
                        : "down"
                  }
                />
                <Metric label="FDV" value={usd(detail.latest.fdv_usd)} />
                <Metric label="Volume 24h" value={usd(detail.latest.volume_usd_h24)} />
                <Metric label="Txns 24h" value={count(detail.latest.txns_h24)} />
                <Metric
                  label="Unique traders 24h"
                  value={count(detail.latest.unique_traders_h24)}
                  hint="Distinct trading addresses. Only GeckoTerminal reports this; blank means the source did not provide it."
                />
                <Metric
                  label="Pool age"
                  value={duration(pool.age_hours)}
                  sub={dateTime(pool.pool_created_at)}
                />
              </MetricGrid>
              <SectionNote>
                Observed {dateTime(detail.latest.observed_at)} ·{" "}
                {detail.latest.missing_field_count === 0
                  ? "all expected fields present"
                  : `${detail.latest.missing_field_count} expected field(s) missing from this observation`}
              </SectionNote>
            </>
          ) : (
            <EmptyState
              title="No observations stored"
              description="This pool exists in the database but no market snapshot has been recorded for it."
            />
          )}
        </Panel>
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Price history">
          <ChartFrame
            title="Close price per hour"
            subtitle={`${count(history?.counts.candles ?? 0)} hourly candles stored`}
            yLabel="USD"
            xLabel="Time (UTC)"
            height={260}
            tableView={
              priceSeries.length ? (
                <DataTable
                  columns={["Time (UTC)", "Close"]}
                  rows={(history?.ohlcv ?? [])
                    .slice(-200)
                    .reverse()
                    .map((candle) => [dateTime(candle.ts), usd(candle.close)])}
                />
              ) : undefined
            }
            footnote="Close price of each hourly bar, as reported by GeckoTerminal. Gaps are hours with no reported bar and are left blank rather than interpolated."
          >
            <TimeSeriesChart
              data={priceSeries}
              series={[{ key: "close", label: "Close price" }]}
              valueFormat="usd"
              height={260}
              emptyMessage="No price history stored for this pool. Run the OHLCV backfill job to populate it."
            />
          </ChartFrame>
        </Panel>

        <Panel title="Trading volume">
          <ChartFrame
            title="Volume per hour"
            subtitle={`${activeCandles} of ${count(history?.counts.candles ?? 0)} hours had non-zero volume`}
            yLabel="USD"
            xLabel="Hour (UTC)"
            height={260}
            tableView={
              volumeSeries.length ? (
                <DataTable
                  columns={["Hour (UTC)", "Volume"]}
                  rows={volumeSeries
                    .slice(-200)
                    .reverse()
                    .map((row) => [row.bucket, usd(row.volume)])}
                />
              ) : undefined
            }
            footnote="Hours with zero volume are the basis of the survival definition: a pool is treated as inactive once volume stays at zero for longer than the dataset's inactivity threshold."
          >
            <BarSeriesChart
              data={volumeSeries.slice(-120)}
              xKey="bucket"
              series={[{ key: "volume", label: "Volume (USD)" }]}
              valueFormat="usd"
              height={260}
              emptyMessage="No volume history stored for this pool."
            />
          </ChartFrame>
        </Panel>
      </div>

      <div className="mb-5">
        <Panel
          title="Liquidity panel"
          description="Built by repeated polling: each point is one stored observation, not an interpolation."
        >
          <ChartFrame
            title="Observed pool liquidity over time"
            subtitle={`${count(history?.counts.observations ?? 0)} observations`}
            yLabel="USD"
            xLabel="Observation time (UTC)"
            height={240}
            tableView={
              liquiditySeries.length ? (
                <DataTable
                  columns={["Observed at (UTC)", "Liquidity", "Volume 24h", "Txns 24h"]}
                  rows={(history?.liquidity_activity_series ?? [])
                    .slice()
                    .reverse()
                    .map((row) => [
                      dateTime(row.observed_at),
                      usd(row.liquidity_usd),
                      usd(row.volume_usd_h24),
                      count(row.txns_h24),
                    ])}
                />
              ) : undefined
            }
            footnote={
              liquiditySeries.length < 2
                ? "With fewer than two observations no liquidity trend can be read. The panel grows each time the refresh job runs."
                : "Liquidity is the pool reserve in USD at each observation."
            }
          >
            <TimeSeriesChart
              data={liquiditySeries}
              series={[{ key: "liquidity", label: "Liquidity (USD)" }]}
              valueFormat="usd"
              height={240}
              area
              emptyMessage="No liquidity observations stored for this pool."
            />
          </ChartFrame>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Metadata & lifecycle">
          <dl>
            <KeyValue label="Mint address">
              <Address value={token.mint} chars={10} />
            </KeyValue>
            <KeyValue label="Pool address">
              <Address value={pool.pool_address} chars={10} />
            </KeyValue>
            <KeyValue label="Quote token">
              {pool.quote_token_mint ? <Address value={pool.quote_token_mint} /> : "—"}
            </KeyValue>
            <KeyValue label="Venue (DEX)">{pool.dex ?? "—"}</KeyValue>
            <KeyValue label="Network">{pool.network}</KeyValue>
            <KeyValue
              label="Launch timestamp"
              hint="Pool creation time reported by the provider. This is the clock all lifecycle measurements start from."
            >
              {dateTime(pool.pool_created_at)}
            </KeyValue>
            <KeyValue label="First seen by us">{dateTime(token.first_seen_at)}</KeyValue>
            <KeyValue
              label="Discovery method"
              hint="How this pool entered the dataset. Permanent: it defines the pool's bias properties and is never overwritten."
            >
              {pool.discovery_method.replace(/_/g, " ")}
            </KeyValue>
            <KeyValue label="Observation window">
              {detail.observation_summary.count > 0
                ? `${dateTime(detail.observation_summary.first_observed_at)} → ${dateTime(
                    detail.observation_summary.last_observed_at,
                  )}`
                : "—"}
            </KeyValue>
            <KeyValue label="Candles by timeframe">
              {Object.keys(detail.candle_counts).length
                ? Object.entries(detail.candle_counts)
                    .map(([timeframe, n]) => `${timeframe.toLowerCase()}: ${n}`)
                    .join(" · ")
                : "none stored"}
            </KeyValue>
          </dl>

          {detail.other_pools_for_token.length ? (
            <div className="mt-4 border-t border-border pt-3">
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                Other pools for this token
              </h3>
              <ul className="space-y-1">
                {detail.other_pools_for_token.map((other) => (
                  <li key={other.pool_address}>
                    <Link
                      href={`/explorer/${other.pool_address}`}
                      className="text-xs text-accent transition-opacity hover:opacity-80"
                    >
                      {other.dex ?? "unknown venue"} · {dateTime(other.pool_created_at)}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Panel>

        <div className="space-y-5">
          <Panel
            title="Holder concentration"
            description="Top-holder share and concentration indices."
          >
            {detail.holder_data_available ? (
              <DataTable
                columns={["Observed", "Top 1", "Top 10", "HHI", "Holders"]}
                rows={detail.holder_snapshots.map((snapshot) => [
                  dateTime(snapshot.observed_at),
                  percent(snapshot.top1_share),
                  percent(snapshot.top10_share),
                  snapshot.herfindahl_index?.toFixed(4) ?? "—",
                  count(snapshot.holder_count),
                ])}
              />
            ) : (
              <EmptyState
                title="Not available"
                description={
                  <div className="space-y-2 text-left">
                    <p>
                      Holder-concentration data is not collected. The public Solana
                      RPC returns HTTP 429 for{" "}
                      <code className="rounded bg-panel px-1 text-[11px]">
                        getTokenLargestAccounts
                      </code>
                      , and that method caps at 20 accounts even when it succeeds, so
                      a true holder count cannot be derived from it.
                    </p>
                    <p className="text-ink-muted">
                      Enabling this requires a dedicated RPC provider with an API key.
                      The platform reports the gap rather than estimating the numbers.
                    </p>
                  </div>
                }
              />
            )}
          </Panel>

          <Panel title="Dataset provenance">
            <dl>
              <KeyValue label="Discovered via">
                {detail.provenance.discovered_via_source ?? "—"}
              </KeyValue>
              {detail.provenance.observation_sources.map((source) => (
                <KeyValue key={source.source} label={`Observations from ${source.source}`}>
                  {count(source.observations)}
                </KeyValue>
              ))}
            </dl>
            <SectionNote>{detail.provenance.note}</SectionNote>
          </Panel>
        </div>
      </div>
    </>
  );
}
