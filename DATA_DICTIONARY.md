# Data Dictionary

Every table and field, with its type, meaning and source.

**Conventions used throughout**

- `NULL` means *not observed*. It is never a stand-in for zero. A pool with
  `liquidity_usd = 0` has no liquidity; a pool with `liquidity_usd = NULL` was
  not measured. Conflating them would corrupt every statistic computed over the
  column.
- All timestamps are timezone-aware UTC.
- "Source" names where a value comes from: a provider field, a computation, or
  the application.

---

## Provenance tables

### `data_sources`

Registry of providers. Anchors provenance for every observation.

| Field | Type | Description | Source |
|---|---|---|---|
| `id` | int PK | | app |
| `name` | str(64), unique | `geckoterminal`, `dexscreener`, `solana_rpc` | app |
| `base_url` | str(255) | API base URL | app |
| `description` | text | What the source provides and its known limits | app |
| `requires_api_key` | bool | Whether a key is needed | app |
| `is_enabled` | bool | Operator switch; `solana_rpc` defaults to false | app |
| `created_at` | timestamptz | | app |

### `ingestion_jobs`

One row per pipeline run. Every write is attributable to a job.

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `job_type` | str(64), indexed | e.g. `discover_pools:new`, `backfill_ohlcv` |
| `status` | enum | `pending`, `running`, `succeeded`, `failed`, `partial` |
| `source_id` | int FK → `data_sources` | |
| `params` | JSON | Parameters the run was invoked with |
| `started_at` / `finished_at` | timestamptz | `finished_at` NULL while running |
| `records_requested` / `records_written` / `records_skipped` | int | Row accounting |
| `http_requests_made` | int | Requests issued, including retries |
| `rate_limit_hits` | int | HTTP 429 responses absorbed by backoff |
| `error_message` | text | Failure reason; NULL on success |
| `notes` | text | Validation flags raised during the run |

> `partial` means some pages or pools succeeded and others failed. It is a
> distinct state so a half-complete run is never reported as success.

### `raw_payloads`

Unmodified provider responses, kept so derived values can be recomputed.

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `job_id` | int FK → `ingestion_jobs` (SET NULL) | |
| `source_id` | int FK → `data_sources` | |
| `endpoint` | str(255) | Path requested |
| `request_params` | JSON | Query parameters |
| `http_status` | int | Response status |
| `payload` | JSON | The response body, untouched |
| `fetched_at` | timestamptz, indexed | |

---

## Core entity tables

### `tokens`

| Field | Type | Description | Source |
|---|---|---|---|
| `id` | int PK | | app |
| `mint` | str(64), unique, indexed | SPL mint address — the real identifier | provider |
| `symbol` | str(64), indexed | Display symbol; may be absent or misleading | provider |
| `name` | str(255), indexed | Display name | provider |
| `first_seen_at` | timestamptz | When *we* first saw it (≠ launch time) | app |
| `created_at` / `updated_at` | timestamptz | Row audit | app |

> Symbols are not unique and are attacker-controlled. All joins use `mint`.

### `pools`

An AMM pool. **The unit of a launch.**

| Field | Type | Description | Source |
|---|---|---|---|
| `id` | int PK | | app |
| `pool_address` | str(64), unique, indexed | Pool/pair address | provider |
| `token_id` | int FK → `tokens` (CASCADE), indexed | Base token | app |
| `network` | str(32) | `solana` | app |
| `dex` | str(64), indexed | Venue, e.g. `pumpswap`, `raydium`, `orca` | provider |
| `pool_name` | str(255) | e.g. `"TOKEN / SOL"` | provider |
| `quote_token_mint` | str(64) | Quote asset mint | provider |
| `pool_created_at` | timestamptz, indexed | **The launch clock.** NULL ⇒ excluded from cohort analysis | provider |
| `discovery_method` | enum, indexed | `new_pools_feed`, `top_pools`, `trending_pools`, `manual` | app |
| `discovered_at` | timestamptz | When ingestion first recorded it | app |
| `source_id` | int FK → `data_sources` | Which source found it | app |

> **`discovery_method` is permanent.** It describes the *sample*, not the pool.
> Only `new_pools_feed` is free of survivorship bias; the others appear in
> ranked listings precisely because they are currently notable.

### `market_observations`

A point-in-time snapshot. Repeated polling builds a longitudinal panel.

| Field | Type | Description | Source |
|---|---|---|---|
| `id` | int PK | | app |
| `pool_id` | int FK → `pools` (CASCADE), indexed | | app |
| `source_id` | int FK → `data_sources` | Which provider reported this | app |
| `job_id` | int FK → `ingestion_jobs` (SET NULL) | | app |
| `raw_payload_id` | int FK → `raw_payloads` (SET NULL) | Traceability to raw bytes | app |
| `observed_at` | timestamptz, indexed | Observation instant | app |
| `liquidity_usd` | float | Pool reserve in USD (`reserve_in_usd`) | provider |
| `fdv_usd` | float | Fully diluted valuation | provider |
| `market_cap_usd` | float | Often NULL for new tokens | provider |
| `price_usd` / `price_native` | float | Base token price | provider |
| `volume_usd_m5/h1/h6/h24` | float | Volume over trailing windows | provider |
| `txns_h1` / `txns_h24` | int | buys + sells over the window | computed |
| `buys_h24` / `sells_h24` | int | Directional counts | provider |
| `unique_traders_h24` | int | Distinct addresses. **GeckoTerminal only** | provider |
| `buyers_h24` / `sellers_h24` | int | Distinct addresses per side | provider |
| `price_change_pct_h1/h24` | float | Percentage points | provider |
| `missing_field_count` | int | Expected fields absent in this response | computed |

**Unique:** `(pool_id, source_id, observed_at)` — a source cannot report two
states for one pool at one instant.
**Check:** `liquidity_usd IS NULL OR liquidity_usd >= 0`.

> Rows are kept **per source** rather than merged, which is what makes
> cross-source disagreement measurable.

### `ohlcv_candles`

Historical price/volume bars — the retrospective history.

| Field | Type | Description | Source |
|---|---|---|---|
| `id` | int PK | | app |
| `pool_id` | int FK → `pools` (CASCADE), indexed | | app |
| `source_id` | int FK → `data_sources` | | app |
| `job_id` | int FK → `ingestion_jobs` (SET NULL) | | app |
| `timeframe` | enum | `minute`, `hour`, `day` | app |
| `ts` | timestamptz | Bar start | provider |
| `open` / `high` / `low` / `close` | float | Prices in USD | provider |
| `volume_usd` | float | Volume during the bar | provider |

**Unique:** `(pool_id, timeframe, ts)` — re-running a backfill cannot duplicate.
**Check:** `volume_usd IS NULL OR volume_usd >= 0`.

> `volume_usd = 0` is meaningful: an hour with no trading. The survival
> definition is built on exactly these zeros.

### `holder_snapshots`

**Not populated in the MVP.** See DATA_SOURCES.md §4.

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `token_id` | int FK → `tokens` (CASCADE), indexed | |
| `source_id` | int FK → `data_sources` | |
| `observed_at` | timestamptz | |
| `holder_count` | int | **Always NULL** from the public RPC: it returns at most 20 accounts, so reporting 20 would be fabricated |
| `top1_share` | float | Largest holder's share of supply, 0–1 |
| `top10_share` | float | Top ten holders' share, 0–1 |
| `herfindahl_index` | float | Sum of squared shares over *visible* accounts — partial by construction |
| `total_supply` | float | From `getTokenSupply` |

---

## Research tables

### `feature_definitions`

The documented, versioned feature registry — the anti-leakage contract.

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `name` | str(64), indexed | Feature name |
| `version` | int | Definition version |
| `dtype` | str(16) | `float`, `int`, `bool`, `categorical` |
| `unit` | str(32) | `USD`, `hours`, `%` |
| `description` | text | What it means and how it is computed |
| `availability` | enum, indexed | `at_launch`, `forward_looking`, `outcome` |
| `window` | str(32) | Time window, where applicable |

**Unique:** `(name, version)`.

### `dataset_versions`

Immutable analytical snapshots.

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `label` | str(64), unique | Human-chosen name; rebuilding under it is refused |
| `description` | text | |
| `criteria` | JSON | Inclusion/exclusion rules actually applied |
| `inactivity_threshold_hours` | int | The survival definition used |
| `pool_count` / `observation_count` / `candle_count` | int | Size |
| `inception_cohort_share` | float | Fraction from the unbiased cohort, 0–1 |
| `built_at` | timestamptz | |

### `pool_features`

Computed features for one pool within one dataset version.

| Field | Type | Availability | Description |
|---|---|---|---|
| `initial_liquidity_usd` | float | **at_launch** | Reserve at the earliest observation |
| `initial_fdv_usd` | float | **at_launch** | FDV at the earliest observation |
| `launch_hour_utc` | int | **at_launch** | 0–23 |
| `launch_dow` | int | **at_launch** | Monday = 0 |
| `dex` | str(64) | **at_launch** | Venue |
| `first_hour_volume_usd` | float | **at_launch** | First hourly candle at/after creation |
| `first_hour_unique_traders` | int | **at_launch** | NULL unless observed within the first hour |
| `first_hour_txns` | int | **at_launch** | Same caveat |
| `peak_liquidity_usd` | float | forward_looking | Max observed liquidity. **Equals initial when only one observation exists** |
| `liquidity_cv` | float | forward_looking | sd/mean of liquidity; NULL with < 2 observations |
| `total_volume_usd` | float | forward_looking | Sum of candle volume |
| `active_hours` | int | forward_looking | Candles with volume > 0 |
| `activity_persistence` | float | forward_looking | active_hours ÷ hours spanned, 0–1 |
| `realized_volatility` | float | forward_looking | sd of hourly log returns |
| `max_drawdown_pct` | float | forward_looking | Largest peak-to-trough decline, % |
| `duration_hours` | float | **outcome** | Survival time |
| `event_observed` | bool | **outcome** | True = went inactive; False = right-censored |
| `survived_24h` | bool | **outcome** | **Three-valued**: NULL when undetermined |
| `survived_72h` | bool | **outcome** | Same |
| `candle_count` / `observation_count` | int | metadata | How well observed |
| `completeness` | float | metadata | 1 − (missing fields ÷ expected), 0–1 |

**Unique:** `(dataset_version_id, pool_id)`.

> **The `survived_*` NULL case is load-bearing.** A pool still active but
> younger than the horizon has not failed to reach it. Labelling it `false`
> would count every recent launch as a failure.

### `research_experiments`

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `name` | str(255) | |
| `research_question` | text, required | What was asked |
| `hypothesis` | text | What was expected |
| `dataset_version_id` | int FK (SET NULL) | Which rows it ran on |
| `method` | str(64) | e.g. `spearman`, `coxph`, `logistic` |
| `features_used` | JSON | Inputs |
| `target_definition` | text | Exactly what the outcome means |
| `split_strategy` | str(64) | e.g. `time-aware 70/30` |
| `parameters` | JSON | Model/test parameters |
| `notes` / `limitations` | text | |
| `created_at` | timestamptz | |

### `experiment_results`

One row per metric, so results are queryable and comparable.

| Field | Type | Description |
|---|---|---|
| `id` | int PK | |
| `experiment_id` | int FK → `research_experiments` (CASCADE), indexed | |
| `metric_name` | str(64) | e.g. `roc_auc`, `rho`, `hazard_ratio` |
| `metric_value` | float | |
| `metric_text` | str | For non-numeric results |
| `sample_size` | int | n behind this metric |
| `ci_lower` / `ci_upper` | float | Confidence interval |
| `p_value` | float | |
| `split` | str(32) | `train`, `test`, `full` |
| `details` | JSON | Method-specific extras |

---

## Enumerations

| Enum | Values |
|---|---|
| `DiscoveryMethod` | `new_pools_feed` (unbiased), `top_pools`, `trending_pools`, `manual` |
| `JobStatus` | `pending`, `running`, `succeeded`, `failed`, `partial` |
| `FeatureAvailability` | `at_launch`, `forward_looking`, `outcome` |
| `Timeframe` | `minute`, `hour`, `day` |

> Enums are stored by **name** (e.g. `HOUR`), not value, because SQLAlchemy's
> non-native enum persists `.name`. Raw SQL must match that casing; ORM queries
> handle it automatically.
