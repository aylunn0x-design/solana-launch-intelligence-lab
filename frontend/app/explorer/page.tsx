/**
 * Token Explorer.
 *
 * A sortable, filterable, paginated table of pools (launches). Filtering and
 * sorting happen in SQL on the server, not in the browser: the dataset grows
 * without bound as ingestion runs, so paging through it client-side would not
 * scale and would make "total" a lie.
 */

import Link from "next/link";
import { Suspense } from "react";

import { api, type DexList, type TokenList } from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import { ExplorerFilters, Pagination } from "@/components/explorer/filters";
import {
  Address,
  Badge,
  Callout,
  EmptyState,
  PageHeader,
  Panel,
  Skeleton,
  Tooltip,
} from "@/components/ui/primitives";
import { count, duration, dateTime, usd } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Token Explorer" };

interface SearchParams {
  q?: string;
  dex?: string;
  cohort?: string;
  max_age_hours?: string;
  min_liquidity_usd?: string;
  has_history?: string;
  sort?: string;
  order?: string;
  page?: string;
}

export default async function ExplorerPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number.parseInt(sp.page ?? "1", 10) || 1);

  let data: TokenList;
  let dexes: DexList = { dexes: [] };
  try {
    [data, dexes] = await Promise.all([
      api.tokens({
        q: sp.q,
        dex: sp.dex,
        cohort: sp.cohort ?? "all",
        max_age_hours: sp.max_age_hours,
        min_liquidity_usd: sp.min_liquidity_usd,
        has_history: sp.has_history,
        sort: sp.sort ?? "launch",
        order: sp.order ?? "desc",
        page,
        page_size: 25,
      }),
      api.dexes().catch(() => ({ dexes: [] })),
    ]);
  } catch (error) {
    return (
      <>
        <PageHeader title="Token Explorer" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  const biasedCount = data.items.filter((item) => item.survivorship_biased).length;

  return (
    <>
      <PageHeader
        title="Token Explorer"
        description="Historical pool records. One row per pool, because a launch is the creation of a market — a token with several pools has several lifecycles."
      />

      <Suspense fallback={<Skeleton className="mb-4 h-8 w-full" />}>
        <ExplorerFilters dexes={dexes.dexes} />
      </Suspense>

      {biasedCount > 0 ? (
        <div className="mb-4">
          <Callout severity="medium" title="Sample composition">
            {biasedCount} of {data.items.length} pools on this page were discovered
            through ranked “top”/“trending” listings, which only contain pools that
            are currently notable. Filter to the inception cohort for a sample
            without survivorship bias.
          </Callout>
        </div>
      ) : null}

      <Panel bodyClassName="p-0">
        {data.items.length === 0 ? (
          <EmptyState
            title="No pools match these filters"
            description={
              sp.q
                ? `Nothing matched “${sp.q}”. The explorer only searches pools that have been ingested — it does not query the chain directly.`
                : "Try widening the filters, or run the ingestion pipeline to collect more pools."
            }
            action={
              <Link
                href="/explorer"
                className="rounded border border-border px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-border-strong hover:text-ink"
              >
                Clear filters
              </Link>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] border-collapse text-left text-xs">
              <caption className="sr-only">
                Pools matching the current filters, {data.pagination.total} in total.
              </caption>
              <thead>
                <tr className="border-b border-border text-[10px] uppercase tracking-wider text-ink-muted">
                  <th scope="col" className="px-3 py-2.5 font-semibold">Token</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">Venue</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold">
                    Liquidity
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold">
                    Volume 24h
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold">
                    Txns 24h
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold">
                    <span className="inline-flex items-center gap-1">
                      Traders
                      <Tooltip text="Unique trading addresses in the last 24 hours, as reported by GeckoTerminal. Blank where the source does not provide it." />
                    </span>
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold">Age</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">
                    <span className="inline-flex items-center gap-1">
                      Data
                      <Tooltip text="Observations stored and OHLCV candles available. A pool with no candles cannot enter survival analysis." />
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr
                    key={item.pool_address}
                    className="border-b border-border/50 transition-colors hover:bg-raised/40"
                  >
                    <td className="px-3 py-2.5">
                      <Link
                        href={`/explorer/${item.pool_address}`}
                        className="group inline-flex flex-col gap-0.5"
                      >
                        <span className="flex items-center gap-1.5">
                          <span className="font-medium text-ink group-hover:text-accent">
                            {item.symbol ?? "(no symbol)"}
                          </span>
                          {item.survivorship_biased ? (
                            <Badge
                              tone="warning"
                              title="Discovered via a ranked listing: survivorship-biased"
                            >
                              biased
                            </Badge>
                          ) : (
                            <Badge tone="good" title="Discovered at launch: unbiased">
                              inception
                            </Badge>
                          )}
                        </span>
                        <Address value={item.mint} />
                      </Link>
                    </td>
                    <td className="px-3 py-2.5 text-ink-secondary">{item.dex ?? "—"}</td>
                    <td className="tnum px-3 py-2.5 text-right text-ink">
                      {usd(item.latest?.liquidity_usd)}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-ink-secondary">
                      {usd(item.latest?.volume_usd_h24)}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-ink-secondary">
                      {count(item.latest?.txns_h24)}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-ink-secondary">
                      {count(item.latest?.unique_traders_h24)}
                    </td>
                    <td
                      className="tnum px-3 py-2.5 text-right text-ink-secondary"
                      title={dateTime(item.pool_created_at)}
                    >
                      {duration(item.age_hours)}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="tnum text-[11px] text-ink-muted">
                        {item.data_completeness.observations} obs ·{" "}
                        <span
                          className={
                            item.data_completeness.candles > 0 ? "" : "text-status-warning"
                          }
                        >
                          {item.data_completeness.candles} candles
                        </span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="px-4 pb-4">
          <Suspense fallback={null}>
            <Pagination
              page={data.pagination.page}
              totalPages={data.pagination.total_pages}
              total={data.pagination.total}
            />
          </Suspense>
        </div>
      </Panel>
    </>
  );
}
