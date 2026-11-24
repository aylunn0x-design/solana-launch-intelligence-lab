# Development Log

What was built, what broke, and what the data turned out to be like. Kept
because the surprises are the most useful part of the record.

---

## Phase 0 — Environment audit and data-source verification

**Environment:** Ubuntu 24.04, Python 3.13.18, Node 22.23.5, PostgreSQL 16
client *and server* binaries, Docker CLI **without a running daemon**. Empty
working directory, no existing project to preserve. No API keys present in the
environment.

**First finding: every market-data host was blocked.** Initial probes to
DexScreener, GeckoTerminal, CoinGecko and the Solana RPC all returned HTTP 000 —
the environment's egress proxy refused the tunnel. Package registries worked,
which is why dependency installation succeeded while data collection could not.
The environment's allow-list was empty; the four API hosts were added before any
code was written. **Verifying data access before designing around it was the
single highest-value step in the project.**

**Live verification results:**

| Probe | Result |
|---|---|
| `GET /networks/solana/new_pools` | 200 — 20 pools/page, with `pool_created_at`, `reserve_in_usd`, `volume_usd`, and `transactions` including `unique_traders` |
| `GET /pools/{addr}/ohlcv/hour?limit=1000` | 200 — **1000 bars**, ≈41 days of hourly history in one call |
| `GET /latest/dex/tokens/{mint}` (DexScreener) | 200 — 30 pairs, independent liquidity measure |
| `POST getVersion` (Solana RPC) | 200 — endpoint reachable |
| `POST getTokenLargestAccounts` | **429 on the first call** |

**Decisions that followed directly from the probes:**

1. GeckoTerminal is primary — it is the only source reporting *unique trader*
   counts, which is a genuinely better activity measure than transaction counts.
2. DexScreener is kept as an independent second measurement rather than a
   failover, so cross-source disagreement is detectable.
3. Holder concentration is **out of scope**. The 429 was not load-related, and
   the method caps at 20 accounts anyway, so a true holder count is not derivable
   from the public RPC at all.

**Documentation could not be verified.** The GeckoTerminal marketing site
returned 403 to our client and the DexScreener docs are a client-rendered
GitBook loading from a CDN host outside the allow-list. Rather than request more
hosts, rate limits were measured empirically and the limiter was designed not to
depend on knowing them. Terms of use remain unverified and are flagged as a
release blocker in TODO.md.

---

## Phase 1–2 — Foundation, schema, ingestion

Schema built around pools rather than tokens (reasoning in ARCHITECTURE.md
§2.1). First end-to-end run ingested 20 real pools with 0 rejections.

**Immediate discovery: pools from the new-pools feed are seconds old.** The
first test backfill returned 3–8 candles per pool, because the pools being
backfilled were the newest ones. Correct behaviour, useless data. The backfill
command gained `--max-age-days`, `--order` and `--only-missing` so the
rate-limit budget could be spent on pools that actually carry history.

**The feed is capped at page 10.** Page 11 returns HTTP 401 ("exceeds the
allowed max number of pages"). One full sweep therefore covers only the last
~6 minutes of launches. This is the single most consequential finding in the
project: **an unbiased inception cohort cannot be backfilled, only
accumulated**, which is why `scripts/collect.sh` and a 5-minute schedule exist,
and why the survivorship-bias machinery is built into the schema rather than
bolted on.

---

## Phase 3 — Rate limiting, learned the hard way

The adaptive limiter justified itself during the first bulk backfill: it
detected repeated 429s and automatically reduced its own rate 25 → 12 → 6
req/min, then kept succeeding.

**But it never recovered.** A burst of throttling early in a long run left
throughput crippled for the remainder. Recovery was added: 25 consecutive clean
requests let the rate creep back toward the configured value, capped so it
cannot stampede into the limit again. Both directions are unit-tested.

---

## Phase 4–5 — Features and the research engine

**A correctness bug caught while writing the outcome definition.** The first
implementation had:

```python
survived_24h = duration_hours >= 24
```

For a *censored* pool — still alive, only 10 hours old — this evaluates to
`False`. That is wrong: the pool has not failed to survive 24 hours, it simply
has not had the opportunity. Labelling it `False` would count every recent
launch as a failure and make the survival rate a function of when collection
started.

Replaced with three-valued logic (`survived_at`): true if it reached the
horizon, false if it died before it, **`None` if still alive and younger than
the horizon**. Undetermined pools are excluded from classification. This is the
kind of bug that produces a plausible-looking model and a wrong conclusion.

A validation test also caught a gap in the validator: nested volume windows were
only compared pairwise between *adjacent* windows, so with `h6` missing an
`h1 > h24` violation slipped through. Now every ordered pair is checked.

---

## Phase 6–7 — Frontend and integration

Nine pages built, then verified by rendering them headlessly and reading the
output. Four pages rendered ~500 characters — they were failing, not loading.
Two separate bugs:

1. **RSC boundary violation.** Formatter *functions* were being passed from
   server components into `"use client"` chart components. Functions are not
   serializable across that boundary. Fixed by passing a format *name*
   (`valueFormat="usd"`) resolved to a function on the client.
2. **The default dataset was the empty one.** `load_dataset` with no arguments
   returned the most recently *built* version, which happened to be
   `live-inception` with 0 pools (its pools are minutes old and have no hourly
   candles yet). The whole Research Lab looked broken while four usable datasets
   sat beside it. Fixed to prefer the most recent **non-empty** version, falling
   back to the newest so an genuinely empty database still shows its warnings.

React 19's stricter hooks lint then flagged three real `setState`-in-effect
patterns (cascading renders). Fixed properly — `useSyncExternalStore` for the
persisted sidebar preference, and the documented adjust-during-render pattern
for URL-derived state — rather than suppressed.

---

## What the data actually looks like

From the development dataset (404 pools, 5,233 candles, collected over ~1 hour):

- **Liquidity is extremely skewed.** Median initial liquidity $7,828 against a
  mean of $299,327; skewness 3.3. The mean describes essentially no real pool,
  which is why medians lead throughout the UI.
- **A real association, with real caveats.** Initial liquidity vs activity
  duration: Spearman ρ = 0.42, n = 50, p = 0.0023, 95% CI [0.163, 0.627] —
  moderate positive. Computed on a 100% survivorship-biased, 100% censored
  sample, so it is reported with five warnings attached and is not a finding
  about Solana launches.
- **Higher initial liquidity ↔ lower realized volatility** (ρ = −0.71) and
  **smaller drawdowns** (ρ = −0.56). Economically sensible and among the
  strongest relationships that survive FDR correction.
- **RQ6 answered, and the answer is a warning.** Event rate across the same
  pools: **12% at a 6h inactivity threshold, 4% at 12h, 0% at 24h and 48h.**
  The measured "death rate" is substantially an artefact of the definition.
- **A tautology the platform now detects.** `initial_liquidity_usd` and
  `peak_liquidity_usd` correlated at exactly 1.000 — because each pool had only
  one observation, so "peak" *was* "initial". Rather than let that sit in a
  heatmap looking like a finding, the platform detects the single-observation
  case and warns that liquidity-variability features are degenerate until
  repeated polling has happened.

---

## Verification performed

| Check | Result |
|---|---|
| Backend tests | 219 passing |
| Frontend tests | 25 passing |
| `ruff`, `eslint`, `tsc --noEmit` | clean |
| Next.js production build | succeeds, 13 routes |
| Migrations on SQLite | apply, reverse, re-apply; **no drift** |
| Migrations on **real PostgreSQL 16** | apply, reverse, re-apply; no drift; constraints, indexes and FKs verified present |
| Full pipeline on PostgreSQL | 80 pools, 575 candles, dataset built |
| All 9 pages rendered headlessly | HTTP 200, real content, **zero console errors** |
| Live ingestion | 424 pools, 5,233 candles, 0 rejected, 0 failed jobs |

**Not verified:** the Docker images and Compose stack. No Docker daemon was
available in the build environment, so they are written and reviewed but
unexecuted. The PostgreSQL path they depend on was verified directly.
