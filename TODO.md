# TODO

Ordered by priority. Items marked **blocker** must be resolved before any
public release.

## Release blockers

- [ ] **Verify provider terms of use** (blocker). The GeckoTerminal and
      DexScreener terms could not be read from the build environment (403 and a
      client-rendered docs site). This project caches API responses in a
      database, which is ordinary research caching, but confirm attribution and
      redistribution requirements before publishing a derived dataset or
      deploying publicly. See DATA_SOURCES.md §6.
- [ ] **Run the Docker stack once** (blocker for the deployment claim). The
      images and Compose file are written but were never executed — no Docker
      daemon in the build environment. Run `docker compose up --build`, apply
      migrations, and confirm both health checks pass.
- [ ] **Choose a licence.** Default copyright currently applies, which makes the
      repository unusable by anyone else.
- [ ] Protect the ingestion endpoints if deployed publicly — they spend a shared
      rate-limit budget and are currently unauthenticated.

## P1 — Makes the research valid rather than illustrative

- [ ] **Run `scripts/collect.sh` on a 5-minute schedule for several weeks.**
      This is the single highest-value outstanding task: it is the only way to
      build an inception cohort large enough for unbiased survival analysis.
      Everything below is less important than this.
- [ ] Add a `refresh_pool_snapshots` schedule so pools gain multiple
      observations. Until then `liquidity_cv` is undefined and
      `peak_liquidity_usd` is degenerate (the platform warns, but the feature is
      unusable).
- [ ] Re-run RQ1 on the inception cohort once it is large enough, and compare
      against the retrospective result. A divergence means the retrospective
      finding was bias.
- [ ] Add minute-timeframe analysis for sub-hour lifecycles. The hourly grid
      plus the 6-candle minimum currently excludes the shortest-lived pools,
      which biases the sample toward survivors in a way more data will not fix.

## P2 — Research capability

- [ ] K-means and hierarchical clustering over launch-time features.
- [ ] PCA for exploratory dimensionality reduction.
- [ ] Persist model runs as experiments automatically, so `POST /api/research/model`
      results become comparable records rather than one-off responses.
- [ ] Experiment comparison UI (the API endpoint exists; no page uses it).
- [ ] Cross-source agreement reporting surfaced in the UI — `compare_sources()`
      exists and is tested but nothing calls it during ingestion yet.
- [ ] Model non-independence of pools sharing a token (currently a documented
      limitation only).

## P3 — Scope and scale

- [ ] A dedicated RPC provider (Helius/Triton/QuickNode) to unblock holder
      concentration and RQ2. **Needs an API key and possibly payment — a
      decision for the project owner, not something to assume.**
- [ ] Market-regime segmentation (RQ5), once the collection window spans months.
- [ ] A second chain for external validity.
- [ ] Replace the in-process rate limiter if ingestion ever runs on more than
      one worker.
- [ ] Replace FastAPI background tasks with a real queue if jobs must survive
      restarts (a job interrupted mid-run currently stays `RUNNING` forever).

## Known defects

- [ ] A job interrupted by a restart is never reclaimed from `RUNNING`. Add a
      startup sweep that marks stale running jobs as `failed`.
- [ ] The explorer's `min_liquidity_usd` filter matches a pool if *any*
      observation exceeded the threshold, not its latest. Defensible but
      surprising; should filter on the most recent observation.
- [ ] `docs/screenshots/` is referenced by the README but not populated.

## Done

- [x] Verify data-source availability by live request before designing around it
- [x] Database schema, migrations, verified on SQLite and PostgreSQL
- [x] Ingestion with validation, job tracking, idempotency, raw-payload retention
- [x] Adaptive rate limiting with backoff and recovery
- [x] Feature engineering with a structural anti-leakage gate
- [x] Three-valued survival labels (the censoring fix)
- [x] Descriptive statistics, correlation with FDR adjustment, Kaplan-Meier, Cox,
      baseline-vs-model comparison
- [x] RQ6 threshold-sensitivity analysis
- [x] Degenerate-feature detection for single-observation pools
- [x] 13 frontend routes with loading, empty and error states
- [x] 244 tests, lint and typecheck clean, CI workflow
- [x] Full documentation set
