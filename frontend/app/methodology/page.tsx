/**
 * Methodology.
 *
 * The page that makes the rest of the platform legible. It is written in prose
 * because the decisions here are judgements, not configuration, and a reader
 * assessing the research needs the reasoning rather than a settings dump.
 *
 * Content is static by design: it documents decisions, not data. Anything that
 * varies with the data (sample sizes, cohort shares, thresholds) is shown on the
 * pages that compute it.
 */

import Link from "next/link";

import { Callout, PageHeader, Panel, SectionNote } from "@/components/ui/primitives";

export const metadata = { title: "Methodology" };

function H3({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-1.5 mt-5 text-[12px] font-semibold uppercase tracking-wider text-ink-secondary first:mt-0">
      {children}
    </h3>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="mb-3 text-[13px] leading-relaxed text-ink-secondary">{children}</p>;
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="tnum rounded bg-panel px-1 py-0.5 text-[11px] text-ink">{children}</code>
  );
}

export default function MethodologyPage() {
  return (
    <>
      <PageHeader
        title="Methodology"
        description="How the data is collected, what each measure means, and the specific ways these results could be wrong."
      />

      <div className="mb-5">
        <Callout severity="high" title="What this platform does and does not claim">
          Every result here is an <strong>association measured in observational
          data</strong>. There is no randomisation and no control over which tokens
          launch, so nothing on this platform can establish that one variable
          <em> causes</em> another. Historical relationships are not predictions, and
          none of this is financial advice or a trading system.
        </Callout>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="1. Research question">
          <P>
            Which observable characteristics of newly launched Solana tokens are
            associated with continued liquidity, market activity and survival, and
            how robust are those relationships across market conditions?
          </P>
          <H3>Sub-questions</H3>
          <ol className="mb-3 list-decimal space-y-1.5 pl-5 text-[13px] leading-relaxed text-ink-secondary">
            <li>Is initial liquidity associated with longer measurable activity?</li>
            <li>
              Is holder concentration associated with liquidity stability?{" "}
              <span className="text-status-serious">
                Out of scope in the MVP — see §7.
              </span>
            </li>
            <li>How does activity distribution relate to the token lifecycle?</li>
            <li>Do launch cohorts differ statistically from one another?</li>
            <li>Do relationships differ across market regimes?</li>
            <li>How much do results change when the definition of survival changes?</li>
            <li>How sensitive are results to missing or incomplete data?</li>
          </ol>
          <SectionNote>
            The hypotheses are not assumed to be true. A result showing no
            association is reported as a result.
          </SectionNote>
        </Panel>

        <Panel title="2. Data collection">
          <H3>Sources</H3>
          <P>
            Primary: the <strong>GeckoTerminal</strong> public API, which reports pool
            creation time, reserve in USD, per-window volume, transaction counts and
            unique trader counts, plus OHLCV history per pool. Secondary:{" "}
            <strong>DexScreener</strong>, used as an independent measurement of
            liquidity and volume for cross-source agreement checks. Neither requires
            an API key.
          </P>
          <H3>Unit of observation</H3>
          <P>
            A <strong>pool</strong>, not a token. A launch is the creation of an AMM
            market, and a token with several pools has several lifecycles with
            different liquidity and activity. Pool creation time is the clock every
            measurement starts from.
          </P>
          <H3>Two collection modes</H3>
          <P>
            The new-pools feed is polled on a schedule, which captures launches at
            birth. Historical candles are then fetched per pool, which reconstructs
            each pool&apos;s price and volume path since creation.
          </P>
          <SectionNote>
            Raw API responses are stored verbatim alongside the normalised rows, so
            any derived number can be recomputed from the original bytes.
          </SectionNote>
        </Panel>

        <Panel title="3. Inclusion and exclusion criteria">
          <P>A pool enters an analytical dataset only if:</P>
          <ul className="mb-3 list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-ink-secondary">
            <li>
              it has a <strong>pool creation timestamp</strong> — without it the pool
              cannot be placed on the launch clock;
            </li>
            <li>
              it has at least <Code>min_candles</Code> hourly candles (default 6) —
              below that the lifecycle is too thinly observed to measure.
            </li>
          </ul>
          <P>
            Excluded pools are dropped, never imputed. Each dataset version records
            the exact criteria applied, so the sample is auditable after the fact.
          </P>
          <H3>Validation before storage</H3>
          <P>
            Rows are rejected for impossible values (negative liquidity or volume, a
            creation time in the future or before Solana mainnet, an unparseable
            address) and flagged for suspicious ones (implausibly large liquidity,
            more unique traders than transactions, a longer volume window smaller
            than a shorter nested one). Flagged rows are stored with the concern
            recorded; they are never silently corrected.
          </P>
        </Panel>

        <Panel title="4. Feature definitions">
          <H3>Known at launch</H3>
          <P>
            <Code>initial_liquidity_usd</Code>, <Code>initial_fdv_usd</Code>,{" "}
            <Code>launch_hour_utc</Code>, <Code>launch_dow</Code>, <Code>dex</Code>,{" "}
            <Code>first_hour_volume_usd</Code>,{" "}
            <Code>first_hour_unique_traders</Code>, <Code>first_hour_txns</Code>.
            These are the only legal model inputs.
          </P>
          <H3>Forward-looking (descriptive only)</H3>
          <P>
            <Code>peak_liquidity_usd</Code>, <Code>liquidity_cv</Code>,{" "}
            <Code>total_volume_usd</Code>, <Code>active_hours</Code>,{" "}
            <Code>activity_persistence</Code>, <Code>realized_volatility</Code>,{" "}
            <Code>max_drawdown_pct</Code>.
          </P>
          <H3>Outcomes</H3>
          <P>
            <Code>duration_hours</Code>, <Code>event_observed</Code>,{" "}
            <Code>survived_24h</Code>, <Code>survived_72h</Code>.
          </P>
          <SectionNote>
            <strong>Caveat on first-hour features:</strong> the provider reports
            trader and transaction counts over a trailing 24-hour window, so these
            only represent launch activity when the pool was observed inside its
            first hour. Otherwise they are left null rather than approximated.
          </SectionNote>
        </Panel>

        <Panel title="5. Survival definition">
          <P>
            Survival is measured on <strong>trading activity</strong>, which is what
            on-chain data can actually observe:
          </P>
          <pre className="mb-3 overflow-x-auto rounded border border-border bg-panel px-3 py-2.5 text-[11px] leading-relaxed text-ink-secondary">
            <code>{`last_activity = latest hourly candle with volume > 0

if (now - last_activity) > inactivity_threshold:
    event observed (inactive)
    duration = last_activity - pool_created_at
else:
    right-censored (still active)
    duration = now - pool_created_at`}</code>
          </pre>
          <H3>Censoring, and the trap in it</H3>
          <P>
            Most pools are still active when we look, so they are{" "}
            <strong>right-censored</strong>: they contribute information up to the
            observation point and nothing after. The binary survival labels are
            three-valued for the same reason — a pool that is alive but younger than
            the horizon is recorded as <em>undetermined</em>, not as a failure.
            Labelling it <Code>false</Code> would count every recent launch as a
            failure and make any survival rate a function of when collection started.
          </P>
          <H3>The threshold is a parameter</H3>
          <P>
            The inactivity threshold is set per dataset version, not fixed in code,
            because it materially changes the measured outcome. The{" "}
            <Link href="/research/survival" className="text-accent hover:opacity-80">
              survival page
            </Link>{" "}
            compares versions built at different thresholds to show how much.
          </P>
        </Panel>

        <Panel title="6. Statistical methods">
          <H3>Descriptive</H3>
          <P>
            Medians and inter-quartile ranges lead; means are reported alongside but
            these distributions are strongly right-skewed, so the mean describes
            almost no actual pool. Skewness is shown so the reader can see why.
          </P>
          <H3>Association</H3>
          <P>
            <strong>Spearman rank correlation</strong> by default: it measures
            monotonic association on ranks and is robust to the extreme outliers
            present here. Pearson is available and carries a warning when the inputs
            are badly skewed. Confidence intervals use the Fisher z-transformation.
          </P>
          <H3>Group comparison</H3>
          <P>
            <strong>Mann-Whitney U</strong>, which assumes no particular
            distribution, with the rank-biserial correlation as the effect size and a
            percentile bootstrap interval (fixed seed) for the difference in medians.
          </P>
          <H3>Multiple comparisons</H3>
          <P>
            A correlation matrix over <em>k</em> variables runs <em>k(k−1)/2</em>{" "}
            tests, so some will look significant by chance. Benjamini-Hochberg
            FDR-adjusted p-values are reported next to the raw ones, and the adjusted
            values are the ones to judge by.
          </P>
          <H3>Survival</H3>
          <P>
            Kaplan-Meier for the curve, the log-rank test for group differences, and
            Cox proportional hazards for covariate effects. The proportional-hazards
            assumption is tested and the result reported, including when it fails.
          </P>
          <H3>Models</H3>
          <P>
            A majority-class baseline is mandatory and always shown. Logistic
            regression is the interpretable model; a shallow random forest is the
            comparison. Accuracy is never the headline metric for these imbalanced
            outcomes — ROC-AUC, average precision and balanced accuracy are reported,
            with accuracy shown beside the baseline&apos;s.
          </P>
        </Panel>

        <Panel title="7. Known limitations" className="lg:col-span-2">
          <H3>Survivorship bias in retrospective samples</H3>
          <P>
            The new-pools feed covers only the most recent few minutes of launches
            and cannot be paged back further, so an unbiased inception cohort can
            only be <em>accumulated</em> by polling over time — it cannot be
            backfilled. Pools discovered instead through ranked “top”/“trending”
            listings are present precisely because they are currently notable, so
            pools that launched and died are systematically missing from them.
            Survival rates computed over such a sample are{" "}
            <strong>biased upward</strong>. Every pool records how it was discovered,
            every result carries the cohort composition, and the discovery label is
            never overwritten.
          </P>
          <H3>Holder concentration is not available</H3>
          <P>
            The public Solana RPC returns HTTP 429 for{" "}
            <Code>getTokenLargestAccounts</Code> — on the first call, not under load.
            That method also caps at 20 accounts, so a true holder count or Gini
            coefficient cannot be derived from it even when it succeeds. Research
            question 2 is therefore <strong>out of scope in the MVP</strong> and is
            documented as blocked rather than quietly dropped. Enabling it requires a
            dedicated RPC provider with an API key.
          </P>
          <H3>Short observation window</H3>
          <P>
            A dataset collected over hours or days cannot speak to market regimes
            (RQ5). Results carry a warning when the launch dates span less than a
            week, and the chronological train/test split is flagged as not testing
            temporal generalisation when all launches fall within minutes of each
            other.
          </P>
          <H3>Heavy censoring</H3>
          <P>
            When most pools are still active, the survival curve is weakly identified
            beyond the observed range and its tail is extrapolation. With too few
            observed events the Cox model and the classifiers refuse to fit rather
            than return unstable coefficients.
          </P>
          <H3>Single-chain, single-provider measurement</H3>
          <P>
            Liquidity and volume are as reported by one primary provider; the
            secondary source is used to detect disagreement, not to correct it.
            Wash trading is not detectable from this data, so “activity” means
            reported activity.
          </P>
          <H3>Sub-hour lifecycles</H3>
          <P>
            Many launches on this chain live for minutes. The hourly candle grid
            cannot resolve them, so the dataset&apos;s minimum-candle rule excludes
            exactly the shortest-lived pools — a bias toward longer-lived tokens that
            no amount of data volume fixes.
          </P>
        </Panel>

        <Panel title="8. Anti-leakage controls" className="lg:col-span-2">
          <P>
            Each feature is declared in a registry as known-at-launch,
            forward-looking or an outcome. The research engine derives its list of
            legal model inputs from that registry, and passing anything else to a
            model or a Cox regression raises an error rather than producing a result.
            The rule is enforced in code and covered by tests, not left to reviewer
            discipline. Imputation and scaling happen inside the model pipeline so
            they are fitted on training data only — fitting an imputer on the whole
            dataset would leak test-set information through the imputed values. The
            train/test split is chronological, because a random split on time-ordered
            launches lets a model learn from launches that happened after the ones it
            is tested on.
          </P>
        </Panel>

        <Panel title="9. Reproducibility" className="lg:col-span-2">
          <P>
            Analytical datasets are <strong>immutable versions</strong>: rebuilding
            under an existing label is refused, because recomputing features in place
            would silently change the meaning of every experiment that referenced the
            label. Each version stores its inclusion criteria, its inactivity
            threshold and its cohort composition. Experiments reference a version by
            id, and the API refuses to record one pointing at a version that does not
            exist. Bootstrap intervals and model fits use fixed seeds, so re-running
            the same analysis on the same version reproduces the same numbers.
          </P>
          <SectionNote>
            Version history: v0.1 — initial methodology, MVP scope. Holder
            concentration deferred; market-regime analysis deferred pending a longer
            collection window.
          </SectionNote>
        </Panel>

        <Panel title="10. Research ethics" className="lg:col-span-2">
          <P>
            This project studies public on-chain market data in aggregate. It records
            no personal data: wallet addresses appear only as anonymous counts of
            unique traders, and no attempt is made to identify or profile individual
            participants. It is built for education, statistics and market-structure
            research. It deliberately contains no trading functionality, no buy or
            sell signals and no recommendations, and it must not be presented as
            investment advice.
          </P>
        </Panel>
      </div>
    </>
  );
}
